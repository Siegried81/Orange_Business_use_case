import json
import os
import sys
import time
import urllib.parse
from datetime import datetime, timedelta
import feedparser
import requests
from pipeline.config import (
    GOOGLE_NEWS_QUERIES,
    GDELT_QUERIES,
    ENABLE_GDELT,
    GDELT_SLEEP_SECONDS,
    GDELT_RETRY_WAIT_SECONDS,
    GDELT_COOLDOWN_MINUTES,
    VENDOR_FEEDS,
    HN_QUERIES,
    COMPETITOR_QUERIES,
    ARXIV_QUERIES,
    SEMANTIC_SCHOLAR_QUERIES,
    SEMANTIC_SCHOLAR_SLEEP_SECONDS,
    SEMANTIC_SCHOLAR_API_KEY,
    SEMANTIC_SCHOLAR_RETRY_WAIT_SECONDS,
    SEMANTIC_SCHOLAR_COOLDOWN_MINUTES,
    REGULATION_QUERIES,
    BUYING_SIGNAL_QUERIES,
    ENABLE_TED,
    TED_API_URL,
    TED_QUERIES,
    TED_FIELDS,
    TED_LOOKBACK_DAYS,
    TED_SLEEP_SECONDS,
    ENABLE_NEWSAPI_AI,
    NEWSAPI_AI_URL,
    NEWSAPI_AI_QUERIES,
    NEWSAPI_AI_KEY,
    NEWSAPI_AI_SLEEP_SECONDS,
)
from pipeline.db import get_connection, init_db, insert_signal

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
HN_ALGOLIA_URL = "https://hn.algolia.com/api/v1/search"
ARXIV_API_URL = "https://export.arxiv.org/api/query"
SEMANTIC_SCHOLAR_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
LOGS_DIR = "logs"
COOLDOWN_FILE = os.path.join(LOGS_DIR, ".source_cooldowns.json")


