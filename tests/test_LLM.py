import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.analyze import _classify_themes
from pipeline.taxonomy_validation import is_generic_taxonomy_term
from pipeline.extend_taxonomy import generate_proposal
from pipeline import scoring


class TestClassifyThemes:

    def test_fully_valid_theme_passes_through_unchanged(self):
        from pipeline.config import USE_CASES_TAXONOMY, TECHNOLOGIES_TAXONOMY

        theme = {
            "use_case": USE_CASES_TAXONOMY[0],
            "technology": TECHNOLOGIES_TAXONOMY[0],
            "supporting_signal_count": 5,
            "rationale": "x",
        }
        valid, watchlist, skipped = _classify_themes([theme], [])
        assert valid == [theme]
        assert watchlist == []
        assert skipped == []

    def test_missing_technology_key_entirely(self):
        from pipeline.config import USE_CASES_TAXONOMY

        theme = {"use_case": USE_CASES_TAXONOMY[0]}
        valid, watchlist, skipped = _classify_themes([theme], [])
        assert valid == []
        assert ("unknown", "technology") in watchlist

    def test_technology_present_but_null(self):
        from pipeline.config import USE_CASES_TAXONOMY

        theme = {"use_case": USE_CASES_TAXONOMY[0], "technology": None}
        valid, watchlist, skipped = _classify_themes([theme], [])
        assert valid == []
        assert ("unknown", "technology") in watchlist
        assert all((term is not None for term, _ in watchlist))

    def test_use_case_also_missing(self):
        theme = {"technology": None}
        valid, watchlist, skipped = _classify_themes([theme], [])
        assert valid == []
        assert ("unknown", "use_case") in watchlist
        assert ("unknown", "technology") in watchlist

    def test_bare_generic_technology_is_skipped_not_watchlisted(self):
        from pipeline.config import USE_CASES_TAXONOMY

        theme = {"use_case": USE_CASES_TAXONOMY[0], "technology": "AI"}
        valid, watchlist, skipped = _classify_themes([theme], [])
        assert ("AI", "technology") in skipped
        assert ("AI", "technology") not in watchlist

    def test_specific_ai_compound_term_is_not_filtered(self):
        from pipeline.config import USE_CASES_TAXONOMY, TECHNOLOGIES_TAXONOMY

        assert "Generative AI" in TECHNOLOGIES_TAXONOMY
        theme = {"use_case": USE_CASES_TAXONOMY[0], "technology": "Generative AI"}
        valid, watchlist, skipped = _classify_themes([theme], [])
        assert valid == [theme]
        assert skipped == []

    def test_explicit_watchlist_candidate_with_missing_term(self):
        candidates = [
            {"category": "technology"},
            {"term": "Something", "category": "nonsense"},
            {"term": "Quantum Networking", "category": "technology"},
        ]
        valid, watchlist, skipped = _classify_themes([], candidates)
        assert ("Quantum Networking", "technology") in watchlist
        assert len(watchlist) == 1

    def test_empty_themes_and_candidates(self):
        valid, watchlist, skipped = _classify_themes([], [])
        assert valid == [] and watchlist == [] and (skipped == [])


class TestIsGenericTaxonomyTerm:

    def test_bare_ai_uppercase(self):
        assert is_generic_taxonomy_term("AI", "technology") is True

    def test_bare_ai_lowercase(self):
        assert is_generic_taxonomy_term("ai", "technology") is True

    def test_whitespace_padded(self):
        assert is_generic_taxonomy_term("  ai  ", "technology") is True

    def test_compound_term_not_generic(self):
        assert is_generic_taxonomy_term("Generative AI", "technology") is False

    def test_use_case_category_never_flagged(self):
        assert is_generic_taxonomy_term("AI", "use_case") is False

    def test_non_string_term_does_not_crash(self):
        assert is_generic_taxonomy_term(None, "technology") is False
        assert is_generic_taxonomy_term(123, "technology") is False


class TestGenerateProposalGenericGuard:

    def test_generic_term_skipped_before_touching_the_db(self):
        term_row = {
            "vertical": "Manufacturing",
            "category": "technology",
            "term": "AI",
            "frequency": 10,
            "first_seen": "2026-01-01",
            "last_seen": "2026-01-02",
        }
        result = generate_proposal(conn=None, term=term_row)
        assert result == "skipped"


FAKE_SIGNALS = [{"source_name": "Test Source", "title": "A test signal title"}]


