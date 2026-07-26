"""Scrape WikiDoc medical articles into normalized markdown documents.

WikiDoc (wikidoc.org) is a MediaWiki instance, so discovery and extraction both
go through its `api.php` rather than the rendered pages. `list=allpages` is a
documented, stable contract for enumerating articles, and `action=parse` returns
the article body, revision id, categories, and section list in a single request.

Content is licensed CC BY-SA 3.0, reported by the API itself
(`action=query&meta=siteinfo&siprop=rightsinfo`). See LICENSE_NOTES.md.

Note the API does *not* remove WikiDoc's transcluded navigation templates
("WikiDoc Resources for X", "X Microchapters", "Resident Survival Guide"): those
tables are part of the article wikitext, so they are stripped here. They are
consistently marked `class="infobox"`, while real content tables are `wikitable`
or unclassed.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from lxml import html as lxml_html

from amfv_datasets.scraping.base import (
    ScrapedDocument,
    ScrapeError,
    ScrapeRun,
    default_client,
    scrape_listing_documents,
)
from amfv_datasets.scraping.html import LinkMode, html_to_markdown

BASE_URL = "https://www.wikidoc.org"
API_URL = f"{BASE_URL}/api.php"
ARTICLE_PATH = "/index.php"
WIKIDOC_DATASET_NAME = "wikidoc-webscrape"
WIKIDOC_DATASET_DISPLAY_NAME = "WikiDoc Webscrape"
DOCUMENT_DELAY_SECONDS = 5.0
LISTING_PAGE_SIZE = 500

logger = logging.getLogger(__name__)

_ARTICLE_PATH_RE = re.compile(r"^/index\.php/(?P<title>.+)$")
# Mainspace is polluted with user drafts and pseudo-titles: '"sandbox:A.R"',
# '"template:AM"', "SANDBOX:HT", "''Asparagaceae''". A leading quote character is
# a reliable marker — it means raw wikitext markup or literal quotes leaked into
# the title. Measured at ~1.5% of mainspace, concentrated alphabetically first.
_NON_ARTICLE_TITLE_RE = re.compile(r"""^["']|^"?(?:sandbox|template|user)\s*:""", re.IGNORECASE)
# WikiDoc marks its transcluded navigation templates with class="infobox";
# content tables are "wikitable" or unclassed.
_NAV_TABLE_XPATH = ".//table[contains(concat(' ', normalize-space(@class), ' '), ' infobox ')]"


class WikiDocFetchError(ScrapeError):
    """Raised when an article cannot be sourced from WikiDoc."""


@dataclass(frozen=True)
class WikiDocPageRef:
    """A WikiDoc mainspace article reference from the allpages listing."""

    title: str
    pageid: int | None = None

    @property
    def slug(self) -> str:
        """Return the underscore-separated title used in WikiDoc article URLs."""
        return self.title.replace(" ", "_")

    @property
    def page_url(self) -> str:
        """Return the canonical article URL."""
        return f"{BASE_URL}{ARTICLE_PATH}/{self.slug}"


def wikidoc_ref_from_url(url: str) -> WikiDocPageRef:
    """Parse a WikiDoc article URL into a page reference.

    Args:
        url: WikiDoc article URL to parse.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "www.wikidoc.org",
        "wikidoc.org",
    }:
        raise WikiDocFetchError(f"Enter a WikiDoc article URL from wikidoc.org; got {url!r}")
    match = _ARTICLE_PATH_RE.match(parsed.path)
    if not match:
        raise WikiDocFetchError(f"Enter a URL like {BASE_URL}/index.php/Hypertension; got {url!r}")
    return WikiDocPageRef(title=unquote(match.group("title")).replace("_", " "))


def _api_get(client: httpx.Client, params: dict[str, Any]) -> dict[str, Any]:
    """Call the MediaWiki API and return the decoded JSON body."""
    response = client.get(API_URL, params={**params, "format": "json"})
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise WikiDocFetchError(f"WikiDoc API error: {payload['error'].get('info', payload['error'])}")
    return payload


def _is_article_title(title: str) -> bool:
    return not _NON_ARTICLE_TITLE_RE.match(title)


def list_wikidoc_articles(
    client: httpx.Client, apcontinue: str | None = None
) -> tuple[list[WikiDocPageRef], str | None]:
    """Return one batch of mainspace article refs and the next continue token.

    Args:
        client: HTTP client used to call the WikiDoc API.
        apcontinue: Continue token from the previous batch (default: None).
    """
    params: dict[str, Any] = {
        "action": "query",
        "list": "allpages",
        "apnamespace": 0,
        "apfilterredir": "nonredirects",
        "aplimit": LISTING_PAGE_SIZE,
    }
    if apcontinue:
        params["apcontinue"] = apcontinue
    payload = _api_get(client, params)
    refs = [
        WikiDocPageRef(title=page["title"], pageid=page.get("pageid"))
        for page in payload.get("query", {}).get("allpages", [])
        if _is_article_title(page["title"])
    ]
    return refs, payload.get("continue", {}).get("apcontinue")


def _strip_nav_tables(article_html: str) -> str:
    """Remove WikiDoc's transcluded navigation templates."""
    root = lxml_html.fragment_fromstring(article_html, create_parent="div")
    for table in root.xpath(_NAV_TABLE_XPATH):
        table.getparent().remove(table)
    return lxml_html.tostring(root, encoding="unicode")


