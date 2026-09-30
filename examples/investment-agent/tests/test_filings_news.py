"""Tests for SEC EDGAR and news tools with HTTP and Tavily mocked out."""

from __future__ import annotations

import json
import sys
import types

import httpx
import pytest

from investment_agent.tools import filings, news

TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire Hathaway"},
}
SUBMISSIONS = {
    "filings": {
        "recent": {
            "form": ["8-K", "10-Q", "4", "10-K", "8-K"],
            "accessionNumber": [
                "0000320193-26-000010",
                "0000320193-26-000008",
                "x",
                "0000320193-25-000079",
                "0000320193-25-000070",
            ],
            "filingDate": [
                "2026-05-01",
                "2026-04-30",
                "2026-04-02",
                "2025-10-31",
                "2025-10-30",
            ],
            "reportDate": ["2026-05-01", "2026-03-28", "", "2025-09-27", ""],
            "primaryDocument": ["a8k.htm", "a10q.htm", "f4.xml", "a10k.htm", "b8k.htm"],
            "primaryDocDescription": ["8-K", "10-Q", "4", "10-K", "8-K"],
        }
    }
}
FILING_HTML = """<html><head><style>.x{color:red}</style></head><body>
<div>Table of Contents</div><p>Item 1A. Risk Factors 12</p><p>Item 1B. Unresolved Staff Comments 20</p>
<p>Item 1A. Risk Factors</p><p>Our business depends on &amp; is exposed to supply-chain disruption.</p>
<p>Competition is intense&nbsp;and growing.</p>
<p>Item 1B. Unresolved Staff Comments</p><p>None.</p></body></html>"""


def _response(
    url: str, *, json_body: object | None = None, text: str = ""
) -> httpx.Response:
    request = httpx.Request("GET", url)
    if json_body is not None:
        return httpx.Response(200, json=json_body, request=request)
    return httpx.Response(200, text=text, request=request)