class TestLlmEvidenceQualityFallback:

    def test_no_signals_at_all(self):
        score, justification = scoring.llm_evidence_quality([])
        assert score == 0.0
        assert "No signals" in justification

    def test_llm_call_returns_none(self, monkeypatch):
        monkeypatch.setattr(scoring, "get_llm_json", lambda *a, **k: None)
        score, justification = scoring.llm_evidence_quality(FAKE_SIGNALS)
        assert score == 5.0
        assert "unavailable" in justification

    def test_llm_returns_dict_missing_score_key(self, monkeypatch):
        monkeypatch.setattr(
            scoring, "get_llm_json", lambda *a, **k: {"justification": "x"}
        )
        score, justification = scoring.llm_evidence_quality(FAKE_SIGNALS)
        assert score == 5.0
        assert "unavailable" in justification


class TestLlmStrategicRelevanceFallback:

    def test_llm_call_returns_none(self, monkeypatch):
        monkeypatch.setattr(scoring, "get_llm_json", lambda *a, **k: None)
        score, justification = scoring.llm_strategic_relevance(
            "Manufacturing", "Energy Optimization", "IoT Platforms", FAKE_SIGNALS
        )
        assert score == 5.0
        assert "unavailable" in justification

    def test_llm_returns_empty_dict(self, monkeypatch):
        monkeypatch.setattr(scoring, "get_llm_json", lambda *a, **k: {})
        score, justification = scoring.llm_strategic_relevance(
            "Manufacturing", "Energy Optimization", "IoT Platforms", FAKE_SIGNALS
        )
        assert score == 5.0
        assert "unavailable" in justification


class TestLlmRightToWinFallback:

    def test_llm_call_returns_none(self, monkeypatch):
        monkeypatch.setattr(scoring, "get_llm_json", lambda *a, **k: None)
        distance, score, assets, justification = scoring.llm_right_to_win(
            "Manufacturing", "Energy Optimization", "IoT Platforms"
        )
        assert distance == "L4"
        assert score == 0.0
        assert assets == ""
        assert "unavailable" in justification

    def test_llm_returns_dict_missing_portfolio_distance_key(self, monkeypatch):
        monkeypatch.setattr(
            scoring, "get_llm_json", lambda *a, **k: {"right_to_win_score": 8}
        )
        distance, score, assets, justification = scoring.llm_right_to_win(
            "Manufacturing", "Energy Optimization", "IoT Platforms"
        )
        assert distance == "L4"
        assert score == 0.0


class TestLlmEnrichFallback:

    def test_llm_call_returns_none(self, monkeypatch):
        monkeypatch.setattr(scoring, "get_llm_json", lambda *a, **k: None)
        result = scoring.llm_enrich(
            "Manufacturing", "Energy Optimization", "IoT Platforms", FAKE_SIGNALS
        )
        assert result["role"] is None
        assert result["geography"] is None
        assert result["horizon"] == "Later"
        assert "unavailable" in result["next_action_sales"]

    def test_llm_returns_dict_missing_role_key(self, monkeypatch):
        monkeypatch.setattr(
            scoring, "get_llm_json", lambda *a, **k: {"geography": ["Benelux"]}
        )
        result = scoring.llm_enrich(
            "Manufacturing", "Energy Optimization", "IoT Platforms", FAKE_SIGNALS
        )
        assert result["role"] is None
        assert result["geography"] is None

    def test_llm_invents_a_domain_not_in_the_taxonomy(self, monkeypatch):
        monkeypatch.setattr(
            scoring,
            "get_llm_json",
            lambda *a, **k: {
                "role": "Sales",
                "buyer_persona": "CIOs",
                "geography": ["Benelux"],
                "horizon": "Now",
                "domain": "Something Made Up",
                "next_action_strategist": "a",
                "next_action_sales": "b",
                "next_action_presales": "c",
            },
        )
        result = scoring.llm_enrich(
            "Manufacturing", "Energy Optimization", "IoT Platforms", FAKE_SIGNALS
        )
        assert result["domain"] is None
        assert result["role"] == "Sales"

    def test_llm_returns_geography_as_a_single_string_not_a_list(self, monkeypatch):
        monkeypatch.setattr(
            scoring,
            "get_llm_json",
            lambda *a, **k: {
                "role": "Sales",
                "buyer_persona": "CIOs",
                "geography": "Benelux",
                "horizon": "Now",
                "domain": None,
                "next_action_strategist": "a",
                "next_action_sales": "b",
                "next_action_presales": "c",
            },
        )
        result = scoring.llm_enrich(
            "Manufacturing", "Energy Optimization", "IoT Platforms", FAKE_SIGNALS
        )
        assert result["geography"] == "Benelux" 

