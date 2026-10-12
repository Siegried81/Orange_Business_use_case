"""`radar_cli.py link` needs MIN_KEYWORD_OVERLAP shared keywords per link.

A single shared word linked a basketball player called Natasha Cloud to a cloud
opportunity space. These tests pin the minimum, the pruning of links that no
longer reach it, and the calibrate count that has to use the same minimum.
"""
import sqlite3
from datetime import datetime, timezone

import pytest

import radar_cli
from pipeline.db import (
    SCHEMA,
    get_linked_signals_for_opportunity_space,
    insert_signal,
    link_signal_to_opportunity,
)


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    yield conn
    conn.close()


def _space(conn, use_case="Cloud Infrastructure Modernization", technology="Cloud"):
    """One OS in Aerospace & Defense, the vertical the Natasha Cloud link sat in."""
    cur = conn.execute(
        "INSERT INTO opportunity_spaces (label, run_id, vertical, use_case, technology, created_at) "
        "VALUES ('OS001', 'test-run', 'Aerospace & Defense', ?, ?, ?)",
        (use_case, technology, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def _signal(conn, title, source="Tech Monitor"):
    insert_signal(
        conn,
        source_name=source,
        source_url=f"https://example.com/{abs(hash(title))}",
        signal_type="market_move",
        title=title,
        summary=None,
        published_date=None,
        vertical_hint="Aerospace & Defense",
    )
    return conn.execute("SELECT id FROM signals WHERE title = ?", (title,)).fetchone()["id"]


def _linked_titles(conn, os_id):
    return sorted(s["title"] for s in get_linked_signals_for_opportunity_space(conn, os_id))


def test_the_minimum_is_two():
    assert radar_cli.MIN_KEYWORD_OVERLAP == 2


def test_one_shared_keyword_is_not_enough(db_conn):
    os_id = _space(db_conn)
    _signal(db_conn, "Natasha Cloud speaks out after the game", source="Idaho Statesman")
    radar_cli.cmd_link(db_conn)
    assert _linked_titles(db_conn, os_id) == []


def test_two_shared_keywords_link(db_conn):
    os_id = _space(db_conn)
    _signal(db_conn, "Defence ministry plans cloud infrastructure overhaul")
    radar_cli.cmd_link(db_conn)
    assert _linked_titles(db_conn, os_id) == ["Defence ministry plans cloud infrastructure overhaul"]


def test_repeated_keyword_counts_once(db_conn):
    os_id = _space(db_conn)
    _signal(db_conn, "Cloud, cloud and more cloud")
    radar_cli.cmd_link(db_conn)
    assert _linked_titles(db_conn, os_id) == []


def test_existing_one_keyword_link_is_pruned_and_strong_link_kept(db_conn):
    os_id = _space(db_conn)
    weak = _signal(db_conn, "Natasha Cloud speaks out after the game", source="Idaho Statesman")
    strong = _signal(db_conn, "Defence ministry plans cloud infrastructure overhaul")
    link_signal_to_opportunity(db_conn, os_id, weak)
    link_signal_to_opportunity(db_conn, os_id, strong)
    radar_cli.cmd_link(db_conn)
    assert _linked_titles(db_conn, os_id) == ["Defence ministry plans cloud infrastructure overhaul"]


def test_pruning_only_touches_the_os_the_link_is_weak_for(db_conn):
    cloud_os = _space(db_conn)
    other_os = db_conn.execute(
        "INSERT INTO opportunity_spaces (label, run_id, vertical, use_case, technology, created_at) "
        "VALUES ('OS002', 'test-run', 'Aerospace & Defense', 'Natasha Speaks', 'Game Analytics', ?)",
        (datetime.now(timezone.utc).isoformat(),),
    ).lastrowid
    db_conn.commit()
    sid = _signal(db_conn, "Natasha Cloud speaks out after the game", source="Idaho Statesman")
    link_signal_to_opportunity(db_conn, cloud_os, sid)
    link_signal_to_opportunity(db_conn, other_os, sid)
    radar_cli.cmd_link(db_conn)
    assert _linked_titles(db_conn, cloud_os) == []
    assert _linked_titles(db_conn, other_os) == ["Natasha Cloud speaks out after the game"]


def test_calibrate_counts_with_the_same_minimum(db_conn, capsys):
    _space(db_conn)
    _signal(db_conn, "Natasha Cloud speaks out after the game", source="Idaho Statesman")
    _signal(db_conn, "Defence ministry plans cloud infrastructure overhaul")
    radar_cli.cmd_calibrate(db_conn)
    out = capsys.readouterr().out
    assert "Signals sharing >= 2 keywords" in out
    assert "Across 1 OS: median=1 " in out