def _load_cooldowns():
    try:
        with open(COOLDOWN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _mark_blocked(source_key):
    os.makedirs(LOGS_DIR, exist_ok=True)
    cooldowns = _load_cooldowns()
    cooldowns[source_key] = datetime.now().isoformat()
    with open(COOLDOWN_FILE, "w", encoding="utf-8") as f:
        json.dump(cooldowns, f)


def _cooldown_remaining_minutes(source_key, cooldown_minutes):
    last = _load_cooldowns().get(source_key)
    if not last:
        return 0.0
    elapsed_min = (datetime.now() - datetime.fromisoformat(last)).total_seconds() / 60
    return max(0.0, cooldown_minutes - elapsed_min)


class _Tee:

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self.streams:
            s.flush()


def _start_logging():
    os.makedirs(LOGS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    log_path = os.path.join(LOGS_DIR, f"ingest_{timestamp}.log")
    log_file = open(log_path, "a", encoding="utf-8")
    original_stdout = sys.stdout
    sys.stdout = _Tee(original_stdout, log_file)
    print(
        f"=== Ingest run started {datetime.now().isoformat()} -- log: {log_path} ===\n"
    )
    return (log_path, log_file, original_stdout)


def fetch_google_news(conn, vertical, query, signal_type="market_move", max_items=15):
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(query) + "&hl=en"
    feed = feedparser.parse(url)
    count = 0
    for entry in feed.entries[:max_items]:
        added = insert_signal(
            conn,
            source_name=entry.get("source", {}).get("title", "Google News"),
            source_url=entry.get("link"),
            signal_type=signal_type,
            title=entry.get("title"),
            summary=entry.get("summary"),
            published_date=entry.get("published"),
            vertical_hint=vertical,
        )
        count += added
    return count


GDELT_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; InnovationRadar/1.0)"}


def fetch_gdelt(
    conn,
    vertical,
    query,
    signal_type="trend",
    max_records=5,
    timespan="1m",
    _is_retry=False,
):
    params = {
        "query": query,
        "mode": "ArtList",
        "maxrecords": max_records,
        "format": "json",
        "timespan": timespan,
        "sort": "hybridrel",
    }
    resp = requests.get(GDELT_DOC_URL, params=params, headers=GDELT_HEADERS, timeout=20)
    if resp.status_code == 429:
        if not _is_retry:
            print(
                f"[GDELT] rate-limited -- waiting {GDELT_RETRY_WAIT_SECONDS}s and retrying once..."
            )
            time.sleep(GDELT_RETRY_WAIT_SECONDS)
            return fetch_gdelt(
                conn,
                vertical,
                query,
                signal_type,
                max_records,
                timespan,
                _is_retry=True,
            )
        print(
            "[GDELT] still rate-limited after one retry -- skipping this query and starting a cooldown"
        )
        _mark_blocked("gdelt")
        return 0
    resp.raise_for_status()
    data = None
    try:
        data = resp.json()
    except ValueError:
        try:
            data = json.loads(resp.text.strip())
        except (ValueError, AttributeError):
            pass
    if data is None:
        content_type = resp.headers.get("content-type", "unknown")
        snippet = resp.text[:120].replace("\n", " ") if resp.text else "(empty body)"
        print(
            f"[GDELT] non-JSON response (content-type: {content_type!r}, body starts: {snippet!r}) -- skipping this query and starting a cooldown"
        )
        _mark_blocked("gdelt")
        return 0
    count = 0
    for article in data.get("articles", []):
        added = insert_signal(
            conn,
            source_name=article.get("domain", "GDELT"),
            source_url=article.get("url"),
            signal_type=signal_type,
            title=article.get("title"),
            summary=None,
            published_date=article.get("seendate"),
            vertical_hint=vertical,
        )
        count += added
    return count


def fetch_vendor_feed(
    conn, name, url, vertical_hint=None, signal_type="tech_maturity", max_items=10
):
    feed = feedparser.parse(url)
    count = 0
    for entry in feed.entries[:max_items]:
        added = insert_signal(
            conn,
            source_name=name,
            source_url=entry.get("link"),
            signal_type=signal_type,
            title=entry.get("title"),
            summary=entry.get("summary"),
            published_date=entry.get("published"),
            vertical_hint=vertical_hint,
        )
        count += added
    return count


def fetch_hacker_news(
    conn, query, signal_type="trend", max_items=10, vertical_hint=None
):
    resp = requests.get(
        HN_ALGOLIA_URL, params={"query": query, "tags": "story"}, timeout=15
    )
    resp.raise_for_status()
    data = resp.json()
    count = 0
    for hit in data.get("hits", [])[:max_items]:
        added = insert_signal(
            conn,
            source_name="Hacker News",
            source_url=hit.get("url")
            or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            signal_type=signal_type,
            title=hit.get("title"),
            summary=None,
            published_date=hit.get("created_at"),
            vertical_hint=vertical_hint,
        )
        count += added
    return count


def fetch_arxiv(conn, vertical, query, signal_type="proof_signal", max_results=10):
    terms = query.split()
    search_query = " OR ".join((f"all:{t}" for t in terms))
    params = {
        "search_query": search_query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": max_results,
    }
    url = ARXIV_API_URL + "?" + urllib.parse.urlencode(params)
    feed = feedparser.parse(url)
    count = 0
    for entry in feed.entries:
        added = insert_signal(
            conn,
            source_name="arXiv",
            source_url=entry.get("link"),
            signal_type=signal_type,
            title=entry.get("title", "").replace("\n", " ").strip(),
            summary=entry.get("summary"),
            published_date=entry.get("published"),
            vertical_hint=vertical,
        )
        count += added
    return count


def fetch_semantic_scholar(
    conn, vertical, query, signal_type="proof_signal", limit=10, _is_retry=False
):
    params = {
        "query": query,
        "limit": limit,
        "fields": "title,abstract,url,publicationDate,venue",
    }
    headers = (
        {"x-api-key": SEMANTIC_SCHOLAR_API_KEY} if SEMANTIC_SCHOLAR_API_KEY else {}
    )
    resp = requests.get(
        SEMANTIC_SCHOLAR_URL, params=params, headers=headers, timeout=20
    )
    if resp.status_code == 429:
        if not SEMANTIC_SCHOLAR_API_KEY and (not _is_retry):
            print(
                f"[Semantic Scholar] rate-limited (no API key -- get one free at semanticscholar.org/product/api) -- waiting {SEMANTIC_SCHOLAR_RETRY_WAIT_SECONDS}s and retrying once..."
            )
            time.sleep(SEMANTIC_SCHOLAR_RETRY_WAIT_SECONDS)
            return fetch_semantic_scholar(
                conn, vertical, query, signal_type, limit, _is_retry=True
            )
        print(
            "[Semantic Scholar] still rate-limited after retry -- skipping this query and starting a cooldown"
        )
        _mark_blocked("semantic_scholar")
        return 0
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError:
        content_type = resp.headers.get("content-type", "unknown")
        print(
            f"[Semantic Scholar] non-JSON response (content-type: {content_type!r}) -- skipping this query and starting a cooldown"
        )
        _mark_blocked("semantic_scholar")
        return 0
    count = 0
    for paper in data.get("data", []):
        added = insert_signal(
            conn,
            source_name=paper.get("venue") or "Semantic Scholar",
            source_url=paper.get("url"),
            signal_type=signal_type,
            title=paper.get("title"),
            summary=paper.get("abstract"),
            published_date=paper.get("publicationDate"),
            vertical_hint=vertical,
        )
        count += added
    return count


def _ted_pick_multilingual(value):
    if not value:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for lang in ("eng", "fra"):
            v = value.get(lang)
            if v:
                return v[0] if isinstance(v, list) else v
        for v in value.values():
            if v:
                return v[0] if isinstance(v, list) else v
    return None


def fetch_ted(conn, vertical, query, signal_type="buying_signal", max_items=25):
    since = (datetime.now() - timedelta(days=TED_LOOKBACK_DAYS)).strftime("%Y%m%d")
    body = {
        "query": f"({query}) AND publication-date>={since}",
        "fields": TED_FIELDS,
        "limit": max_items,
        "scope": "ACTIVE",
        "checkQuerySyntax": False,
        "paginationMode": "ITERATION",
    }
    resp = requests.post(
        TED_API_URL, json=body, headers={"Content-Type": "application/json"}, timeout=25
    )
    if resp.status_code != 200:
        print(
            f"[TED] request failed (HTTP {resp.status_code}) -- skipping this query. Body: {resp.text[:200]}"
        )
        return 0
    data = resp.json()
    notices = data.get("notices") or data.get("results") or []
    count = 0
    for n in notices[:max_items]:
        title = _ted_pick_multilingual(n.get("notice-title"))
        buyer = _ted_pick_multilingual(n.get("buyer-name"))
        pub_number = n.get("publication-number")
        combined_title = (
            f"{title} — {buyer}"
            if title and buyer
            else title or f"TED notice {pub_number}"
        )
        url = (
            f"https://ted.europa.eu/en/notice/-/detail/{pub_number}"
            if pub_number
            else None
        )
        added = insert_signal(
            conn,
            source_name="TED - EU Public Procurement",
            source_url=url,
            signal_type=signal_type,
            title=combined_title,
            summary=None,
            published_date=n.get("publication-date"),
            vertical_hint=vertical,
        )
        count += added
    return count


def fetch_newsapi_ai(conn, vertical, query, signal_type="market_move", max_items=15):
    if not NEWSAPI_AI_KEY:
        print(
            "[NewsAPI.ai] NEWSAPI_AI_KEY not set in .env -- skipping (get a free key at newsapi.ai)"
        )
        return 0
    params = {
        "apiKey": NEWSAPI_AI_KEY,
        "action": "getArticles",
        "keyword": query,
        "keywordSearchMode": "simple",
        "lang": "eng",
        "articlesSortBy": "date",
        "articlesSortByAsc": False,
        "articlesCount": max_items,
        "resultType": "articles",
    }
    resp = requests.get(NEWSAPI_AI_URL, params=params, timeout=20)
    if resp.status_code != 200:
        print(
            f"[NewsAPI.ai] request failed (HTTP {resp.status_code}) -- skipping this query"
        )
        return 0
    data = resp.json()
    articles = data.get("articles", {}).get("results", [])
    count = 0
    for a in articles[:max_items]:
        added = insert_signal(
            conn,
            source_name=(a.get("source") or {}).get("title", "NewsAPI.ai"),
            source_url=a.get("url"),
            signal_type=signal_type,
            title=a.get("title"),
            summary=(a.get("body") or "")[:500] or None,
            published_date=a.get("date"),
            vertical_hint=vertical,
        )
        count += added
    return count


def run_full_refresh():
    log_path, log_file, original_stdout = _start_logging()
    try:
        init_db()
        conn = get_connection()
        total = 0

        def safe_run(label, fn, *args, **kwargs):
            nonlocal total
            try:
                n = fn(*args, **kwargs)
                print(f"[{label}] +{n} new signals")
                total += n
            except Exception as e:
                print(
                    f"[{label}] FAILED ({e}) -- skipping, continuing with other sources"
                )

        for q in GOOGLE_NEWS_QUERIES:
            safe_run(
                f"Google News / {q['vertical']}",
                fetch_google_news,
                conn,
                q["vertical"],
                q["query"],
            )
        if ENABLE_NEWSAPI_AI:
            for q in NEWSAPI_AI_QUERIES:
                safe_run(
                    f"NewsAPI.ai / {q['vertical']}",
                    fetch_newsapi_ai,
                    conn,
                    q["vertical"],
                    q["query"],
                )
                time.sleep(NEWSAPI_AI_SLEEP_SECONDS)
        else:
            print(
                "[NewsAPI.ai] disabled in config.py (ENABLE_NEWSAPI_AI = False) -- skipping"
            )
        if ENABLE_GDELT:
            remaining = _cooldown_remaining_minutes("gdelt", GDELT_COOLDOWN_MINUTES)
            if remaining > 0:
                print(
                    f"[GDELT] cooling down from a recent rate-limit ({remaining:.0f} min left) -- skipping the whole GDELT pass this run instead of hammering it again. It'll retry automatically once the cooldown clears."
                )
            else:
                consecutive_blocks = 0
                for q in GDELT_QUERIES:
                    safe_run(
                        f"GDELT / {q['vertical']}",
                        fetch_gdelt,
                        conn,
                        q["vertical"],
                        q["query"],
                    )
                    if _cooldown_remaining_minutes("gdelt", GDELT_COOLDOWN_MINUTES) > 0:
                        consecutive_blocks += 1
                    else:
                        consecutive_blocks = 0
                    if consecutive_blocks >= 2:
                        print(
                            f"[GDELT] blocked 2x in a row -- stopping the GDELT pass early (only tried {GDELT_QUERIES.index(q) + 1}/{len(GDELT_QUERIES)} verticals) instead of burning through the rest. Will retry automatically after the {GDELT_COOLDOWN_MINUTES}-min cooldown."
                        )
                        break
                    time.sleep(GDELT_SLEEP_SECONDS)
        else:
            print("[GDELT] disabled in config.py (ENABLE_GDELT = False) -- skipping")
        for feed in VENDOR_FEEDS:
            safe_run(
                f"Vendor / {feed['name']}",
                fetch_vendor_feed,
                conn,
                feed["name"],
                feed["url"],
                vertical_hint="Cross-vertical",
            )
        for q in HN_QUERIES:
            safe_run(
                f"Hacker News / '{q}'",
                fetch_hacker_news,
                conn,
                q,
                vertical_hint="Cross-vertical",
            )
        for q in COMPETITOR_QUERIES:
            safe_run(
                f"Competitor watch / {q['vertical']}",
                fetch_google_news,
                conn,
                q["vertical"],
                q["query"],
                signal_type="market_move",
            )
        for q in ARXIV_QUERIES:
            safe_run(
                f"arXiv / {q['vertical']}", fetch_arxiv, conn, q["vertical"], q["query"]
            )
        for q in REGULATION_QUERIES:
            safe_run(
                f"Regulation (EUR-Lex) / {q['vertical']}",
                fetch_google_news,
                conn,
                q["vertical"],
                q["query"],
                signal_type="regulation",
            )
        if ENABLE_TED:
            for q in TED_QUERIES:
                safe_run(
                    f"TED API / {q['vertical']}",
                    fetch_ted,
                    conn,
                    q["vertical"],
                    q["query"],
                )
                time.sleep(TED_SLEEP_SECONDS)
        else:
            print(
                "[TED] ENABLE_TED = False -- falling back to Google News site: scrape"
            )
            for q in BUYING_SIGNAL_QUERIES:
                safe_run(
                    f"Buying signal (TED via Google News) / {q['vertical']}",
                    fetch_google_news,
                    conn,
                    q["vertical"],
                    q["query"],
                    signal_type="buying_signal",
                )
        remaining = _cooldown_remaining_minutes(
            "semantic_scholar", SEMANTIC_SCHOLAR_COOLDOWN_MINUTES
        )
        if remaining > 0:
            print(
                f"[Semantic Scholar] cooling down from a recent rate-limit ({remaining:.0f} min left) -- skipping the whole pass this run. It'll retry automatically once the cooldown clears."
            )
        else:
            consecutive_blocks = 0
            for q in SEMANTIC_SCHOLAR_QUERIES:
                safe_run(
                    f"Semantic Scholar / {q['vertical']}",
                    fetch_semantic_scholar,
                    conn,
                    q["vertical"],
                    q["query"],
                )
                if (
                    _cooldown_remaining_minutes(
                        "semantic_scholar", SEMANTIC_SCHOLAR_COOLDOWN_MINUTES
                    )
                    > 0
                ):
                    consecutive_blocks += 1
                else:
                    consecutive_blocks = 0
                if consecutive_blocks >= 2:
                    print(
                        f"[Semantic Scholar] blocked 2x in a row -- stopping this pass early instead of burning through the rest. Will retry automatically after the {SEMANTIC_SCHOLAR_COOLDOWN_MINUTES}-min cooldown."
                    )
                    break
                time.sleep(SEMANTIC_SCHOLAR_SLEEP_SECONDS)
        conn.close()
        print(f"\nTotal new signals collected: {total}")
        print(f"\n=== Ingest run finished {datetime.now().isoformat()} ===")
    finally:
        sys.stdout = original_stdout
        log_file.close()
        print(f"Log written: {log_path}")


if __name__ == "__main__":
    run_full_refresh()  