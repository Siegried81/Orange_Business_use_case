"""Foreign keys are enforced, and a database left pointing at a renamed parent is repaired.

The committed radar.db had every row of the three child tables counted as a
foreign-key violation while every id was valid: a past migration renamed
opportunity_spaces, SQLite rewrote the children's REFERENCES to the new name,
and the old table was then dropped. These tests rebuild that state in memory,
repair it, and check the committed database once repaired has nothing left.
"""

import shutil
import sqlite3
from pathlib import Path

import pytest

from pipeline import config, db
from pipeline.db import SCHEMA, repair_foreign_key_targets

ROOT = Path(__file__).resolve().parents[1]


def _broken_db():
    """Replay the migration that broke the references: rename, recreate, drop."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO opportunity_spaces (id, label, vertical, use_case, technology, created_at) "
        "VALUES (1, 'OS001', 'Manufacturing', 'Energy Optimization', 'IoT Platforms', 'now')"
    )
    conn.execute("INSERT INTO signals (id, source_name, signal_type, title, collected_at) VALUES (7, 's', 'news', 't', 'now')")
    conn.execute("INSERT INTO opportunity_signals VALUES (1, 7)")
    conn.execute("INSERT INTO scores (opportunity_space_id, total_score, computed_at) VALUES (1, 5.5, 'now')")
    conn.execute("INSERT INTO right_to_win_scores (opportunity_space_id, right_to_win_score, computed_at) VALUES (1, 4.0, 'now')")
    conn.execute("ALTER TABLE opportunity_spaces RENAME TO opportunity_spaces_old")  # rewrites the children's REFERENCES
    conn.executescript(SCHEMA)  # a fresh opportunity_spaces
    conn.execute("INSERT INTO opportunity_spaces SELECT * FROM opportunity_spaces_old")
    conn.execute("DROP TABLE opportunity_spaces_old")
    conn.commit()
    return conn


def test_the_replayed_migration_reproduces_the_break():
    conn = _broken_db()
    assert len(conn.execute("PRAGMA foreign_key_check").fetchall()) == 3
    assert db._dangling_fk_targets(conn, "scores") == ["opportunity_spaces_old"]


def test_repair_rebuilds_the_children_and_keeps_every_row():
    conn = _broken_db()
    assert sorted(repair_foreign_key_targets(conn)) == ["opportunity_signals", "right_to_win_scores", "scores"]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("SELECT total_score FROM scores").fetchone()[0] == 5.5
    assert conn.execute("SELECT COUNT(*) FROM opportunity_signals").fetchone()[0] == 1
    assert db._dangling_fk_targets(conn, "scores") == []
    # Idempotent: a healthy database is left alone.
    assert repair_foreign_key_targets(conn) == []


def test_foreign_keys_are_enforced_on_every_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "radar.db"))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "radar.db"))
    db.init_db()
    conn = db.get_connection()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO scores (opportunity_space_id, computed_at) VALUES (999, 'now')")


def test_the_committed_database_has_no_violation_left(tmp_path):
    """Run on a copy, so the test never writes to the tracked file."""
    source = ROOT / "radar.db"
    if not source.exists():
        pytest.skip("radar.db is not in this checkout")
    copy = tmp_path / "radar.db"
    shutil.copy(source, copy)
    conn = sqlite3.connect(copy)
    repair_foreign_key_targets(conn)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
