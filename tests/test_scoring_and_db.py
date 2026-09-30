import sqlite3
from datetime import datetime, timedelta, timezone
import pytest
from pipeline.scoring import (
    novelty_momentum,
    _urgency_weighted,
    urgency_score,
    compute_urgency_scaling_point,
    recalibrate_deterministic_scores,
    URGENCY_CAP,
)
from pipeline.db import (
    SCHEMA,
    get_latest_scores,
    delete_opportunity_spaces,
    get_unscored_opportunity_spaces,
    get_opportunity_spaces_missing_right_to_win,
    add_to_watchlist,
    insert_signal,
    link_signal_to_opportunity,
    get_linked_signals_for_opportunity_space,
)


def _signal(days_ago, now):
    return {"collected_at": (now - timedelta(days=days_ago)).isoformat()}


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    yield conn
    conn.close()


def _insert_opportunity_space(
    conn,
    label,
    vertical="Manufacturing",
    use_case="Energy Optimization",
    technology="IoT Platforms",
):
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO opportunity_spaces (label, run_id, vertical, use_case, technology, created_at) VALUES (?, 'test-run', ?, ?, ?, ?)",
        (label, vertical, use_case, technology, now),
    )
    conn.commit()
    return cur.lastrowid


def _insert_score(conn, os_id, total_score=7.0):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO scores (opportunity_space_id, market_signal_strength, source_diversity, evidence_quality, novelty_momentum, strategic_relevance, urgency_score, total_score, computed_at) VALUES (?, 5, 5, 5, 5, 5, 5, ?, ?)",
        (os_id, total_score, now),
    )
    conn.commit()


def _insert_right_to_win(conn, os_id, right_to_win_score=6.0):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO right_to_win_scores (opportunity_space_id, portfolio_distance, right_to_win_score, matched_assets, justification, computed_at) VALUES (?, 'L1', ?, 'API X', 'test', ?)",
        (os_id, right_to_win_score, now),
    )
    conn.commit()


class TestNoveltyMomentum:

    def test_empty_signals_returns_zero(self):
        assert novelty_momentum([]) == 0.0

    def test_fewer_than_three_signals_returns_neutral(self):
        now = datetime.now(timezone.utc)
        signals = [_signal(1, now), _signal(2, now)]
        assert novelty_momentum(signals) == 5.0

    def test_recent_burst_scores_higher_than_even_spread(self):
        now = datetime.now(timezone.utc)
        spread_even = [
            _signal(d, now) for d in (90, 80, 70, 60, 50, 40, 30, 20, 10, 5, 2, 1)
        ]
        burst_recent = [_signal(d, now) for d in (90, 3, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1)]
        spread_score = novelty_momentum(spread_even)
        burst_score = novelty_momentum(burst_recent)
        assert burst_score > spread_score

    def test_all_signals_stale_scores_low(self):
        now = datetime.now(timezone.utc)
        stale = [_signal(d, now) for d in (200, 201, 202, 203, 204, 205)]
        assert novelty_momentum(stale) < 2.0

    def test_malformed_date_is_skipped_not_fatal(self):
        now = datetime.now(timezone.utc)
        signals = [
            _signal(1, now),
            _signal(2, now),
            _signal(3, now),
            {"collected_at": "not-a-real-date"},
            {"collected_at": None},
        ]
        result = novelty_momentum(signals)
        assert isinstance(result, float)

    def test_clock_skew_returns_neutral(self):
        now = datetime.now(timezone.utc)
        future_signals = [_signal(-5, now), _signal(-4, now), _signal(-3, now)]
        assert novelty_momentum(future_signals) == 5.0


