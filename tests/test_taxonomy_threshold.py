"""One taxonomy proposal threshold: the defaults and the pipeline's call agree.

generate_all_proposals() used to default to 3 while run_extend_taxonomy() called
it with 5. These tests pin every entry point to config.TAXONOMY_PROPOSAL_THRESHOLD
and check that the pipeline still proposes exactly the terms seen 5 times or
more. Temporary databases only.
"""
import inspect
import sqlite3

import pytest

from pipeline import config, db
from pipeline import extend_taxonomy as ext
from pipeline.config import TAXONOMY_PROPOSAL_THRESHOLD
from pipeline.db import SCHEMA

# Specific (non-generic) technology names, one per frequency.
TERMS = {3: "Digital Twin Platforms", 4: "Edge Inference Gateways",
         5: "Private 5G Slicing", 6: "Quantum Key Distribution"}


def _seed(conn):
    for freq, term in TERMS.items():
        conn.execute(
            "INSERT INTO watchlist_terms (term, category, vertical, frequency, first_seen, last_seen) "
            "VALUES (?, 'technology', 'Manufacturing', ?, '2026-09-01', '2026-09-30')", (term, freq))
    conn.commit()


def _proposed(conn):
    return sorted(r[0] for r in conn.execute("SELECT proposed_technology FROM proposals"))


def test_the_pipeline_value_is_kept():
    assert TAXONOMY_PROPOSAL_THRESHOLD == 5


def test_every_default_reads_the_constant():
    for fn in (ext.get_terms_reaching_threshold, ext.generate_all_proposals):
        assert inspect.signature(fn).parameters["threshold"].default == TAXONOMY_PROPOSAL_THRESHOLD


def test_default_call_proposes_the_same_terms_as_the_pipeline(tmp_path, monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed(conn)
    assert ext.generate_all_proposals(conn) == (2, 0)
    by_default = _proposed(conn)
    assert by_default == sorted([TERMS[5], TERMS[6]])

    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "radar.db"))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "radar.db"))
    db.init_db()
    seeded = db.get_connection()
    _seed(seeded)
    seeded.close()
    ext.run_extend_taxonomy(run_id="t")
    assert _proposed(db.get_connection()) == by_default


@pytest.mark.parametrize("threshold, expected", [(3, 4), (6, 1)])
def test_an_explicit_threshold_still_wins(threshold, expected):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed(conn)
    assert ext.generate_all_proposals(conn, threshold=threshold) == (expected, 0)
