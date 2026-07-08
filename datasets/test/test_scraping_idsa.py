"""Tests for IDSA scraping helpers."""

import httpx
import pytest

from amfv_datasets.scraping.html import LinkMode
from amfv_datasets.scraping.idsa import (
    BASE_URL,
    LISTING_URL,
    IDSAFetchError,
    IDSAGuidelineRef,
    idsa_ref_from_url,
    list_practice_guidelines,
    scrape_guideline,
    scrape_idsa,
)

_LISTING_CASES = (
    ("current-guideline", "Current Guideline", 2024, ("Current",)),
    ("current-endorsed-guideline", "Current Endorsed Guideline", 2023, ("Current", "Endorsed")),
    ("endorsed-guideline", "Endorsed Guideline", 2022, ("Endorsed",)),
    ("archived-guideline", "Archived Guideline", 2021, ("Archived",)),
    ("development-guideline", "Development Guideline", 2020, ("In Development",)),
    ("archived-development-guideline", "Archived Development Guideline", 2019, ("Archived", "In Development")),
)


def test_list_practice_guidelines_parses_listing_fields() -> None:
    """The IDSA listing parser normalizes title, URL, year, and statuses."""
    listing = list_practice_guidelines(_listing_client())

    assert listing.total == 2
    assert listing.refs == [
        IDSAGuidelineRef(
            title="Current Guideline",
            slug="current-guideline",
            page_url="https://www.idsociety.org/practice-guideline/current-guideline/",
            year=2024,
            statuses=("Current",),
        ),
        IDSAGuidelineRef(
            title="Current Endorsed Guideline",
            slug="current-endorsed-guideline",
            page_url="https://www.idsociety.org/practice-guideline/current-endorsed-guideline/",
            year=2023,
            statuses=("Current", "Endorsed"),
        ),
    ]


@pytest.mark.parametrize(
    ("kwargs", "expected_slugs"),
    [
        ({}, ["current-guideline", "current-endorsed-guideline"]),
        (
            {"include_archived": True},
            ["current-guideline", "current-endorsed-guideline", "archived-guideline"],
        ),
        (
            {"include_in_development": True},
            ["current-guideline", "current-endorsed-guideline", "development-guideline"],
        ),
        (
            {"include_archived": True, "include_in_development": True},
            [
                "current-guideline",
                "current-endorsed-guideline",
                "archived-guideline",
                "development-guideline",
                "archived-development-guideline",
            ],
        ),
    ],
    ids=["default", "include-archived", "include-in-development", "include-both"],
)
def test_list_practice_guidelines_filters_statuses(kwargs: dict[str, bool], expected_slugs: list[str]) -> None:
    """Status flags preserve the intended IDSA listing inclusion policy."""
    listing = list_practice_guidelines(_listing_client(), **kwargs)

    assert [ref.slug for ref in listing.refs] == expected_slugs


def test_idsa_ref_from_url_normalizes_practice_guideline_url() -> None:
    """IDSA practice guideline URLs are normalized to canonical refs."""
    assert idsa_ref_from_url("https://www.idsociety.org/practice-guideline/Current-Guideline/?utm=1") == (
        IDSAGuidelineRef(
            title="Current Guideline",
            slug="current-guideline",
            page_url="https://www.idsociety.org/practice-guideline/current-guideline/",
            year=None,
            statuses=(),
        )
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/practice-guideline/current-guideline/",
        "https://www.idsociety.org/news/current-guideline/",
    ],
    ids=["wrong-domain", "wrong-path"],
)
def test_idsa_ref_from_url_rejects_non_guideline_urls(url: str) -> None:
    """Only IDSA practice guideline URLs are accepted."""
    with pytest.raises(IDSAFetchError):
        idsa_ref_from_url(url)


def test_scrape_guideline_extracts_content_and_link_metadata() -> None:
    """Guideline page content is converted to markdown and noisy UI is stripped."""
    document = scrape_guideline(_guideline_client(_guideline_html()), _ref())

    assert document.title == "Current Guideline"
    assert document.section_count == 3
    assert "# Current Guideline" in document.content
    assert "## Abstract" in document.content
    assert "## Recommendations" in document.content
    assert "[Download PDF](https://academic.oup.com/example.pdf)" in document.content
    assert "Recommendation text with [evidence](https://doi.org/10.1093/cid/example)." in document.content
    assert "Back to top" not in document.content
    assert "Table of Contents" not in document.content
    assert "https://www.idsociety.org#abstract" not in document.content
    assert document.metadata["external_links"] == [
        "https://academic.oup.com/example.pdf",
        "https://doi.org/10.1093/cid/example",
    ]
    assert document.metadata["pdf_links"] == ["https://academic.oup.com/example.pdf"]
    assert document.metadata["content_length_chars"] == len(document.content)
    assert document.metadata["quality_flags"] == ["short_content"]