class TestGetLatestScores:

    def test_fully_scored_os_appears_with_both_scores(self, db_conn):
        os_id = _insert_opportunity_space(db_conn, "OS001")
        _insert_score(db_conn, os_id, total_score=7.5)
        _insert_right_to_win(db_conn, os_id, right_to_win_score=6.5)
        rows = get_latest_scores(db_conn)
        assert len(rows) == 1
        assert rows[0]["label"] == "OS001"
        assert rows[0]["total_score"] == 7.5
        assert rows[0]["right_to_win_score"] == 6.5

    def test_partially_scored_os_still_appears(self, db_conn):
        os_id = _insert_opportunity_space(db_conn, "OS002")
        _insert_score(db_conn, os_id, total_score=8.0)
        rows = get_latest_scores(db_conn)
        assert len(rows) == 1
        assert rows[0]["label"] == "OS002"
        assert rows[0]["total_score"] == 8.0
        assert rows[0]["right_to_win_score"] is None

    def test_mix_of_fully_and_partially_scored(self, db_conn):
        full_id = _insert_opportunity_space(db_conn, "OS003")
        _insert_score(db_conn, full_id, total_score=6.0)
        _insert_right_to_win(db_conn, full_id, right_to_win_score=4.0)
        partial_id = _insert_opportunity_space(db_conn, "OS004")
        _insert_score(db_conn, partial_id, total_score=9.0)
        rows = {r["label"]: r for r in get_latest_scores(db_conn)}
        assert set(rows.keys()) == {"OS003", "OS004"}
        assert rows["OS004"]["right_to_win_score"] is None

    def test_unscored_os_does_not_appear(self, db_conn):
        _insert_opportunity_space(db_conn, "OS005")
        rows = get_latest_scores(db_conn)
        assert rows == []


class TestDeleteOpportunitySpaces:

    def test_deletes_only_requested_labels(self, db_conn):
        keep_id = _insert_opportunity_space(db_conn, "OS013")
        drop1_id = _insert_opportunity_space(db_conn, "OS001")
        drop2_id = _insert_opportunity_space(db_conn, "OS024")
        for os_id in (keep_id, drop1_id, drop2_id):
            _insert_score(db_conn, os_id)
            _insert_right_to_win(db_conn, os_id)
        deleted = delete_opportunity_spaces(db_conn, ["OS001", "OS024"])
        assert set(deleted) == {"OS001", "OS024"}
        remaining = [
            r["label"] for r in db_conn.execute("SELECT label FROM opportunity_spaces")
        ]
        assert remaining == ["OS013"]

    def test_cleans_up_referencing_rows(self, db_conn):
        os_id = _insert_opportunity_space(db_conn, "OS006")
        _insert_score(db_conn, os_id)
        _insert_right_to_win(db_conn, os_id)
        delete_opportunity_spaces(db_conn, ["OS006"])
        assert (
            db_conn.execute(
                "SELECT COUNT(*) c FROM scores WHERE opportunity_space_id = ?", (os_id,)
            ).fetchone()["c"]
            == 0
        )
        assert (
            db_conn.execute(
                "SELECT COUNT(*) c FROM right_to_win_scores WHERE opportunity_space_id = ?",
                (os_id,),
            ).fetchone()["c"]
            == 0
        )

    def test_unknown_label_is_silently_skipped(self, db_conn):
        _insert_opportunity_space(db_conn, "OS007")
        deleted = delete_opportunity_spaces(db_conn, ["OS999"])
        assert deleted == []
        remaining = [
            r["label"] for r in db_conn.execute("SELECT label FROM opportunity_spaces")
        ]
        assert remaining == ["OS007"]


class TestGetOpportunitySpacesMissingRightToWin:

    def test_fully_scored_os_is_not_flagged(self, db_conn):
        os_id = _insert_opportunity_space(db_conn, "OS001")
        _insert_score(db_conn, os_id)
        _insert_right_to_win(db_conn, os_id)
        assert get_opportunity_spaces_missing_right_to_win(db_conn) == []

    def test_unscored_os_is_not_flagged_either(self, db_conn):
        _insert_opportunity_space(db_conn, "OS002")
        assert get_opportunity_spaces_missing_right_to_win(db_conn) == []

    def test_partially_scored_os_is_flagged(self, db_conn):
        os_id = _insert_opportunity_space(db_conn, "OS003")
        _insert_score(db_conn, os_id)
        flagged = get_opportunity_spaces_missing_right_to_win(db_conn)
        assert len(flagged) == 1
        assert flagged[0]["label"] == "OS003"

    def test_unscored_and_partially_scored_are_mutually_exclusive(self, db_conn):
        _insert_opportunity_space(db_conn, "OS004")
        partial_id = _insert_opportunity_space(db_conn, "OS005")
        _insert_score(db_conn, partial_id)
        done_id = _insert_opportunity_space(db_conn, "OS006")
        _insert_score(db_conn, done_id)
        _insert_right_to_win(db_conn, done_id)
        unscored_labels = {r["label"] for r in get_unscored_opportunity_spaces(db_conn)}
        partial_labels = {
            r["label"] for r in get_opportunity_spaces_missing_right_to_win(db_conn)
        }
        assert unscored_labels == {"OS004"}
        assert partial_labels == {"OS005"}
        assert unscored_labels & partial_labels == set()
        assert "OS006" not in unscored_labels and "OS006" not in partial_labels


