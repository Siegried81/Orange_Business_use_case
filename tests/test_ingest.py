"""Offline tests for pipeline/ingest.py: parsing of each source's payload into
signals, the HTTP error paths, and the cooldown file. Every HTTP call and feed
download is replaced by a stub (conftest.py blocks sockets anyway)."""
import json
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from pipeline import ingest
from pipeline.db import SCHEMA


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    yield c
    c.close()


@pytest.fixture
def cooldown_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "LOGS_DIR", str(tmp_path))
    monkeypatch.setattr(ingest, "COOLDOWN_FILE", str(tmp_path / "cooldowns.json"))
    return tmp_path


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", content_type="application/json"):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text or payload is None else json.dumps(payload)
        self.headers = {"content-type": content_type}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM signals ORDER BY id")]


def _feed(*entries):
    return SimpleNamespace(entries=list(entries))


# ---------- _parse_feed ----------

def test_parse_feed_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: FakeResponse(503, text="busy"))
    with pytest.raises(RuntimeError):
        ingest._parse_feed("https://example.test/feed")


def test_parse_feed_parses_rss_bytes(monkeypatch):
    rss = (
        b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
        b"<item><title>Hello</title><link>https://e.test/1</link></item></channel></rss>"
    )
    resp = SimpleNamespace(content=rss, raise_for_status=lambda: None)
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: resp)
    feed = ingest._parse_feed("https://example.test/feed")
    assert [e.title for e in feed.entries] == ["Hello"]


# ---------- RSS-based sources ----------

def test_google_news_caps_items_and_skips_duplicates(conn, monkeypatch):
    entries = [{"link": f"https://e.test/{i}", "title": f"T{i}", "published": "x"} for i in range(5)]
    monkeypatch.setattr(ingest, "_parse_feed", lambda url: _feed(*entries))
    assert ingest.fetch_google_news(conn, "Retail", "q", max_items=3) == 3
    # Second run: same URLs are already stored, nothing new is counted.
    assert ingest.fetch_google_news(conn, "Retail", "q", max_items=3) == 0
    assert len(_rows(conn)) == 3


def test_google_news_query_is_url_encoded(conn, monkeypatch):
    seen = {}

    def fake_parse(url):
        seen["url"] = url
        return _feed()

    monkeypatch.setattr(ingest, "_parse_feed", fake_parse)
    ingest.fetch_google_news(conn, "Retail", "edge AI & 5G")
    assert "edge%20AI%20%26%205G" in seen["url"]


def test_google_news_defaults_source_name_when_publisher_missing(conn, monkeypatch):
    monkeypatch.setattr(ingest, "_parse_feed", lambda url: _feed({"link": "https://e.test/1", "title": "T"}))
    ingest.fetch_google_news(conn, "Retail", "q")
    assert _rows(conn)[0]["source_name"] == "Google News"


def test_vendor_feed_uses_given_name_and_type(conn, monkeypatch):
    monkeypatch.setattr(
        ingest, "_parse_feed",
        lambda url: _feed({"link": "https://v.test/1", "title": "Release", "summary": "notes", "published": "d"}),
    )
    assert ingest.fetch_vendor_feed(conn, "Vendor", "https://v.test/rss", vertical_hint="Retail") == 1
    row = _rows(conn)[0]
    assert (row["source_name"], row["signal_type"], row["vertical_hint"]) == ("Vendor", "tech_maturity", "Retail")
    assert row["summary"] == "notes"


def test_arxiv_builds_or_query_and_cleans_title(conn, monkeypatch):
    seen = {}

    def fake_parse(url):
        seen["url"] = url
        return _feed({"link": "https://arxiv.test/1", "title": "Two\nlines ", "summary": "s", "published": "d"})

    monkeypatch.setattr(ingest, "_parse_feed", fake_parse)
    ingest.fetch_arxiv(conn, "Healthcare", "federated learning", max_results=3)
    assert "all%3Afederated+OR+all%3Alearning" in seen["url"]
    assert "max_results=3" in seen["url"]
    row = _rows(conn)[0]
    assert row["title"] == "Two lines"
    assert row["source_name"] == "arXiv"
    assert row["signal_type"] == "proof_signal"


# ---------- JSON APIs ----------

def test_hacker_news_falls_back_to_item_url(conn, monkeypatch):
    hits = [
        {"url": "https://x.test/a", "title": "With url", "created_at": "2026-01-01T00:00:00Z", "objectID": "1"},
        {"url": None, "title": "Ask HN", "created_at": "2026-01-02T00:00:00Z", "objectID": "42"},
    ]
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: FakeResponse(payload={"hits": hits}))
    assert ingest.fetch_hacker_news(conn, "llm") == 2
    urls = [r["source_url"] for r in _rows(conn)]
    assert urls == ["https://x.test/a", "https://news.ycombinator.com/item?id=42"]