class TestGroqKeyRotation:
    """GROQ_API_KEY to GROQ_API_KEY_5 are all read and tried in order on a
    rate limit. The groq SDK is replaced by a fake module: no network."""

    def _reload_client(self, monkeypatch, keys):
        import importlib
        import llm.llm_client as client

        for i, name in enumerate(
            ["GROQ_API_KEY"] + [f"GROQ_API_KEY_{n}" for n in range(2, 6)]
        ):
            monkeypatch.setenv(name, keys[i] if i < len(keys) else "")
        return importlib.reload(client)

    def teardown_method(self):
        import importlib
        import llm.llm_client as client

        importlib.reload(client)

    def test_all_five_keys_are_read(self, monkeypatch):
        client = self._reload_client(monkeypatch, ["k1", "k2", "k3", "k4", "k5"])
        assert client.GROQ_KEYS == ["k1", "k2", "k3", "k4", "k5"]

    def test_rotation_reaches_the_fifth_key(self, monkeypatch):
        import types

        client = self._reload_client(monkeypatch, ["k1", "k2", "k3", "k4", "k5"])
        tried = []

        class FakeGroq:
            def __init__(self, api_key):
                self.api_key = api_key
                self.chat = types.SimpleNamespace(
                    completions=types.SimpleNamespace(create=self._create)
                )

            def _create(self, **kwargs):
                tried.append(self.api_key)
                if self.api_key != "k5":
                    raise RuntimeError("Error code: 429 rate_limit_exceeded")
                message = types.SimpleNamespace(content="OK")
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=message)]
                )

        monkeypatch.setitem(
            sys.modules, "groq", types.SimpleNamespace(Groq=FakeGroq)
        )
        assert client._call_groq("ping") == "OK"
        assert tried == ["k1", "k2", "k3", "k4", "k5"]


class TestNonNumericLlmScore:
    """A hallucinated score must degrade one opportunity space, not the run.

    float("high") used to raise straight out of llm_evidence_quality /
    llm_strategic_relevance / llm_right_to_win, aborting the loop and losing
    every space still queued behind it.
    """

    def test_coerce_accepts_numbers_and_numeric_strings(self):
        assert scoring._coerce_llm_score(7.3) == (7.3, "")
        assert scoring._coerce_llm_score("6.7") == (6.7, "")
        assert scoring._coerce_llm_score(8) == (8.0, "")

    def test_coerce_marks_a_word_as_a_fallback(self):
        score, note = scoring._coerce_llm_score("high")
        assert score == scoring.NEUTRAL_SCORE
        assert scoring.FALLBACK_MARKER in note

    def test_coerce_marks_none_as_a_fallback(self):
        score, note = scoring._coerce_llm_score(None)
        assert score == scoring.NEUTRAL_SCORE
        assert scoring.FALLBACK_MARKER in note

    def test_evidence_quality_survives_a_word_score(self, monkeypatch):
        monkeypatch.setattr(
            scoring,
            "get_llm_json",
            lambda *a, **k: {"score": "high", "justification": "lots of signals"},
        )
        score, justification = scoring.llm_evidence_quality(FAKE_SIGNALS)
        assert score == scoring.NEUTRAL_SCORE
        assert "lots of signals" in justification
        assert scoring.FALLBACK_MARKER in justification

    def test_strategic_relevance_survives_a_word_score(self, monkeypatch):
        monkeypatch.setattr(
            scoring,
            "get_llm_json",
            lambda *a, **k: {"score": "very relevant", "justification": "fits"},
        )
        score, justification = scoring.llm_strategic_relevance(
            "Manufacturing", "Energy Optimization", "IoT Platforms", FAKE_SIGNALS
        )
        assert score == scoring.NEUTRAL_SCORE
        assert scoring.FALLBACK_MARKER in justification

    def test_right_to_win_survives_a_word_score(self, monkeypatch):
        monkeypatch.setattr(
            scoring,
            "get_llm_json",
            lambda *a, **k: {
                "portfolio_distance": "L1",
                "right_to_win_score": "strong",
                "matched_assets": [],
                "justification": "two assets exist",
            },
        )
        distance, score, assets, justification = scoring.llm_right_to_win(
            "Manufacturing", "Energy Optimization", "IoT Platforms"
        )
        assert distance == "L1"
        # The neutral score still gets the deterministic CRM bonus on top.
        expected = scoring.NEUTRAL_SCORE + scoring.crm_customer_overlap_bonus(
            "Manufacturing"
        )
        assert score == expected
        assert scoring.FALLBACK_MARKER in justification