@pytest.fixture
def edgar(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        calls.append(url)
        assert "User-Agent" in kwargs["headers"]  # type: ignore[operator]
        if url == filings.TICKERS_URL:
            return _response(url, json_body=TICKERS)
        if "submissions" in url:
            return _response(url, json_body=SUBMISSIONS)
        return _response(url, text=FILING_HTML)

    monkeypatch.setattr(filings.httpx, "get", fake_get)
    return calls


def _call(tool: object, **kwargs: object) -> dict:
    result = tool.invoke(kwargs)  # type: ignore[attr-defined]
    json.dumps(result)
    return result


def test_search_filings_default_forms(edgar: list[str]) -> None:
    r = _call(filings.search_filings, ticker="aapl")
    assert r["cik"] == 320193 and r["company"] == "Apple Inc."
    assert [f["form"] for f in r["filings"]] == ["8-K", "10-Q", "10-K", "8-K"]
    assert (
        r["filings"][0]["url"]
        == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/a8k.htm"
    )
    assert r["filings"][3]["report_date"] is None
    assert "CIK0000320193" in edgar[1]


def test_search_filings_form_filter_and_limit(edgar: list[str]) -> None:
    r = _call(filings.search_filings, ticker="AAPL", form_types=["10-k"], limit=1)
    assert [f["form"] for f in r["filings"]] == ["10-K"]


def test_search_filings_caches_ticker_map(edgar: list[str]) -> None:
    _call(filings.search_filings, ticker="AAPL")
    _call(filings.search_filings, ticker="AAPL")
    assert edgar.count(filings.TICKERS_URL) == 1


def test_lookup_cik_share_class(edgar: list[str]) -> None:
    assert filings.lookup_cik("brk.b") == (1067983, "Berkshire Hathaway")


def test_search_filings_unknown_ticker(edgar: list[str]) -> None:
    assert (
        "not found in SEC EDGAR"
        in _call(filings.search_filings, ticker="ZZZZ")["error"]
    )


def test_search_filings_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(url: str, **kwargs: object) -> httpx.Response:
        return httpx.Response(403, request=httpx.Request("GET", url))

    monkeypatch.setattr(filings.httpx, "get", fail)
    assert "HTTPStatusError" in _call(filings.search_filings, ticker="AAPL")["error"]


def test_fetch_filing_section_picks_body_not_toc(edgar: list[str]) -> None:
    url = "https://www.sec.gov/Archives/edgar/data/320193/x/a10k.htm"
    r = _call(filings.fetch_filing_section, url=url, section="Item 1A")
    assert r["text"].startswith(
        filings.TEXT_BEGIN
        + "\nItem 1A. Risk Factors\nOur business depends on & is exposed"
    )
    assert r["text"].endswith(filings.TEXT_END)
    assert r["note"] == filings.UNTRUSTED_NOTE
    assert "Competition is intense and growing." in r["text"]
    assert "Unresolved" not in r["text"]
    assert r["truncated"] is False


def test_fetch_filing_section_full_text_strips_styles(edgar: list[str]) -> None:
    r = _call(filings.fetch_filing_section, url="https://www.sec.gov/doc.htm")
    assert r["truncated"] is False and r["total_chars"] < len(r["text"])
    assert ".x{color" not in r["text"] and "<p>" not in r["text"]


def test_fetch_filing_section_truncates_with_clamped_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_doc = "<p>" + "word " * 2000 + "</p>"
    monkeypatch.setattr(
        filings.httpx, "get", lambda url, **kw: _response(url, text=long_doc)
    )
    r = _call(
        filings.fetch_filing_section, url="https://www.sec.gov/doc.htm", max_chars=10
    )
    assert r["truncated"] is True
    body = (
        r["text"]
        .removeprefix(filings.TEXT_BEGIN + "\n")
        .removesuffix("\n" + filings.TEXT_END)
    )
    assert len(body) == 1000  # max_chars is clamped to at least 1000
    assert r["total_chars"] > 1000


def test_fetch_filing_section_missing(edgar: list[str]) -> None:
    assert (
        "not found"
        in _call(
            filings.fetch_filing_section,
            url="https://www.sec.gov/doc.htm",
            section="Item 9Z",
        )["error"]
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/x",
        "http://www.sec.gov/x",
        "file:///etc/passwd",
        "https://sec.gov.evil.com/",
        "https://www.sec.gov:8443/x",
    ],
)
def test_fetch_filing_section_rejects_non_sec_urls(url: str) -> None:
    # httpx.get is blocked by the autouse fixture, so reaching it would fail differently
    assert (
        "Only https URLs on sec.gov"
        in _call(filings.fetch_filing_section, url=url)["error"]
    )


def _redirecting_get(chain: dict[str, str], calls: list[str]) -> object:
    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        calls.append(url)
        assert kwargs["follow_redirects"] is False
        request = httpx.Request("GET", url)
        if url in chain:
            return httpx.Response(
                302, headers={"location": chain[url]}, request=request
            )
        return httpx.Response(200, text="<p>ok</p>", request=request)

    return fake_get


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example.com/steal",
        "http://www.sec.gov/doc.htm",
        "https://www.sec.gov:444/doc.htm",
    ],
)
def test_redirect_to_disallowed_url_is_blocked(
    monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        filings.httpx,
        "get",
        _redirecting_get({"https://www.sec.gov/a.htm": target}, calls),
    )
    r = _call(filings.fetch_filing_section, url="https://www.sec.gov/a.htm")
    assert "Only https URLs on sec.gov" in r["error"]
    assert calls == [
        "https://www.sec.gov/a.htm"
    ]  # the disallowed hop is never requested


def test_redirects_within_sec_are_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    chain = {
        "https://www.sec.gov/a.htm": "/b.htm",
        "https://www.sec.gov/b.htm": "https://data.sec.gov/c.htm",
    }
    monkeypatch.setattr(filings.httpx, "get", _redirecting_get(chain, calls))
    r = _call(filings.fetch_filing_section, url="https://www.sec.gov/a.htm")
    assert "ok" in r["text"]
    assert calls == [
        "https://www.sec.gov/a.htm",
        "https://www.sec.gov/b.htm",
        "https://data.sec.gov/c.htm",
    ]


