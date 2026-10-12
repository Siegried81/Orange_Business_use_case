"""Score history: every accepted score write is also appended to a history table.

The current tables keep one row per opportunity space, so every latest-score
read is unchanged; these tests pin that down, the baseline copied when the
history is first created, and that a refused fallback write leaves no trace.
They run on in-memory or temporary databases; the committed radar.db is only
ever read through a copy.
"""
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline import config, db
from pipeline.db import (
    SCHEMA,
    delete_opportunity_spaces,
    ensure_history_tables,
    get_latest_scores,
    get_score_history,
    insert_right_to_win_score,
    insert_score,
)

ROOT = Path(__file__).resolve().parents[1]
SUBS = {"market_signal_strength": 6.0, "source_diversity": 5.0, "evidence_quality": 7.0,
        "novelty_momentum": 4.0, "strategic_relevance": 8.0}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(SCHEMA)
    yield c
    c.close()


def _os(conn, label="OS001"):
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO opportunity_spaces (label, run_id, vertical, use_case, technology, created_at) "
        "VALUES (?, 'r', 'Manufacturing', 'Energy Optimization', 'IoT Platforms', ?)", (label, now))
    conn.commit()
    return cur.lastrowid


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_reading_history_never_creates_it(conn):
    assert get_score_history(conn) == []
    assert "score_history" not in _tables(conn)


def test_every_write_is_kept_while_scores_keeps_one_row(conn):
    os_id = _os(conn)
    assert insert_score(conn, os_id, SUBS, 6.1, "good evidence", "fits", urgency_score=3.0)
    assert insert_score(conn, os_id, SUBS, 7.4, "better evidence", "fits", urgency_score=4.0)
    assert conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 1
    rows = get_score_history(conn, "OS001")
    assert [(r["total_score"], r["origin"]) for r in rows] == [(6.1, "write"), (7.4, "write")]
    assert rows[1]["label"] == "OS001" and rows[1]["technology"] == "IoT Platforms"
    # The last history row is exactly the current row.
    current = conn.execute("SELECT * FROM scores").fetchone()
    for col in db._HISTORY_OF["score_history"][1]:
        assert rows[-1][col] == current[col]
    assert get_latest_scores(conn)[0]["total_score"] == 7.4


def test_refused_fallback_write_leaves_no_history(conn):
    os_id = _os(conn)
    insert_score(conn, os_id, SUBS, 6.1, "good evidence", "fits")
    assert not insert_score(conn, os_id, SUBS, 5.0, "LLM unavailable", "LLM unavailable")
    assert [r["total_score"] for r in get_score_history(conn)] == [6.1]


def test_right_to_win_history(conn):
    os_id = _os(conn)
    insert_right_to_win_score(conn, os_id, "L1", 7.0, "Live Intelligence", "close to the portfolio")
    insert_right_to_win_score(conn, os_id, "L3", 4.0, "", "further away")
    assert not insert_right_to_win_score(conn, os_id, "L4", 0.0, "", "LLM unavailable")
    rows = get_score_history(conn, "OS001", kind="right_to_win")
    assert [(r["portfolio_distance"], r["right_to_win_score"]) for r in rows] == [("L1", 7.0), ("L3", 4.0)]
    assert conn.execute("SELECT COUNT(*) FROM right_to_win_scores").fetchone()[0] == 1


def test_first_creation_copies_the_current_scores_as_a_baseline(conn):
    a, b = _os(conn, "OS001"), _os(conn, "OS002")
    for os_id, total in ((a, 5.5), (b, 8.0)):
        conn.execute("INSERT INTO scores (opportunity_space_id, total_score, computed_at) VALUES (?, ?, 'then')",
                     (os_id, total))
    conn.commit()
    insert_score(conn, a, SUBS, 6.0, "e", "s")
    rows = get_score_history(conn)
    assert [(r["label"], r["total_score"], r["origin"]) for r in rows] == [
        ("OS001", 5.5, "baseline"), ("OS002", 8.0, "baseline"), ("OS001", 6.0, "write")]
    # Created once: a second call seeds nothing.
    assert ensure_history_tables(conn) == []
    assert len(get_score_history(conn)) == 3


def test_history_outlives_a_deleted_opportunity_space(conn):
    os_id = _os(conn)
    insert_score(conn, os_id, SUBS, 6.1, "e", "s")
    insert_right_to_win_score(conn, os_id, "L2", 5.0, "", "j")
    assert delete_opportunity_spaces(conn, ["OS001"]) == ["OS001"]
    assert conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 0
    assert len(get_score_history(conn, "OS001")) == 1
    assert len(get_score_history(conn, "OS001", kind="right_to_win")) == 1


def test_init_db_creates_the_history_on_a_new_database(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "radar.db"))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "radar.db"))
    db.init_db()
    c = db.get_connection()
    assert {"score_history", "right_to_win_history"} <= _tables(c)
    assert get_score_history(c) == []


def test_on_the_committed_database_reads_are_unchanged(tmp_path):
    """Run on a copy, so the test never writes to the tracked file."""
    source = ROOT / "radar.db"
    if not source.exists():
        pytest.skip("radar.db is not in this checkout")
    copy = tmp_path / "radar.db"
    shutil.copy(source, copy)
    c = sqlite3.connect(copy)
    c.row_factory = sqlite3.Row
    before = [tuple(r) for r in get_latest_scores(c)]
    n_scores = c.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
    n_rtw = c.execute("SELECT COUNT(*) FROM right_to_win_scores").fetchone()[0]
    assert sorted(ensure_history_tables(c)) == ["right_to_win_history", "score_history"]
    assert [tuple(r) for r in get_latest_scores(c)] == before
    assert len(get_score_history(c)) == n_scores
    assert len(get_score_history(c, kind="right_to_win")) == n_rtw
    assert {r["origin"] for r in get_score_history(c)} == {"baseline"}
