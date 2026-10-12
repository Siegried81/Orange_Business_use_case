"""scripts/derive_caps.py: the caps keep the percentile the one-keyword caps held.

Small in-memory databases pin the statistics and the replay of both link rules;
one test checks that the constants in pipeline/scoring.py are what the script
derives from the committed radar.db (read-only), so the two cannot drift apart.
"""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline import scoring
from pipeline.db import SCHEMA, insert_signal

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import derive_caps  # noqa: E402


def test_mid_rank_percentile_splits_ties():
    assert derive_caps.mid_rank_percentile([1, 2, 3, 4], 5) == 1.0
    assert derive_caps.mid_rank_percentile([1, 2, 3, 4], 3) == 0.625
    assert derive_caps.mid_rank_percentile([5, 5, 5, 5], 5) == 0.5


def test_quantile_interpolates_inclusively():
    assert derive_caps.quantile([0, 10], 0.5) == 5
    assert derive_caps.quantile([1, 2, 3, 4, 5], 0.0) == 1
    assert derive_caps.quantile([1, 2, 3, 4, 5], 1.0) == 5
    assert derive_caps.quantile([1, 2, 3, 4, 5], 0.9) == pytest.approx(4.6)


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    yield conn
    conn.close()


def _space(conn, label, use_case, technology):
    conn.execute(
        "INSERT INTO opportunity_spaces (label, run_id, vertical, use_case, technology, created_at) "
        "VALUES (?, 'test-run', 'Retail', ?, ?, ?)",
        (label, use_case, technology, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def _signal(conn, title, source):
    insert_signal(
        conn,
        source_name=source,
        source_url=f"https://example.com/{abs(hash((title, source)))}",
        signal_type="market_move",
        title=title,
        summary=None,
        published_date=None,
        vertical_hint="Retail",
    )


def test_both_rules_are_replayed_without_writing_links(db_conn):
    _space(db_conn, "OS001", "Contact Centre", "Agentic AI")
    _signal(db_conn, "Agentic contact centre launch", "A")   # 2 keywords
    _signal(db_conn, "Agentic AI in retail stores", "B")     # 1 keyword ("agentic")
    _signal(db_conn, "Agentic agents again", "B")            # 1 keyword
    signals_1, publishers_1 = derive_caps.per_os_counts(db_conn, 1, top_n=56)
    signals_2, publishers_2 = derive_caps.per_os_counts(db_conn, 2, top_n=56)
    assert (signals_1, publishers_1) == ([3], [2])
    assert (signals_2, publishers_2) == ([1], [1])
    assert derive_caps.per_os_counts(db_conn, 1, top_n=2)[0] == [2]
    assert db_conn.execute("SELECT COUNT(*) FROM opportunity_signals").fetchone()[0] == 0


def test_new_cap_sits_at_the_old_caps_percentile(db_conn, monkeypatch):
    # Four OS; under the one-keyword rule they get 1, 2, 3 and 4 signals, under
    # the two-keyword rule 0, 1, 1 and 2.
    # Each OS has its own two keywords (letters only: digits are not keywords).
    pairs = [("alpha", "xray"), ("bravo", "yankee"), ("charlie", "zulu"), ("delta", "whisky")]
    for i, (n_two, (use_case, technology)) in enumerate(zip([0, 1, 1, 2], pairs)):
        _space(db_conn, f"OS{i + 1:03d}", use_case, technology)
        for j in range(i + 1):
            words = f"{use_case} {technology}" if j < n_two else f"{use_case} unrelated"
            _signal(db_conn, f"{words} item {'abcd'[j]}", f"pub{j}")
    monkeypatch.setattr(derive_caps, "OLD_CAPS", {"market_signal_strength": 3, "source_diversity": 3})
    result = derive_caps.derive(db_conn, top_n=56)
    market = result["market_signal_strength"]
    assert market["old_values"] == [1, 2, 3, 4]
    assert market["new_values"] == [0, 1, 1, 2]
    assert market["percentile"] == 0.625            # (2 below + 0.5 * 1 equal) / 4
    assert market["new_quantile"] == pytest.approx(1.0)
    assert market["new_cap"] == 1
    assert market["old_saturated"] == 2


def test_cap_is_never_below_one(db_conn, monkeypatch):
    _space(db_conn, "OS001", "Fleet", "Telematics")
    _signal(db_conn, "fleet only", "A")
    monkeypatch.setattr(derive_caps, "OLD_CAPS", {"market_signal_strength": 1, "source_diversity": 1})
    assert derive_caps.derive(db_conn, top_n=56)["market_signal_strength"]["new_cap"] == 1


def test_scoring_caps_match_the_derivation_on_the_committed_database():
    conn = sqlite3.connect(f"file:{ROOT / 'radar.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        result = derive_caps.derive(conn)
    finally:
        conn.close()
    assert result["market_signal_strength"]["new_cap"] == scoring.MARKET_SIGNAL_CAP
    assert result["source_diversity"]["new_cap"] == scoring.SOURCE_DIVERSITY_CAP