def test_redirect_loop_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    loop = {
        "https://www.sec.gov/a.htm": "/b.htm",
        "https://www.sec.gov/b.htm": "/a.htm",
    }
    monkeypatch.setattr(filings.httpx, "get", _redirecting_get(loop, calls))
    assert (
        "Too many redirects"
        in _call(filings.fetch_filing_section, url="https://www.sec.gov/a.htm")["error"]
    )
    assert len(calls) == filings.MAX_REDIRECTS + 1


def test_extract_section_prefers_heading_over_cross_reference() -> None:
    body = "x " * 400
    text = (
        "Item 1A. Risk Factors\nSee Part II, Item 7, Management\u2019s Discussion for details. "
        + body
        + "\nItem 1B. Unresolved\nNone.\nItem 7. Management's Discussion and Analysis\n"
        + body
        + "\nItem 8. Financial Statements\n"
    )
    assert filings.extract_section(text, "Item 7").startswith(
        "Item 7. Management's Discussion"
    )


def test_extract_section_falls_back_to_mid_line_heading() -> None:
    text = (
        "Contents\nRisk Factors 5\nItem 1B. Other 9\nItem 1A. Risk Factors "
        + "risk " * 200
        + "\nItem 1B. Other\n"
    )
    assert filings.extract_section(text, "risk factors").startswith("Risk Factors risk")


def test_html_to_text_normalizes_quotes() -> None:
    assert (
        filings.html_to_text("<p>Management&#8217;s &#8220;view&#8221;</p>")
        == 'Management\'s "view"'
    )


def test_html_to_text() -> None:
    text = filings.html_to_text("<script>x()</script><p>A&amp;B</p><div>C&nbsp;D</div>")
    assert text == "A&B\nC D"


# --- news --------------------------------------------------------------------


def test_search_news_without_key_or_ticker() -> None:
    r = _call(news.search_news, query="chips")
    assert "TAVILY_API_KEY" in r["error"] and r["results"] == []


def test_search_news_yahoo_fallback(market: dict) -> None:
    market["ACME"]["news"] = [
        {
            "content": {
                "title": "Acme beats",
                "summary": "x" * 900,
                "pubDate": "2026-09-01T12:00:00Z",
                "canonicalUrl": {"url": "https://news/1"},
                "provider": {"displayName": "Wire"},
            }
        },
        {
            "title": "Old format",
            "link": "https://news/2",
            "publisher": "Legacy",
            "providerPublishTime": 1700000000,
        },
    ]
    r = _call(news.search_news, query="acme", ticker="acme", max_results=5)
    assert r["provider"] == "yahoo_finance"
    first, second = r["results"]
    assert first == {
        "title": "Acme beats",
        "url": "https://news/1",
        "publisher": "Wire",
        "published": "2026-09-01T12:00:00Z",
        "snippet": "x" * news.SNIPPET_CHARS,
    }
    assert second["url"] == "https://news/2" and second["publisher"] == "Legacy"


def test_search_news_tavily(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            captured["key"] = api_key

        def search(self, query: str, **kwargs: object) -> dict:
            captured.update(query=query, **kwargs)
            return {
                "results": [
                    {
                        "title": "T",
                        "url": "https://u",
                        "content": "c" * 1000,
                        "published_date": "2026-09-29",
                        "score": 0.9,
                    }
                ]
            }

    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setitem(
        sys.modules, "tavily", types.SimpleNamespace(TavilyClient=FakeClient)
    )
    r = _call(news.search_news, query="acme guidance", max_results=50, days=7)
    assert (
        r["provider"] == "tavily"
        and len(r["results"][0]["snippet"]) == news.SNIPPET_CHARS
    )
    assert captured == {
        "key": "test-key",
        "query": "acme guidance",
        "max_results": 15,
        "topic": "news",
        "days": 7,
    }


def test_search_news_tavily_error_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    class Boom:
        def __init__(self, api_key: str) -> None:
            raise RuntimeError("invalid key")

    monkeypatch.setenv("TAVILY_API_KEY", "bad")
    monkeypatch.setitem(sys.modules, "tavily", types.SimpleNamespace(TavilyClient=Boom))
    assert "invalid key" in _call(news.search_news, query="x", topic="finance")["error"]