def test_hacker_news_raises_on_http_error(conn, monkeypatch):
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: FakeResponse(500, text="boom"))
    with pytest.raises(RuntimeError):
        ingest.fetch_hacker_news(conn, "llm")


def test_gdelt_maps_articles(conn, monkeypatch):
    payload = {
        "articles": [
            {"domain": "site.test", "url": "https://site.test/a", "title": "T", "seendate": "20260101T000000Z"}
        ]
    }
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: FakeResponse(payload=payload))
    assert ingest.fetch_gdelt(conn, "Energy", "q") == 1
    row = _rows(conn)[0]
    assert (row["source_name"], row["signal_type"], row["published_date"]) == (
        "site.test",
        "trend",
        "20260101T000000Z",
    )


def test_gdelt_parses_json_sent_with_wrong_content_type(conn, monkeypatch):
    body = json.dumps({"articles": [{"url": "https://s.test/1", "title": "T"}]})

    class Resp(FakeResponse):
        def json(self):
            raise ValueError("wrong content type")

    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: Resp(text=f"  {body}\n", payload={"x": 1}))
    assert ingest.fetch_gdelt(conn, "Energy", "q") == 1
    assert _rows(conn)[0]["source_name"] == "GDELT"


def test_gdelt_non_json_body_starts_cooldown(conn, cooldown_dir, monkeypatch):
    monkeypatch.setattr(
        ingest.requests, "get", lambda *a, **k: FakeResponse(text="<html>blocked</html>", content_type="text/html")
    )
    assert ingest.fetch_gdelt(conn, "Energy", "q") == 0
    assert "gdelt" in json.loads((cooldown_dir / "cooldowns.json").read_text())
    assert _rows(conn) == []


def test_gdelt_retries_once_on_429_then_gives_up(conn, cooldown_dir, monkeypatch):
    calls = []
    slept = []
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: calls.append(1) or FakeResponse(429, text="slow"))
    monkeypatch.setattr(ingest.time, "sleep", slept.append)
    assert ingest.fetch_gdelt(conn, "Energy", "q") == 0
    assert len(calls) == 2
    assert slept == [ingest.GDELT_RETRY_WAIT_SECONDS]
    assert ingest._cooldown_remaining_minutes("gdelt", 60) > 0


def test_semantic_scholar_maps_papers_and_venue_fallback(conn, monkeypatch):
    data = {
        "data": [
            {"venue": "Nature", "url": "https://s.test/1", "title": "A", "abstract": "abs", "publicationDate": "2026-01-01"},
            {"venue": "", "url": "https://s.test/2", "title": "B", "abstract": None, "publicationDate": None},
        ]
    }
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: FakeResponse(payload=data))
    assert ingest.fetch_semantic_scholar(conn, "Healthcare", "q") == 2
    assert [r["source_name"] for r in _rows(conn)] == ["Nature", "Semantic Scholar"]


def test_semantic_scholar_429_without_key_retries_then_cools_down(conn, cooldown_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(ingest, "SEMANTIC_SCHOLAR_API_KEY", "")
    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: calls.append(1) or FakeResponse(429, text=""))
    assert ingest.fetch_semantic_scholar(conn, "Healthcare", "q") == 0
    assert len(calls) == 2
    assert ingest._cooldown_remaining_minutes("semantic_scholar", 60) > 0


def test_semantic_scholar_429_with_key_does_not_retry(conn, cooldown_dir, monkeypatch):
    calls = []
    sent = {}
    monkeypatch.setattr(ingest, "SEMANTIC_SCHOLAR_API_KEY", "k")

    def fake_get(*a, **k):
        calls.append(1)
        sent.update(k.get("headers", {}))
        return FakeResponse(429, text="")

    monkeypatch.setattr(ingest.requests, "get", fake_get)
    assert ingest.fetch_semantic_scholar(conn, "Healthcare", "q") == 0
    assert len(calls) == 1
    assert sent == {"x-api-key": "k"}


# ---------- TED ----------

@pytest.mark.parametrize(
    "value, expected",
    [
        (None, None),
        ("", None),
        ("plain", "plain"),
        ({"fra": ["Titre"], "eng": ["Title"]}, "Title"),
        ({"fra": "Titre"}, "Titre"),
        ({"deu": ["Titel"]}, "Titel"),
        ({"eng": []}, None),
        (12, None),
    ],
)
def test_ted_pick_multilingual(value, expected):
    assert ingest._ted_pick_multilingual(value) == expected