def build_wikidoc_article_text(
    client: httpx.Client,
    ref: WikiDocPageRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> tuple[str, int, str, dict[str, Any]]:
    """Scrape one article into markdown text, section count, title, and metadata.

    Args:
        client: HTTP client used to call the WikiDoc API.
        ref: WikiDoc page reference to scrape.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
    """
    payload = _api_get(
        client,
        {
            "action": "parse",
            "page": ref.title,
            "prop": "text|revid|categories|sections",
            # Drops the "[edit | edit source]" markers MediaWiki injects after
            # every heading; cheaper and more reliable than stripping them here.
            "disableeditsection": 1,
        },
    )
    parse = payload.get("parse")
    if not parse:
        raise WikiDocFetchError(f"No parse result for article {ref.title!r}")

    article_html = _strip_nav_tables(parse["text"]["*"])
    content = html_to_markdown(article_html, link_mode=link_mode, base_url=BASE_URL)
    if not content:
        raise WikiDocFetchError(f"No readable content for article {ref.title!r}")

    sections = parse.get("sections", [])
    metadata = {
        "pageid": parse.get("pageid", ref.pageid),
        "revid": parse.get("revid"),
        "categories": [category["*"] for category in parse.get("categories", [])],
        # Many WikiDoc topics are "microchapter" hubs whose body is only links to
        # sub-pages; record the length so a corpus build can filter them out.
        "content_length_chars": len(content),
        "license": "CC BY-SA 3.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/3.0/",
    }
    return content, max(len(sections), 1), parse.get("title", ref.title), metadata


def scrape_wikidoc_article(
    client: httpx.Client,
    ref: WikiDocPageRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> ScrapedDocument:
    """Scrape a WikiDoc article into a normalized document.

    Args:
        client: HTTP client used to call the WikiDoc API.
        ref: WikiDoc page reference to scrape.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
    """
    content, section_count, title, metadata = build_wikidoc_article_text(client, ref, link_mode=link_mode)
    return ScrapedDocument(
        source="wikidoc",
        external_id=f"wikidoc-{ref.slug}",
        title=title,
        url=ref.page_url,
        content=content,
        section_count=section_count,
        metadata=metadata,
    )


def _scrape_or_skip(
    client: httpx.Client,
    ref: WikiDocPageRef,
    *,
    link_mode: LinkMode,
) -> ScrapedDocument | None:
    """Scrape one article, skipping it if WikiDoc cannot serve it.

    A handful of WikiDoc pages are corrupt — `Hypertelorism` reports "There is no
    revision with ID 388595" — and a corpus run over 141k articles should not die
    on one of them.

    Args:
        client: HTTP client used to call the WikiDoc API.
        ref: WikiDoc page reference to scrape.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text.
    """
    try:
        return scrape_wikidoc_article(client, ref, link_mode=link_mode)
    except WikiDocFetchError:
        logger.warning("Skipping WikiDoc article %r", ref.title, exc_info=True)
        return None


def _article_count(client: httpx.Client) -> int | None:
    """Return WikiDoc's self-reported mainspace article count."""
    payload = _api_get(client, {"action": "query", "meta": "siteinfo", "siprop": "statistics"})
    articles = payload.get("query", {}).get("statistics", {}).get("articles")
    return articles if isinstance(articles, int) else None


def scrape_wikidoc(
    *,
    documents: int | None,
    link_mode: LinkMode = LinkMode.KEEP,
    url: str | None = None,
) -> ScrapeRun:
    """Scrape WikiDoc documents from a URL or the mainspace article listing.

    Args:
        documents: Number of documents to scrape. Ignored when `url` is set.
            When unset, the listing is walked until it is exhausted
            (default: None).
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
        url: WikiDoc article URL to scrape as a single document (default: None).
    """
    if url is not None:

        def scrape_url() -> Iterable[ScrapedDocument]:
            with default_client() as client:
                yield scrape_wikidoc_article(client, wikidoc_ref_from_url(url), link_mode=link_mode)

        return ScrapeRun(documents=scrape_url(), total=1)

    # ponytail: MediaWiki paginates by opaque token, but scrape_listing_documents
    # hands list_page an integer page number. Keep the token in a closure and
    # ignore the page argument. Swap for a token-aware base contract if a second
    # token-paginated source ever lands.
    token: str | None = None
    exhausted = False

    def list_page(client: httpx.Client, _page: int) -> list[WikiDocPageRef]:
        nonlocal token, exhausted
        if exhausted:
            return []
        refs, token = list_wikidoc_articles(client, token)
        if token is None:
            exhausted = True
        return refs

    with default_client() as client:
        article_count = _article_count(client)
    total = article_count if documents is None else min(documents, article_count or documents)

    return ScrapeRun(
        total=total,
        documents=scrape_listing_documents(
            documents=documents,
            client_factory=default_client,
            list_page=list_page,
            scrape_item=lambda client, ref: _scrape_or_skip(client, ref, link_mode=link_mode),
            document_delay_seconds=DOCUMENT_DELAY_SECONDS,
        ),
    )


__all__ = [
    "API_URL",
    "BASE_URL",
    "DOCUMENT_DELAY_SECONDS",
    "WIKIDOC_DATASET_DISPLAY_NAME",
    "WIKIDOC_DATASET_NAME",
    "WikiDocFetchError",
    "WikiDocPageRef",
    "build_wikidoc_article_text",
    "list_wikidoc_articles",
    "scrape_wikidoc",
    "scrape_wikidoc_article",
    "wikidoc_ref_from_url",
]
