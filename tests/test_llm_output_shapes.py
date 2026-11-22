"""What the pipeline does with LLM answers that are valid JSON but the wrong shape.

A null justification, a string where a list of assets was asked for, a list
where an object was asked for, or a horizon outside the closed list each used
to raise or to store a value the dashboard then mishandled. Every LLM call is
stubbed; nothing reaches the network.
"""
import sqlite3

import pytest

from llm import llm_client
from pipeline import analyze, config, scoring
from pipeline.config import GEOS, normalize_geography
from pipeline.db import SCHEMA, migrate_schema

FAKE_SIGNALS = [{"source_name": "Test Source", "title": "A test signal title"}]


def _answer(monkeypatch, payload):
    monkeypatch.setattr(scoring, "get_llm_json", lambda *a, **k: payload)


class TestGetLlmJson:
    def test_a_null_content_is_no_answer_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(llm_client, "get_llm_response", lambda *a, **k: None)
        assert llm_client.get_llm_json("q") is None

    @pytest.mark.parametrize("raw", ["[1, 2]", "7", '"text"', "null"])
    def test_a_json_value_that_is_not_an_object_is_no_answer(self, monkeypatch, raw):
        monkeypatch.setattr(llm_client, "get_llm_response", lambda *a, **k: raw)
        assert llm_client.get_llm_json("q") is None

    def test_a_fenced_object_still_parses(self, monkeypatch):
        monkeypatch.setattr(
            llm_client, "get_llm_response", lambda *a, **k: '```json\n{"score": 6}\n```'
        )
        assert llm_client.get_llm_json("q") == {"score": 6}


class TestNullJustification:
    def test_evidence_quality(self, monkeypatch):
        _answer(monkeypatch, {"score": 6, "justification": None})
        assert scoring.llm_evidence_quality(FAKE_SIGNALS) == (6.0, "")

    def test_strategic_relevance(self, monkeypatch):
        _answer(monkeypatch, {"score": 6, "justification": None})
        score, justification = scoring.llm_strategic_relevance("Retail", "u", "t", FAKE_SIGNALS)
        assert score == 6.0
        assert "None" not in justification

    def test_right_to_win(self, monkeypatch):
        _answer(monkeypatch, {"portfolio_distance": "L1", "right_to_win_score": 5,
                              "matched_assets": [], "justification": None})
        distance, _, _, justification = scoring.llm_right_to_win("Energy", "u", "t")
        assert distance == "L1"
        assert "None" not in justification


class TestMatchedAssets:
    @pytest.mark.parametrize("matched, expected", [
        (None, ""),
        ([], ""),
        ("API Cloud Avenue", "API Cloud Avenue"),
        (["API Cloud Avenue", "API Mobile"], "API Cloud Avenue, API Mobile"),
    ])
    def test_any_shape_gives_a_comma_separated_list(self, monkeypatch, matched, expected):
        _answer(monkeypatch, {"portfolio_distance": "L1", "right_to_win_score": 5,
                              "matched_assets": matched, "justification": "j"})
        assert scoring.llm_right_to_win("Energy", "u", "t")[2] == expected


