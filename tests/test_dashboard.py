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

    def test_strategic_position_is_rendered(self, ran_app):
        markdown = " ".join(str(m.value) for m in ran_app.markdown)
        assert "Strategic position" in markdown
        assert "Matched assets" in markdown
