"""Tests for IDSA scraping helpers."""

import httpx
import pytest

from amfv_datasets.scraping.html import LinkMode
from amfv_datasets.scraping.idsa import (
    BASE_URL,
    LISTING_URL,
    IDSAFetchError,
    IDSAGuidelineRef,
    build_guideline_text,
    idsa_ref_from_url,
    list_practice_guidelines,
    scrape_guideline,
    scrape_idsa,
)


def test_list_practice_guidelines_parses_and_filters_default_statuses() -> None:
    """The IDSA listing includes only current non-development guidelines by default."""
    client = httpx.Client(transport=httpx.MockTransport(_listing_handler), base_url=BASE_URL)

    listing = list_practice_guidelines(client)

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


def test_list_practice_guidelines_can_include_archived_statuses() -> None:
    """Archived guidelines are included only when requested."""
    client = httpx.Client(transport=httpx.MockTransport(_listing_handler), base_url=BASE_URL)

    listing = list_practice_guidelines(client, include_archived=True)

    assert [ref.slug for ref in listing.refs] == [
        "current-guideline",
        "current-endorsed-guideline",
        "archived-guideline",
    ]


def test_list_practice_guidelines_can_include_in_development_statuses() -> None:
    """In-development guidelines are included only when requested."""
    client = httpx.Client(transport=httpx.MockTransport(_listing_handler), base_url=BASE_URL)

    listing = list_practice_guidelines(client, include_in_development=True)

    assert [ref.slug for ref in listing.refs] == [
        "current-guideline",
        "current-endorsed-guideline",
        "development-guideline",
    ]


def test_list_practice_guidelines_requires_both_flags_for_archived_development_statuses() -> None:
    """Guidelines marked both archived and in development require both inclusion flags."""
    client = httpx.Client(transport=httpx.MockTransport(_listing_handler), base_url=BASE_URL)

    listing = list_practice_guidelines(client, include_archived=True, include_in_development=True)

    assert [ref.slug for ref in listing.refs] == [
        "current-guideline",
        "current-endorsed-guideline",
        "archived-guideline",
        "development-guideline",
        "archived-development-guideline",
    ]


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


def test_build_guideline_text_extracts_content_and_link_metadata() -> None:
    """Guideline page content is converted to markdown and noisy UI is stripped."""
    client = httpx.Client(transport=httpx.MockTransport(_guideline_handler), base_url=BASE_URL)

    content, section_count, title, links_metadata = build_guideline_text(
        client,
        IDSAGuidelineRef(
            title="Current Guideline",
            slug="current-guideline",
            page_url="https://www.idsociety.org/practice-guideline/current-guideline/",
            year=2024,
            statuses=("Current",),
        ),
    )

    assert title == "Current Guideline"
    assert section_count == 3
    assert "# Current Guideline" in content
    assert "## Abstract" in content
    assert "## Recommendations" in content
    assert "Recommendation text with [evidence](https://doi.org/10.1093/cid/example)." in content
    assert "Back to top" not in content
    assert "Table of Contents" not in content
    assert "https://www.idsociety.org#abstract" not in content
    assert links_metadata == {
        "external_links": [
            "https://academic.oup.com/example.pdf",
            "https://doi.org/10.1093/cid/example",
        ],
        "pdf_links": ["https://academic.oup.com/example.pdf"],
    }

    stripped_content, _section_count, _title, _links_metadata = build_guideline_text(
        client,
        IDSAGuidelineRef(
            title="Current Guideline",
            slug="current-guideline",
            page_url="https://www.idsociety.org/practice-guideline/current-guideline/",
            year=2024,
            statuses=("Current",),
        ),
        link_mode=LinkMode.STRIP,
    )
    assert "Recommendation text with evidence." in stripped_content