def _signal_row(days_ago, now, signal_type="market_move"):
    return {
        "signal_type": signal_type,
        "collected_at": (now - timedelta(days=days_ago)).isoformat(),
    }


def _insert_and_link_signal(conn, os_id, url, signal_type, days_ago, now):
    published = (now - timedelta(days=days_ago)).isoformat()
    insert_signal(
        conn,
        source_name="Test Source",
        source_url=url,
        signal_type=signal_type,
        title="t",
        summary=None,
        published_date=published,
        vertical_hint="Manufacturing",
    )
    row = conn.execute("SELECT id FROM signals WHERE source_url = ?", (url,)).fetchone()
    link_signal_to_opportunity(conn, os_id, row["id"])


class TestUrgencyScalingPoint:

    def test_regulation_signal_contributes_full_weight(self):
        signals = [
            {
                "signal_type": "regulation",
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
        assert _urgency_weighted(signals) == 1.0

    def test_non_urgent_signal_type_contributes_nothing(self):
        signals = [
            _signal_row(1, datetime.now(timezone.utc), signal_type="market_move")
        ]
        assert _urgency_weighted(signals) == 0.0

    def test_scaling_point_never_exceeds_the_real_maximum(self, db_conn):
        now = datetime.now(timezone.utc)
        for i, n_regs in enumerate([0, 1, 2, 3, 10]):
            os_id = _insert_opportunity_space(db_conn, f"OS{i:03d}")
            for j in range(n_regs):
                _insert_and_link_signal(
                    db_conn, os_id, f"http://x/{i}/{j}", "regulation", 1, now
                )
            _insert_score(db_conn, os_id)
        db_conn.commit()
        scaling_point = compute_urgency_scaling_point(db_conn)
        assert (
            scaling_point <= 10.0
        ), "scaling point must never exceed the real observed maximum"

    def test_scaling_point_falls_back_to_static_cap_when_population_is_flat(
        self, db_conn
    ):
        os_id = _insert_opportunity_space(db_conn, "OS001")
        _insert_score(db_conn, os_id)
        db_conn.commit()
        assert compute_urgency_scaling_point(db_conn) == URGENCY_CAP

    def test_higher_weighted_signals_score_higher_urgency_at_a_fixed_scaling_point(
        self,
    ):
        now = datetime.now(timezone.utc)
        few = [{"signal_type": "regulation", "collected_at": now.isoformat()}]
        many = [
            {"signal_type": "regulation", "collected_at": now.isoformat()}
            for _ in range(5)
        ]
        assert urgency_score(many, scaling_point=6.0) > urgency_score(
            few, scaling_point=6.0
        )

    def test_urgency_score_is_capped_at_ten(self):
        now = datetime.now(timezone.utc)
        way_more_than_scaling_point = [
            {"signal_type": "regulation", "collected_at": now.isoformat()}
            for _ in range(50)
        ]
        assert urgency_score(way_more_than_scaling_point, scaling_point=6.0) == 10.0


class TestNoveltyInUrgency:

    def test_trending_os_scores_higher_urgency_than_flat_os_with_same_signal_count(
        self, db_conn
    ):
        now = datetime.now(timezone.utc)
        trending_id = _insert_opportunity_space(db_conn, "OS_TRENDING")
        for i, age in enumerate([90, 5, 3, 2, 1]):
            _insert_and_link_signal(
                db_conn, trending_id, f"http://t/{i}", "market_move", age, now
            )
        flat_id = _insert_opportunity_space(db_conn, "OS_FLAT")
        for i, age in enumerate([90, 72, 54, 36, 18]):
            _insert_and_link_signal(
                db_conn, flat_id, f"http://f/{i}", "market_move", age, now
            )
        db_conn.commit()
        trending_signals = get_linked_signals_for_opportunity_space(
            db_conn, trending_id
        )
        flat_signals = get_linked_signals_for_opportunity_space(db_conn, flat_id)
        assert _urgency_weighted(trending_signals) > _urgency_weighted(flat_signals)

    def test_novelty_contribution_requires_at_least_three_signals(self, db_conn):
        now = datetime.now(timezone.utc)
        os_id = _insert_opportunity_space(db_conn, "OS_SMALL")
        for i, age in enumerate([5, 1]):
            _insert_and_link_signal(
                db_conn, os_id, f"http://s/{i}", "market_move", age, now
            )
        db_conn.commit()
        signals = get_linked_signals_for_opportunity_space(db_conn, os_id)
        assert (
            _urgency_weighted(signals) == 0.0
        ), "2 non-urgent signals must contribute exactly 0 -- the novelty term must not fire below the 3-signal guard"

    def test_novelty_and_regulation_contributions_add_up(self, db_conn):
        now = datetime.now(timezone.utc)
        reg_plus_burst_id = _insert_opportunity_space(db_conn, "OS_REG_BURST")
        _insert_and_link_signal(
            db_conn, reg_plus_burst_id, "http://rb/reg", "regulation", 1, now
        )
        for i, age in enumerate([90, 5, 3, 2, 1]):
            _insert_and_link_signal(
                db_conn, reg_plus_burst_id, f"http://rb/{i}", "market_move", age, now
            )
        reg_plus_flat_id = _insert_opportunity_space(db_conn, "OS_REG_FLAT")
        _insert_and_link_signal(
            db_conn, reg_plus_flat_id, "http://rf/reg", "regulation", 1, now
        )
        for i, age in enumerate([90, 72, 54, 36, 18]):
            _insert_and_link_signal(
                db_conn, reg_plus_flat_id, f"http://rf/{i}", "market_move", age, now
            )
        db_conn.commit()
        burst_signals = get_linked_signals_for_opportunity_space(
            db_conn, reg_plus_burst_id
        )
        flat_signals = get_linked_signals_for_opportunity_space(
            db_conn, reg_plus_flat_id
        )
        assert _urgency_weighted(burst_signals) > _urgency_weighted(flat_signals)


class TestRefreshDeterministicScores:

    def test_score_moves_when_new_signals_are_linked(self, db_conn):
        now = datetime.now(timezone.utc)
        os_id = _insert_opportunity_space(db_conn, "OS001")
        for i in range(3):
            _insert_and_link_signal(
                db_conn, os_id, f"http://r/{i}", "market_move", 1, now
            )
        _insert_score(db_conn, os_id, total_score=5.9)
        db_conn.commit()
        before = get_latest_scores(db_conn)[0]
        for i in range(3, 53):
            _insert_and_link_signal(
                db_conn, os_id, f"http://r/{i}", "market_move", 1, now
            )
        db_conn.commit()
        recalibrate_deterministic_scores(db_conn)
        after = get_latest_scores(db_conn)[0]
        assert after["total_score"] != before["total_score"]

    def test_llm_fields_are_never_touched(self, db_conn):
        now = datetime.now(timezone.utc)
        os_id = _insert_opportunity_space(db_conn, "OS002")
        for i in range(3):
            _insert_and_link_signal(
                db_conn, os_id, f"http://l/{i}", "market_move", 1, now
            )
        _insert_score(db_conn, os_id)
        db_conn.commit()
        before = get_latest_scores(db_conn)[0]
        recalibrate_deterministic_scores(db_conn)
        after = get_latest_scores(db_conn)[0]
        assert after["evidence_quality"] == before["evidence_quality"]
        assert after["strategic_relevance"] == before["strategic_relevance"]

    def test_no_scored_os_is_a_no_op_not_a_crash(self, db_conn):
        recalibrate_deterministic_scores(db_conn)

    def test_updates_the_existing_row_rather_than_keeping_history(self, db_conn):
        # `scores` holds one row per opportunity space: insert_score() updates
        # it in place and clean_scores() prunes anything older, so a refresh
        # must not add a second row.
        now = datetime.now(timezone.utc)
        os_id = _insert_opportunity_space(db_conn, "OS003")
        for i in range(3):
            _insert_and_link_signal(
                db_conn, os_id, f"http://n/{i}", "market_move", 1, now
            )
        _insert_score(db_conn, os_id)
        db_conn.commit()
        before = get_latest_scores(db_conn)[0]
        recalibrate_deterministic_scores(db_conn)
        rows = db_conn.execute(
            "SELECT total_score FROM scores WHERE opportunity_space_id = ?", (os_id,)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["total_score"] != before["total_score"]


class TestAddToWatchlistNoneGuard:

    def test_none_term_is_skipped_not_a_crash(self, db_conn):
        add_to_watchlist(db_conn, None, "technology", "Natural Resources")
        rows = db_conn.execute("SELECT * FROM watchlist_terms").fetchall()
        assert rows == [], "a None term must not be inserted at all"

    def test_empty_string_term_is_also_skipped(self, db_conn):
        add_to_watchlist(db_conn, "", "use_case", "Energy")
        rows = db_conn.execute("SELECT * FROM watchlist_terms").fetchall()
        assert rows == []

    def test_normal_term_still_gets_inserted(self, db_conn):
        add_to_watchlist(
            db_conn, "Quantum Networking", "technology", "Natural Resources"
        )
        rows = db_conn.execute("SELECT * FROM watchlist_terms").fetchall()
        assert len(rows) == 1
        assert rows[0]["term"] == "Quantum Networking"

    def test_repeat_term_bumps_frequency_not_a_duplicate_row(self, db_conn):
        add_to_watchlist(
            db_conn, "Quantum Networking", "technology", "Natural Resources"
        )
        add_to_watchlist(
            db_conn, "Quantum Networking", "technology", "Natural Resources"
        )
        rows = db_conn.execute("SELECT * FROM watchlist_terms").fetchall()
        assert len(rows) == 1
        assert rows[0]["frequency"] == 2

class TestQuadrant:
    """The dashboard's overview counts and detail panel both use quadrant(),
    so these tests pin the single threshold and the no-gap property."""

    def test_every_pair_lands_in_exactly_one_quadrant(self):
        from pipeline.config import QUADRANTS, quadrant

        grid = [x / 2 for x in range(0, 21)]  # 0.0 .. 10.0 in 0.5 steps
        for a in grid:
            for w in grid:
                assert quadrant(a, w) in QUADRANTS

    def test_between_thresholds_is_not_a_gap(self):
        # Right-to-win in [7, 8) used to match no quadrant in the overview.
        from pipeline.config import quadrant

        assert quadrant(8.5, 7.5) == "strong"
        assert quadrant(6.9, 7.5) == "moderate_market"

    def test_threshold_is_inclusive_and_shared_by_both_axes(self):
        from pipeline.config import STRONG_THRESHOLD, quadrant

        t = STRONG_THRESHOLD
        assert quadrant(t, t) == "strong"
        assert quadrant(t, t - 0.1) == "needs_capability"
        assert quadrant(t - 0.1, t) == "moderate_market"
        assert quadrant(t - 0.1, t - 0.1) == "low_both"

    def test_missing_score_is_not_counted_as_low(self):
        from pipeline.config import quadrant

        assert quadrant(None, 5) is None
        assert quadrant(8, float("nan")) is None


class TestSummaryHeader:
    """The --top cut must not make scored opportunity spaces look unscored."""

    def test_top_cut_is_not_reported_as_unscored(self, db_conn, tmp_path):
        import radar_cli

        for i in range(3):
            os_id = _insert_opportunity_space(
                db_conn, f"OS00{i}", use_case=f"Use case {i}"
            )
            _insert_score(db_conn, os_id, total_score=5.0 + i)
            _insert_right_to_win(db_conn, os_id)
        out = tmp_path / "summary.md"
        radar_cli.cmd_summary(db_conn, output_path=str(out), top_n=1)
        text = out.read_text(encoding="utf-8")
        assert "3/3 opportunity spaces scored, top 1 by attractiveness shown" in text
        assert "not yet scored" not in text

    def test_really_unscored_os_is_still_reported(self, db_conn, tmp_path):
        import radar_cli

        os_id = _insert_opportunity_space(db_conn, "OS001")
        _insert_score(db_conn, os_id)
        _insert_right_to_win(db_conn, os_id)
        _insert_opportunity_space(db_conn, "OS002", use_case="Other")
        out = tmp_path / "summary.md"
        radar_cli.cmd_summary(db_conn, output_path=str(out))
        assert "1/2 opportunity spaces scored -- 1 not yet scored" in out.read_text(
            encoding="utf-8"
        )

    def test_sub_scores_are_shown_rounded(self, db_conn, tmp_path):
        import radar_cli

        os_id = _insert_opportunity_space(db_conn, "OS001")
        _insert_score(db_conn, os_id)
        db_conn.execute(
            "UPDATE scores SET market_signal_strength = ?, source_diversity = ?",
            (55 / 7, 10 / 3),
        )
        _insert_right_to_win(db_conn, os_id)
        out = tmp_path / "summary.md"
        radar_cli.cmd_summary(db_conn, output_path=str(out))
        text = out.read_text(encoding="utf-8")
        assert "- Market signal strength: 7.86" + chr(10) in text
        assert "- Source diversity: 3.33" + chr(10) in text

    def test_urgency_line_does_not_claim_a_flat_per_signal_bonus(
        self, db_conn, tmp_path
    ):
        """The old text said "+2 per regulation/buying_signal signal", which
        scoring.py never computed (weighted sum scaled to a percentile)."""
        import radar_cli

        os_id = _insert_opportunity_space(db_conn, "OS001")
        _insert_score(db_conn, os_id)
        _insert_right_to_win(db_conn, os_id)
        out = tmp_path / "summary.md"
        radar_cli.cmd_summary(db_conn, output_path=str(out))
        text = out.read_text(encoding="utf-8")
        assert "+2 per" not in text
        assert "95th percentile" in text


class TestGoogleNewsSourceName:
    """The TED fallback must store TED under the same name as the TED API."""

    def _feed(self):
        from types import SimpleNamespace

        entry = {
            "source": {"title": "ted.europa.eu"},
            "link": "https://ted.europa.eu/notice/1",
            "title": "Tender for network services",
            "summary": None,
            "published": "Mon, 01 Sep 2026 10:00:00 GMT",
        }
        return SimpleNamespace(entries=[entry])

    def test_override_replaces_publisher_name(self, db_conn, monkeypatch):
        from pipeline import ingest

        monkeypatch.setattr(ingest.feedparser, "parse", lambda url: self._feed())
        ingest.fetch_google_news(
            db_conn, "Retail", "q", source_name=ingest.TED_SOURCE_NAME
        )
        row = db_conn.execute("SELECT source_name FROM signals").fetchone()
        assert row["source_name"] == "TED - EU Public Procurement"

    def test_default_keeps_publisher_name(self, db_conn, monkeypatch):
        from pipeline import ingest

        monkeypatch.setattr(ingest.feedparser, "parse", lambda url: self._feed())
        ingest.fetch_google_news(db_conn, "Retail", "q")
        row = db_conn.execute("SELECT source_name FROM signals").fetchone()
        assert row["source_name"] == "ted.europa.eu"

class TestLinkNonTechSources:
    """link must drop old links to non-tech sources and never add new ones."""

    def test_existing_non_tech_link_is_removed(self, db_conn):
        import radar_cli

        os_id = _insert_opportunity_space(
            db_conn, "OS001", use_case="Fire detection", technology="Cloud"
        )
        for source, title in [
            ("Sports Illustrated", "Cloud of fire over the stadium"),
            ("Tech Monitor", "Cloud fire detection for factories"),
        ]:
            insert_signal(
                db_conn,
                source_name=source,
                source_url=f"https://example.com/{source}",
                signal_type="market_move",
                title=title,
                summary=None,
                published_date=None,
                vertical_hint="Manufacturing",
            )
        sport_id = db_conn.execute(
            "SELECT id FROM signals WHERE source_name = 'Sports Illustrated'"
        ).fetchone()["id"]
        link_signal_to_opportunity(db_conn, os_id, sport_id)
        radar_cli.cmd_link(db_conn)
        sources = [
            s["source_name"]
            for s in get_linked_signals_for_opportunity_space(db_conn, os_id)
        ]
        assert sources == ["Tech Monitor"]
