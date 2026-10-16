"""Tests for app/streamlit_app.py, the read-only Streamlit dashboard.

What is worth testing here is not the layout but the four things a reader of
the page relies on: that a missing radar.db is explained instead of dumped as a
traceback, that the AI-generated nature of the scores is disclosed (AI Act),
that every tab holds the content it is labelled with, and that a missing or
unusable value degrades to a dash instead of an exception.

The dashboard is a script, not a library, so it is driven through Streamlit's
own AppTest harness; the two pure helpers are taken from a bare import of the
same file. Both read the tracked radar.db and never touch the network.
"""

import importlib.util
import os

import pandas as pd
import pytest

import pipeline.db as pipeline_db

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP_PATH = os.path.join(_REPO_ROOT, "app", "streamlit_app.py")

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="module")
def dashboard():
    """The dashboard's module globals, for the helpers that are pure functions.

    Importing the file runs the whole script in Streamlit's "bare mode" (no
    script run context), which is noisy but harmless and the only way to reach
    a function defined in a Streamlit entry-point script.
    """
    spec = importlib.util.spec_from_file_location("dashboard_under_test", _APP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ran_app():
    """One full dashboard run against the tracked radar.db."""
    app = AppTest.from_file(_APP_PATH, default_timeout=120)
    app.run()
    assert not app.exception, f"dashboard raised: {app.exception}"
    return app


class TestIsLinkableUrl:
    """Signals come from feeds and tender exports, so source_url is not always
    a web address; only http(s) may become a clickable link."""

    def test_http_and_https_are_linkable(self, dashboard):
        assert dashboard.is_linkable_url("http://example.com/a")
        assert dashboard.is_linkable_url("https://example.com/a")

    def test_leading_whitespace_is_tolerated(self, dashboard):
        assert dashboard.is_linkable_url("  https://example.com/a ")

    def test_uppercase_scheme_is_linkable(self, dashboard):
        assert dashboard.is_linkable_url("HTTPS://example.com/a")

    def test_other_schemes_and_paths_are_not_linkable(self, dashboard):
        assert not dashboard.is_linkable_url("javascript:alert(1)")
        assert not dashboard.is_linkable_url("mailto:someone@example.com")
        assert not dashboard.is_linkable_url("ftp://example.com/a")
        assert not dashboard.is_linkable_url("/local/export/ted.xml")
        assert not dashboard.is_linkable_url("TED reference 2026/S 123-456")

    def test_missing_url_is_not_linkable(self, dashboard):
        assert not dashboard.is_linkable_url(None)
        assert not dashboard.is_linkable_url("")
        assert not dashboard.is_linkable_url(float("nan"))


class TestTopOpportunityLabel:
    """idxmax() raises on an all-NaN column, which the filters can select."""

    def test_returns_the_highest_scoring_label(self, dashboard):
        frame = pd.DataFrame(
            {"label": ["OS001", "OS002", "OS003"], "total_score": [5.0, 8.1, 7.2]}
        )
        assert dashboard.top_opportunity_label(frame) == "OS002"

    def test_ignores_missing_scores(self, dashboard):
        frame = pd.DataFrame(
            {"label": ["OS001", "OS002"], "total_score": [None, 4.0]}
        )
        assert dashboard.top_opportunity_label(frame) == "OS002"

    def test_all_nan_selection_returns_none_instead_of_raising(self, dashboard):
        frame = pd.DataFrame(
            {"label": ["OS001", "OS002"], "total_score": [None, None]}
        )
        assert dashboard.top_opportunity_label(frame) is None

    def test_space_without_right_to_win_cannot_be_the_top(self, dashboard):
        frame = pd.DataFrame(
            {"label": ["OS001", "OS002"], "total_score": [9.9, 6.0], "right_to_win_score": [None, 7.0]}
        )
        ranked = frame.assign(overall=frame["total_score"] + frame["right_to_win_score"])
        assert dashboard.top_opportunity_label(ranked, score_column="overall") == "OS002"

    def test_empty_selection_returns_none(self, dashboard):
        frame = pd.DataFrame({"label": [], "total_score": []})
        assert dashboard.top_opportunity_label(frame) is None


class TestAiActDisclosure:
    """The page presents LLM output as if it were measured, so it must say so
    where the reader cannot miss it."""

    def test_disclosure_names_what_is_generated(self, dashboard):
        text = dashboard.AI_DISCLOSURE.lower()
        assert "language model" in text
        assert "justification" in text
        assert "review" in text

    def test_disclosure_is_rendered_on_the_page(self, ran_app):
        shown = [w.value for w in ran_app.warning] + [
            c.value for c in ran_app.caption
        ]
        assert any("AI-generated content" in str(v) for v in shown)


class TestMissingDatabaseIsExplained:
    """sqlite3.connect() creates an empty file when radar.db is absent, so the
    first query raised "no such table" as a traceback in the browser."""

    def test_empty_database_shows_the_pipeline_hint(self, tmp_path, monkeypatch):
        import streamlit as st

        monkeypatch.setattr(pipeline_db, "DB_PATH", str(tmp_path / "radar.db"))
        st.cache_data.clear()
        app = AppTest.from_file(_APP_PATH, default_timeout=120)
        app.run()
        st.cache_data.clear()
        assert not app.exception, f"dashboard raised instead of reporting: {app.exception}"
        errors = " ".join(str(e.value) for e in app.error)
        assert "radar.db" in errors
        assert "pipeline" in errors


class TestTabsHoldTheirOwnContent:
    """`tab_evidence` was opened twice, so "Strategic position" rendered in the
    right-to-win tab instead of its own and nothing ever reached the third
    block. Each tab label must appear exactly once."""

    def test_each_tab_context_is_opened_once(self):
        with open(_APP_PATH, encoding="utf-8") as handle:
            source = handle.read()
        for tab in ("tab_score", "tab_evidence", "tab_signals"):
            assert source.count(f"with {tab}:") == 1, f"{tab} is opened more than once"

    def test_overview_counts_the_spaces_with_no_evidence(self, ran_app):
        captions = " ".join(str(c.value) for c in ran_app.caption)
        assert "Not enough evidence:" in captions

    def test_strategic_position_is_rendered(self, ran_app):
        markdown = " ".join(str(m.value) for m in ran_app.markdown)
        assert "Strategic position" in markdown
        assert "Matched assets" in markdown


class TestChartClickSync:
    """Both charts keep their last selection across reruns and are synced in
    order (radar, then bubble), so a stale bubble pick used to override every
    radar click and every selectbox change. Only a new click may count."""

    LABELS = {"OS001": "OS001 — a x b", "OS002": "OS002 — c x d"}

    @staticmethod
    def _click(label):
        return {"selection": {"points": [{"customdata": label}]}}

    @pytest.fixture
    def fake_st(self, dashboard, monkeypatch):
        class FakeSt:
            session_state = {}
            query_params = {}

        monkeypatch.setattr(dashboard, "st", FakeSt)
        return FakeSt

    def test_radar_click_wins_over_a_stale_bubble_selection(self, dashboard, fake_st):
        sync = dashboard.sync_selection_from_click
        sync(None, self.LABELS, "radar_chart")
        sync(self._click("OS001"), self.LABELS, "bubble_chart")
        # Next run: the radar is clicked, the bubble still reports OS001.
        sync(self._click("OS002"), self.LABELS, "radar_chart")
        sync(self._click("OS001"), self.LABELS, "bubble_chart")
        assert fake_st.session_state["os_selectbox"] == self.LABELS["OS002"]
        assert fake_st.query_params["topic"] == "OS002"

    def test_selectbox_change_is_not_undone_by_an_old_click(self, dashboard, fake_st):
        sync = dashboard.sync_selection_from_click
        sync(self._click("OS001"), self.LABELS, "bubble_chart")
        fake_st.session_state["os_selectbox"] = self.LABELS["OS002"]
        sync(self._click("OS001"), self.LABELS, "bubble_chart")
        assert fake_st.session_state["os_selectbox"] == self.LABELS["OS002"]

    def test_clicking_the_same_point_again_after_clearing_reselects_it(self, dashboard, fake_st):
        sync = dashboard.sync_selection_from_click
        sync(self._click("OS001"), self.LABELS, "radar_chart")
        sync({"selection": {"points": []}}, self.LABELS, "radar_chart")
        fake_st.session_state["os_selectbox"] = self.LABELS["OS002"]
        sync(self._click("OS001"), self.LABELS, "radar_chart")
        assert fake_st.session_state["os_selectbox"] == self.LABELS["OS001"]


class TestPageOpening:
    """What a first-time visitor (no ?topic= link) sees before any click."""

    def test_opens_on_the_best_space_on_both_axes(self, ran_app, dashboard):
        frame, _, _ = dashboard.load_scores()
        overall = frame["total_score"] + frame["right_to_win_score"]
        best = frame.loc[overall.idxmax(), "label"]
        # Only meaningful while the two rankings disagree on radar.db.
        assert best != frame.loc[frame["total_score"].idxmax(), "label"]
        assert ran_app.selectbox(key="os_selectbox").value.startswith(f"{best} —")
        top_metric = next(m for m in ran_app.metric if m.label == "Top Opportunity")
        assert top_metric.value == best

    def test_caption_dates_the_signals_and_the_scores(self, ran_app, dashboard):
        collected, scored = dashboard.data_dates()
        captions = " ".join(str(c.value) for c in ran_app.caption)
        assert f"signals collected up to {collected.day} {collected:%b %Y}" in captions
        assert f"scored {scored.day} {scored:%b %Y}" in captions

    def test_pipeline_growth_section_is_rendered(self, ran_app):
        subheaders = " ".join(str(h.value) for h in ran_app.subheader)
        assert "Pipeline growth" in subheaders
        metrics = {m.label: m.value for m in ran_app.metric}
        conn = pipeline_db.get_connection()
        try:
            pending = len(conn.execute("SELECT 1 FROM proposals WHERE status = 'pending'").fetchall())
            below = len(conn.execute("SELECT 1 FROM watchlist_terms WHERE status = 'pending'").fetchall())
        finally:
            conn.close()
        assert metrics["Proposals pending review"] == str(pending)
        captions = " ".join(str(c.value) for c in ran_app.caption)
        # "proposed" terms already crossed the threshold and must not be counted.
        assert f"{below} term(s) on the watchlist, still below" in captions