def test_scrape_idsa_returns_normalized_document() -> None:
    """The IDSA scraper returns normalized scraped documents."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == LISTING_URL:
            return httpx.Response(200, text=_listing_html())
        if str(request.url) == "https://www.idsociety.org/practice-guideline/current-guideline/":
            return httpx.Response(200, text=_guideline_html())
        raise AssertionError(f"Unexpected URL: {request.url}")

    original_client_factory = "amfv_datasets.scraping.idsa.default_client"
    transport = httpx.MockTransport(handler)

    class ClientFactory:
        def __call__(self) -> httpx.Client:
            return httpx.Client(transport=transport)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(original_client_factory, ClientFactory())
        scrape_run = scrape_idsa(documents=1)
        documents = list(scrape_run.documents)

    assert scrape_run.total == 1
    assert len(documents) == 1
    assert documents[0].source == "idsa"
    assert documents[0].external_id == "idsa-current-guideline"
    assert documents[0].metadata["statuses"] == ["Current"]
    assert documents[0].metadata["year"] == 2024
    assert documents[0].metadata["pdf_links"] == ["https://academic.oup.com/example.pdf"]
    assert documents[0].metadata["content_length_chars"] == len(documents[0].content)
    assert documents[0].metadata["quality_flags"] == ["short_content"]


def test_scrape_guideline_does_not_flag_long_content() -> None:
    """Long guideline content records length without short-content quality flags."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://www.idsociety.org/practice-guideline/long-guideline/"
        return httpx.Response(200, text=_long_guideline_html())

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL)

    document = scrape_guideline(
        client,
        IDSAGuidelineRef(
            title="Long Guideline",
            slug="long-guideline",
            page_url="https://www.idsociety.org/practice-guideline/long-guideline/",
            year=2024,
            statuses=("Current",),
        ),
    )

    assert document.metadata["content_length_chars"] == len(document.content)
    assert document.metadata["quality_flags"] == []


def _listing_handler(request: httpx.Request) -> httpx.Response:
    assert str(request.url) == LISTING_URL
    return httpx.Response(200, text=_listing_html())


def _guideline_handler(request: httpx.Request) -> httpx.Response:
    assert str(request.url) == "https://www.idsociety.org/practice-guideline/current-guideline/"
    return httpx.Response(200, text=_guideline_html())


def _listing_html() -> str:
    return """
        <html>
          <div class="alpha-listing">
            <ul class="list-pages">
              <li>
                <ul class="list-pages__categories"><li class="category-dot category-dot-current">Current</li></ul>
                <a class="list-pages__link" href="/practice-guideline/current-guideline/">Current Guideline</a>
                <span class="list-pages__year">2024</span>
              </li>
              <li>
                <ul class="list-pages__categories">
                  <li class="category-dot category-dot-current">Current</li>
                  <li class="category-dot category-dot-endorsed">Endorsed</li>
                </ul>
                <a class="list-pages__link" href="/practice-guideline/current-endorsed-guideline/">
                  Current Endorsed Guideline
                </a>
                <span class="list-pages__year">2023</span>
              </li>
              <li>
                <ul class="list-pages__categories"><li class="category-dot category-dot-endorsed">Endorsed</li></ul>
                <a class="list-pages__link" href="/practice-guideline/endorsed-guideline/">Endorsed Guideline</a>
                <span class="list-pages__year">2022</span>
              </li>
              <li>
                <ul class="list-pages__categories"><li class="category-dot category-dot-archived">Archived</li></ul>
                <a class="list-pages__link" href="/practice-guideline/archived-guideline/">Archived Guideline</a>
                <span class="list-pages__year">2021</span>
              </li>
              <li>
                <ul class="list-pages__categories">
                  <li class="category-dot category-dot-in-development">In Development</li>
                </ul>
                <a class="list-pages__link" href="/practice-guideline/development-guideline/">
                  Development Guideline
                </a>
                <span class="list-pages__year">2020</span>
              </li>
              <li>
                <ul class="list-pages__categories">
                  <li class="category-dot category-dot-archived">Archived</li>
                  <li class="category-dot category-dot-in-development">In Development</li>
                </ul>
                <a class="list-pages__link" href="/practice-guideline/archived-development-guideline/">
                  Archived Development Guideline
                </a>
                <span class="list-pages__year">2019</span>
              </li>
            </ul>
          </div>
        </html>
    """


def _guideline_html() -> str:
    return """
        <html>
          <title>Current Guideline | IDSA</title>
          <div class="standardpage-col-left">
            <nav>Navigation noise</nav>
            <p>Intro column noise.</p>
          </div>
          <div class="standardpage-col-left">
            <p><a href="https://academic.oup.com/example.pdf">Download PDF</a></p>
            <h1>Current Guideline</h1>
            <h3>Table of Contents</h3>
            <ul>
              <li><a href="#abstract">Abstract</a></li>
              <li><a href="#recommendations">Recommendations</a></li>
            </ul>
            <p class="social">Share this page</p>
            <p>Published January 1, 2024</p>
            <h2>Abstract</h2>
            <p>Useful abstract.</p>
            <h2>Recommendations</h2>
            <p>Recommendation text with <a href="https://doi.org/10.1093/cid/example">evidence</a>.</p>
            <p>Back to top</p>
          </div>
        </html>
    """


def _long_guideline_html() -> str:
    long_text = "Recommendation text. " * 700
    return f"""
        <html>
          <title>Long Guideline | IDSA</title>
          <div class="standardpage-col-left">
            <h1>Long Guideline</h1>
            <h2>Recommendations</h2>
            <p>{long_text}</p>
          </div>
        </html>
    """