class TestPortfolioDistanceValidation:
    """portfolio_distance drives the Presales filter, so only L0-L4 may be
    stored; anything else falls back to L4 and says so."""

    def _result(self, distance):
        return {
            "portfolio_distance": distance,
            "right_to_win_score": 7.0,
            "matched_assets": ["Flexible SD-WAN"],
            "justification": "ok",
        }

    def test_every_declared_level_passes_through(self, monkeypatch):
        for level in scoring.PORTFOLIO_DISTANCE_LEVELS:
            monkeypatch.setattr(
                scoring, "get_llm_json", lambda *a, **k: self._result(level)
            )
            distance, _, _, justification = scoring.llm_right_to_win(
                "Manufacturing", "Energy Optimization", "IoT Platforms"
            )
            assert distance == level
            assert scoring.FALLBACK_MARKER not in justification

    def test_invented_level_falls_back_to_l4(self, monkeypatch):
        monkeypatch.setattr(scoring, "get_llm_json", lambda *a, **k: self._result("L7"))
        distance, _, _, justification = scoring.llm_right_to_win(
            "Manufacturing", "Energy Optimization", "IoT Platforms"
        )
        assert distance == "L4"
        assert "L7" in justification
        assert scoring.FALLBACK_MARKER in justification

    def test_prose_level_falls_back_to_l4(self, monkeypatch):
        monkeypatch.setattr(
            scoring, "get_llm_json", lambda *a, **k: self._result("Direct offer")
        )
        distance, _, _, justification = scoring.llm_right_to_win(
            "Manufacturing", "Energy Optimization", "IoT Platforms"
        )
        assert distance == "L4"
        assert scoring.FALLBACK_MARKER in justification

    def test_lowercase_level_is_not_silently_accepted(self, monkeypatch):
        monkeypatch.setattr(scoring, "get_llm_json", lambda *a, **k: self._result("l0"))
        distance, _, _, _ = scoring.llm_right_to_win(
            "Manufacturing", "Energy Optimization", "IoT Platforms"
        )
        assert distance == "L4"


class TestRightToWinJustificationClaimsOnlyRealBonuses:
    """The pipeline/opportunity-count bonus read two empty config dicts, so it
    was always 0 while the justification still advertised it. The function and
    its mention are gone; only the CRM customer-overlap bonus is real."""

    def test_no_pipeline_bonus_function_left(self):
        assert not hasattr(scoring, "pipeline_calibration_bonus")

    def test_justification_never_mentions_pipeline_value(self, monkeypatch):
        monkeypatch.setattr(
            scoring,
            "get_llm_json",
            lambda *a, **k: {
                "portfolio_distance": "L0",
                "right_to_win_score": 9.0,
                "matched_assets": [],
                "justification": "direct offer",
            },
        )
        _, _, _, justification = scoring.llm_right_to_win(
            "Healthcare", "Energy Optimization", "IoT Platforms"
        )
        assert "pipeline value" not in justification
        assert "opportunity count" not in justification

    def test_crm_bonus_is_still_applied_and_named(self, monkeypatch):
        from pipeline.config import CUSTOMER_REFERENCES

        vertical_with_reference = next(
            c["vertical"] for c in CUSTOMER_REFERENCES if c.get("vertical")
        )
        monkeypatch.setattr(
            scoring,
            "get_llm_json",
            lambda *a, **k: {
                "portfolio_distance": "L0",
                "right_to_win_score": 7.0,
                "matched_assets": [],
                "justification": "direct offer",
            },
        )
        _, score, _, justification = scoring.llm_right_to_win(
            vertical_with_reference, "Energy Optimization", "IoT Platforms"
        )
        assert score > 7.0
        assert "CRM customer overlap" in justification


class TestNetworkIsBlockedInTests:
    """conftest.py blocks outbound sockets for the whole suite, so a test that
    forgets to mock its HTTP/feed/LLM call fails loudly instead of reaching the
    real internet."""

    def test_opening_a_connection_raises(self):
        import socket

        with pytest.raises(Exception) as excinfo:
            socket.create_connection(("example.com", 80), timeout=1)
        assert "mock" in str(excinfo.value).lower()

    def test_socket_connect_raises(self):
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with pytest.raises(Exception) as excinfo:
            sock.connect(("example.com", 80))
        assert "mock" in str(excinfo.value).lower()