class TestClosedListsOfTheEnrichment:
    def _enrich(self, monkeypatch, **overrides):
        answer = {"role": "Sales", "buyer_persona": "CIOs", "geography": ["Benelux"],
                  "horizon": "Next", "domain": "Cloud",
                  "next_action_strategist": "a", "next_action_sales": "b",
                  "next_action_presales": "c"}
        answer.update(overrides)
        _answer(monkeypatch, answer)
        return scoring.llm_enrich("Retail", "u", "t", FAKE_SIGNALS)

    def test_a_valid_answer_is_kept(self, monkeypatch):
        result = self._enrich(monkeypatch)
        assert (result["role"], result["buyer_persona"], result["horizon"]) == ("Sales", "CIOs", "Next")

    @pytest.mark.parametrize("field, bad", [
        ("horizon", "Soon"), ("horizon", "now"), ("role", "Manager"), ("buyer_persona", "The CEO"),
    ])
    def test_an_answer_off_its_list_is_stored_as_none(self, monkeypatch, field, bad):
        assert self._enrich(monkeypatch, **{field: bad})[field] is None

    def test_countries_in_parentheses_are_reduced_to_regions(self, monkeypatch):
        result = self._enrich(monkeypatch, geography=[
            "Benelux (Netherlands, Belgium, Luxembourg)", "UK & Ireland (United Kingdom, Ireland)"])
        assert result["geography"] == "Benelux, UK & Ireland"

    def test_a_geography_with_no_known_region_is_none(self, monkeypatch):
        assert self._enrich(monkeypatch, geography=["Atlantis"])["geography"] is None


class TestNormalizeGeography:
    @pytest.mark.parametrize("raw, expected", [
        ("Benelux (Netherlands, Belgium, Luxembourg), Germany (Germany), "
         "UK & Ireland (United Kingdom, Ireland)", "Benelux, Germany, UK & Ireland"),
        ("Southern Europe (Italy, Spain, Portugal, Israel)", "Southern Europe"),
        ("Germany, United Kingdom, Ireland", "Germany, UK & Ireland"),
        (["DACH", "dach", "Nordics"], "DACH, Nordics"),
        ("Benelux", "Benelux"),
        ("", None),
        (None, None),
        ([], None),
    ])
    def test_stored_and_fresh_answers(self, raw, expected):
        assert normalize_geography(raw) == expected

    def test_every_region_is_known_to_the_prompt_and_to_the_filter(self):
        for region in GEOS:
            assert normalize_geography(region) == region
            assert region in config.GEOS_PROMPT

    def test_the_committed_database_holds_only_regions_once_normalized(self):
        conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
        try:
            stored = [r[0] for r in conn.execute("SELECT geography FROM opportunity_spaces")]
        finally:
            conn.close()
        for cell in stored:
            normalized = normalize_geography(cell)
            assert normalized is None or set(normalized.split(", ")) <= set(GEOS), cell


class TestEnrichmentIsStoredNormalized:
    def test_recalibrate_geography_stores_regions(self, monkeypatch):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        migrate_schema(conn)
        conn.execute(
            "INSERT INTO opportunity_spaces (label, run_id, vertical, use_case, technology, created_at) "
            "VALUES ('OS001', 'r', 'Retail', 'u', 't', 'now')")
        conn.execute("INSERT INTO scores (opportunity_space_id, total_score, computed_at) VALUES (1, 5, 'now')")
        _answer(monkeypatch, {"role": "Sales", "geography": ["DACH (Switzerland, Austria)"],
                              "horizon": "Now", "domain": "Cloud"})
        scoring.recalibrate_geography(conn)
        row = conn.execute("SELECT geography, horizon FROM opportunity_spaces").fetchone()
        assert (row["geography"], row["horizon"]) == ("DACH", "Now")


def test_portfolio_levels_come_from_the_config():
    assert scoring.PORTFOLIO_DISTANCE_LEVELS == tuple(config.PORTFOLIO_DISTANCE)
    assert scoring.PORTFOLIO_DISTANCE_LEVELS == ("L0", "L1", "L2", "L3", "L4")


class TestThemeExtractionNulls:
    def test_null_themes_and_candidates_are_an_empty_answer(self, monkeypatch):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        for i in range(4):
            conn.execute(
                "INSERT INTO signals (source_name, signal_type, title, collected_at, vertical_hint) "
                "VALUES ('s', 'trend', ?, 'now', 'Retail')", (f"title {i}",))
        monkeypatch.setattr(analyze, "_call_theme_extraction_llm",
                            lambda *a, **k: {"themes": None, "watchlist_candidates": None})
        assert analyze.extract_themes(conn, "Retail") == ([], [])