def test_ted_builds_title_url_and_source(conn, monkeypatch):
    notices = [
        {
            "notice-title": {"eng": ["Network"]},
            "buyer-name": {"eng": ["City"]},
            "publication-number": "123-2026",
            "publication-date": "2026-01-01+01:00",
        },
        {"notice-title": None, "buyer-name": None, "publication-number": "9-2026"},
        {"notice-title": "Only title", "buyer-name": None, "publication-number": None},
    ]
    monkeypatch.setattr(ingest.requests, "post", lambda *a, **k: FakeResponse(payload={"notices": notices}))
    assert ingest.fetch_ted(conn, "Public Sector", "5G") == 3
    rows = _rows(conn)
    assert rows[0]["title"] == "Network — City"
    assert rows[0]["source_url"] == "https://ted.europa.eu/en/notice/-/detail/123-2026"
    assert rows[1]["title"] == "TED notice 9-2026"
    assert rows[2]["title"] == "Only title"
    assert rows[2]["source_url"] is None
    assert {r["source_name"] for r in rows} == {ingest.TED_SOURCE_NAME}


def test_ted_query_is_limited_to_the_lookback_window(conn, monkeypatch):
    sent = {}

    def fake_post(url, json=None, **k):
        sent.update(json)
        return FakeResponse(payload={"results": []})

    monkeypatch.setattr(ingest.requests, "post", fake_post)
    ingest.fetch_ted(conn, "Public Sector", "5G")
    since = (datetime.now() - timedelta(days=ingest.TED_LOOKBACK_DAYS)).strftime("%Y%m%d")
    assert sent["query"] == f"(5G) AND publication-date>={since}"


def test_ted_http_error_is_skipped(conn, monkeypatch):
    monkeypatch.setattr(ingest.requests, "post", lambda *a, **k: FakeResponse(400, text="bad query"))
    assert ingest.fetch_ted(conn, "Public Sector", "5G") == 0


# ---------- NewsAPI.ai ----------

def test_newsapi_without_key_makes_no_request(conn, monkeypatch):
    monkeypatch.setattr(ingest, "NEWSAPI_AI_KEY", "")

    def fail(*a, **k):
        raise AssertionError("no request expected")

    monkeypatch.setattr(ingest.requests, "get", fail)
    assert ingest.fetch_newsapi_ai(conn, "Retail", "q") == 0


def test_newsapi_maps_articles_and_truncates_body(conn, monkeypatch):
    monkeypatch.setattr(ingest, "NEWSAPI_AI_KEY", "k")
    results = [
        {"source": {"title": "Wire"}, "url": "https://n.test/1", "title": "A", "body": "x" * 900, "date": "2026-01-01"},
        {"source": None, "url": "https://n.test/2", "title": "B", "body": "", "date": "2026-01-02"},
    ]
    monkeypatch.setattr(
        ingest.requests, "get", lambda *a, **k: FakeResponse(payload={"articles": {"results": results}})
    )
    assert ingest.fetch_newsapi_ai(conn, "Retail", "q") == 2
    rows = _rows(conn)
    assert rows[0]["source_name"] == "Wire"
    assert len(rows[0]["summary"]) == 500
    assert rows[1]["source_name"] == "NewsAPI.ai"
    assert rows[1]["summary"] is None


def test_newsapi_http_error_is_skipped(conn, monkeypatch):
    monkeypatch.setattr(ingest, "NEWSAPI_AI_KEY", "k")
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: FakeResponse(401, text="no"))
    assert ingest.fetch_newsapi_ai(conn, "Retail", "q") == 0


# ---------- cooldown file ----------

def test_cooldown_missing_or_corrupt_file_means_no_cooldown(cooldown_dir):
    assert ingest._cooldown_remaining_minutes("gdelt", 30) == 0.0
    (cooldown_dir / "cooldowns.json").write_text("{not json")
    assert ingest._cooldown_remaining_minutes("gdelt", 30) == 0.0


def test_cooldown_counts_down_and_expires(cooldown_dir):
    ingest._mark_blocked("gdelt")
    assert 29 < ingest._cooldown_remaining_minutes("gdelt", 30) <= 30
    old = (datetime.now() - timedelta(minutes=45)).isoformat()
    (cooldown_dir / "cooldowns.json").write_text(json.dumps({"gdelt": old, "other": old}))
    assert ingest._cooldown_remaining_minutes("gdelt", 30) == 0.0
    ingest._mark_blocked("semantic_scholar")
    saved = json.loads((cooldown_dir / "cooldowns.json").read_text())
    assert set(saved) == {"gdelt", "other", "semantic_scholar"}
