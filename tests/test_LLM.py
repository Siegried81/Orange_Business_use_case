import sys
from pathlib import Path

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