def test_scrape_guideline_strips_links_when_requested() -> None:
    """The IDSA scraper honors the shared link-mode option."""
    document = scrape_guideline(_guideline_client(_guideline_html()), _ref(), link_mode=LinkMode.STRIP)

    assert "Recommendation text with evidence." in document.content
    assert "[evidence]" not in document.content


def test_scrape_idsa_returns_normalized_document() -> None:
    """The IDSA scraper returns normalized scraped documents."""
    transport = httpx.MockTransport(_scrape_idsa_handler)

    class ClientFactory:
        def __call__(self) -> httpx.Client:
            return httpx.Client(transport=transport)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("amfv_datasets.scraping.idsa.default_client", ClientFactory())
        scrape_run = scrape_idsa(documents=1)
        documents = list(scrape_run.documents)

    assert scrape_run.total == 1
    assert len(documents) == 1
    assert documents[0].source == "idsa"
    assert documents[0].external_id == "idsa-current-guideline"
    assert documents[0].metadata["statuses"] == ["Current"]
    assert documents[0].metadata["year"] == 2024
    assert documents[0].metadata["slug"] == "current-guideline"
    assert documents[0].metadata["listing_url"] == LISTING_URL


@pytest.mark.parametrize(
    ("paragraph", "expected_flags"),
    [
        ("Useful abstract.", ["short_content"]),
        ("Recommendation text. " * 700, []),
    ],
    ids=["short", "long"],
)
def test_scrape_guideline_sets_content_quality_flags(paragraph: str, expected_flags: list[str]) -> None:
    """Short content is flagged while long content is left unflagged."""
    document = scrape_guideline(_guideline_client(_guideline_html(paragraph=paragraph)), _ref())

    assert document.metadata["content_length_chars"] == len(document.content)
    assert document.metadata["quality_flags"] == expected_flags


def _listing_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == LISTING_URL
        return httpx.Response(200, text=_listing_html())

    return httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL)


def _guideline_client(content_html: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://www.idsociety.org/practice-guideline/current-guideline/"
        return httpx.Response(200, text=content_html)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL)


def _scrape_idsa_handler(request: httpx.Request) -> httpx.Response:
    if str(request.url) == LISTING_URL:
        return httpx.Response(200, text=_listing_html())
    if str(request.url) == "https://www.idsociety.org/practice-guideline/current-guideline/":
        return httpx.Response(200, text=_guideline_html())
    raise AssertionError(f"Unexpected URL: {request.url}")


def _listing_html() -> str:
    items = "\n".join(
        _listing_item(slug=slug, title=title, year=year, statuses=statuses)
        for slug, title, year, statuses in _LISTING_CASES
    )
    return f'<html><div class="alpha-listing"><ul class="list-pages">{items}</ul></div></html>'


def _listing_item(*, slug: str, title: str, year: int, statuses: tuple[str, ...]) -> str:
    categories = "".join(f'<li class="category-dot">{status}</li>' for status in statuses)
    return f"""
        <li>
          <ul class="list-pages__categories">{categories}</ul>
          <a class="list-pages__link" href="/practice-guideline/{slug}/">{title}</a>
          <span class="list-pages__year">{year}</span>
        </li>
    """


def _guideline_html(*, title: str = "Current Guideline", paragraph: str = "Useful abstract.") -> str:
    return f"""
        <html>
          <title>{title} | IDSA</title>
          <div class="standardpage-col-left">
            <nav>Navigation noise</nav>
            <p>Intro column noise.</p>
          </div>
          <div class="standardpage-col-left">
            <p><a href="https://academic.oup.com/example.pdf">Download PDF</a></p>
            <h1>{title}</h1>
            <h3>Table of Contents</h3>
            <ul>
              <li><a href="#abstract">Abstract</a></li>
              <li><a href="#recommendations">Recommendations</a></li>
            </ul>
            <p class="social">Share this page</p>
            <p>Published January 1, 2024</p>
            <h2>Abstract</h2>
            <p>{paragraph}</p>
            <h2>Recommendations</h2>
            <p>Recommendation text with <a href="https://doi.org/10.1093/cid/example">evidence</a>.</p>
            <p>Back to top</p>
          </div>
        </html>
    """


def _ref() -> IDSAGuidelineRef:
    return IDSAGuidelineRef(
        title="Current Guideline",
        slug="current-guideline",
        page_url="https://www.idsociety.org/practice-guideline/current-guideline/",
        year=2024,
        statuses=("Current",),
    )
