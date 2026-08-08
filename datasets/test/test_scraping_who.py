"""Tests for WHO scraping helpers."""

import json
from pathlib import Path

import httpx
import pytest

from amfv_datasets.scraping.html import LinkMode
from amfv_datasets.scraping.who import (
    BASE_URL,
    WHO_LICENSE,
    WhoFetchError,
    WhoListingPage,
    WhoPublicationRef,
    build_publication_text,
    list_publications,
    publication_ref_from_url,
    scrape_publication,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_CVC_GUIDELINE_TITLE = (
    "Guidelines for the prevention of bloodstream infections and other infections "
    "associated with the use of intravascular catheters: part 2: central venous catheters"
)
_CERVICAL_GUIDELINE_TITLE = (
    "WHO guideline for screening and treatment of cervical pre-cancer lesions for cervical cancer prevention"
)


def test_list_publications_parses_api_listing() -> None:
    """WHO listing payloads are parsed from the publications OData API."""
    payload = json.loads((_FIXTURES / "who_listing_api.json").read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/hubs/publications"
        assert request.url.params["$filter"] == "publishingoffices/any(s:s eq c09761c0-ab8e-4cfa-9744-99509c4d306b)"
        assert request.url.params["$skip"] == "0"
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL)

    listing_page = list_publications(client)

    assert listing_page == WhoListingPage(
        total=356,
        refs=[
            WhoPublicationRef(
                publication_id="9789240121805",
                title=_CVC_GUIDELINE_TITLE,
                page_url="https://www.who.int/publications/i/item/9789240121805",
                publication_date="28 May 2026",
                tag="Guideline",
                download_url="https://iris.who.int/server/api/core/bitstreams/f750f24d-c0c2-425c-85fd-ec310d2ce994/content",
            ),
            WhoPublicationRef(
                publication_id="9789240121744",
                title=_CERVICAL_GUIDELINE_TITLE,
                page_url="https://www.who.int/publications/i/item/9789240121744",
                publication_date="8 May 2026",
                tag="Guideline",
                download_url="https://iris.who.int/server/api/core/bitstreams/32214b73-0e95-4243-9e83-617948510dcd/content",
            ),
        ],
    )


def test_publication_ref_from_url_normalizes_publication_url() -> None:
    """WHO publication URLs are normalized to canonical refs."""
    assert publication_ref_from_url("https://www.who.int/publications/i/item/9789240121805") == WhoPublicationRef(
        publication_id="9789240121805",
        title="9789240121805",
        page_url="https://www.who.int/publications/i/item/9789240121805",
    )


def test_build_publication_text_scrapes_overview() -> None:
    """Publication pages are converted into Overview markdown."""
    html = (_FIXTURES / "who_publication_overview.html").read_text(encoding="utf-8")
    page_url = "https://www.who.int/publications/i/item/9789240121805"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == page_url
        return httpx.Response(200, text=html)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ref = WhoPublicationRef(
        publication_id="9789240121805",
        title=_CVC_GUIDELINE_TITLE,
        page_url=page_url,
        publication_date="28 May 2026",
        tag="Guideline",
        download_url="https://iris.who.int/server/api/core/bitstreams/f750f24d-c0c2-425c-85fd-ec310d2ce994/content",
    )

    content, section_count, title, metadata = build_publication_text(client, ref)

    assert title.startswith("Guidelines for the prevention of bloodstream infections")
    assert section_count == 1
    assert "### Overview" in content
    assert "central venous catheters (CVCs)" in content
    assert "WHO Team" not in content
    assert metadata["publication_date"] == "28 May 2026"
    assert metadata["tag"] == "Guideline"
    assert metadata["isbn"] == "978-92-4-012180-5"
    assert metadata["content_scope"] == "overview"
    assert metadata["license"] == WHO_LICENSE
    assert metadata["listing_category"] == "who-guidelines"


def test_scrape_publication_builds_scraped_document() -> None:
    """A WHO publication ref is normalized into a ScrapedDocument."""
    html = (_FIXTURES / "who_publication_overview.html").read_text(encoding="utf-8")
    page_url = "https://www.who.int/publications/i/item/9789240121805"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ref = WhoPublicationRef(
        publication_id="9789240121805",
        title=_CVC_GUIDELINE_TITLE,
        page_url=page_url,
    )

    document = scrape_publication(client, ref, link_mode=LinkMode.STRIP)

    assert document.source == "who"
    assert document.external_id == "who-9789240121805"
    assert document.url == page_url
    assert document.section_count == 1
    assert document.content.strip()
    assert document.metadata["publication_id"] == "9789240121805"
    assert document.metadata["content_scope"] == "overview"


def test_build_publication_text_raises_when_overview_missing() -> None:
    """Missing publication markup raises WhoFetchError."""
    html = "<html><body><p>No publication section here.</p></body></html>"
    page_url = "https://www.who.int/publications/i/item/9789240121805"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ref = WhoPublicationRef(
        publication_id="9789240121805",
        title="Example guideline",
        page_url=page_url,
    )

    with pytest.raises(WhoFetchError, match="No publication content section"):
        build_publication_text(client, ref)
