"""run_full_refresh() pauses between requests with the delays config.py declares.

ARXIV_SLEEP_SECONDS and GOOGLE_NEWS_SLEEP_SECONDS were defined and never read,
so the arXiv API (which asks for one request every three seconds) and Google
News were queried back to back. Every fetcher is stubbed; the sleeps are
recorded instead of waited for.
"""
import sqlite3

from pipeline import config, ingest


def _run(monkeypatch, tmp_path, **overrides):
    sleeps = []
    monkeypatch.setattr(ingest.time, "sleep", sleeps.append)
    monkeypatch.setattr(ingest, "LOGS_DIR", str(tmp_path))
    monkeypatch.setattr(ingest, "COOLDOWN_FILE", str(tmp_path / "cooldowns.json"))
    monkeypatch.setattr(ingest, "init_db", lambda: None)
    monkeypatch.setattr(ingest, "get_connection", lambda: sqlite3.connect(":memory:"))
    one = [{"vertical": "Retail", "query": "q"}]
    for name in (
        "GOOGLE_NEWS_QUERIES", "COMPETITOR_QUERIES", "ARXIV_QUERIES", "REGULATION_QUERIES",
        "BUYING_SIGNAL_QUERIES", "TED_QUERIES", "NEWSAPI_AI_QUERIES", "GDELT_QUERIES",
        "SEMANTIC_SCHOLAR_QUERIES",
    ):
        monkeypatch.setattr(ingest, name, one)
    monkeypatch.setattr(ingest, "VENDOR_FEEDS", [])
    monkeypatch.setattr(ingest, "HN_QUERIES", [])
    for name in (
        "fetch_google_news", "fetch_arxiv", "fetch_ted", "fetch_newsapi_ai", "fetch_gdelt",
        "fetch_semantic_scholar",
    ):
        monkeypatch.setattr(ingest, name, lambda *a, **k: 0)
    # Distinct values, so a sleep can be traced to the setting that asked for it.
    monkeypatch.setattr(ingest, "GOOGLE_NEWS_SLEEP_SECONDS", 7)
    monkeypatch.setattr(ingest, "ARXIV_SLEEP_SECONDS", 9)
    for name, value in overrides.items():
        monkeypatch.setattr(ingest, name, value)
    ingest.run_full_refresh()
    return sleeps


def test_arxiv_and_google_news_requests_are_spaced(monkeypatch, tmp_path):
    sleeps = _run(monkeypatch, tmp_path)
    assert sleeps.count(9) == 1
    # Google News is queried three times: news, competitor watch, EUR-Lex.
    assert sleeps.count(7) == 3


def test_the_ted_fallback_through_google_news_is_spaced_too(monkeypatch, tmp_path):
    sleeps = _run(monkeypatch, tmp_path, ENABLE_TED=False)
    assert sleeps.count(7) == 4


def test_every_signal_type_ingest_assigns_is_declared():
    """config.SIGNAL_TYPES lists the allowed values; nothing validates against
    it at run time, so this pins that the fetchers and radar.db stay inside it."""
    import inspect

    used = {
        p.default
        for name, fn in inspect.getmembers(ingest, inspect.isfunction)
        if name.startswith("fetch_")
        for p in inspect.signature(fn).parameters.values()
        if p.name == "signal_type"
    }
    used |= {"market_move", "regulation", "buying_signal"}  # passed explicitly by run_full_refresh
    assert used <= set(config.SIGNAL_TYPES)
    conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        stored = {r[0] for r in conn.execute("SELECT DISTINCT signal_type FROM signals")}
    finally:
        conn.close()
    assert stored <= set(config.SIGNAL_TYPES)
