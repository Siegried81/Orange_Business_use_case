"""The right-to-win customer-reference bonus: named parameters and their sensitivity.

The bonus is a hand-set parameter with no outcome data to fit it against, so
what is pinned here is that it is configurable, applied as documented, and
that scripts/rtw_bonus_sensitivity.py counts what another value would move.
"""
import sys
from pathlib import Path

import pytest

from pipeline import scoring

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import rtw_bonus_sensitivity as sens  # noqa: E402


@pytest.fixture
def references(monkeypatch):
    """Three references in Public Sector, one in Retail, none elsewhere."""
    monkeypatch.setattr(
        scoring,
        "CUSTOMER_REFERENCES",
        [
            {"customer": "A", "vertical": "Public Sector", "source": "https://example.com"},
            {"customer": "B", "vertical": "Public Sector", "source": "https://example.com"},
            {"customer": "C", "vertical": "Public Sector", "source": "https://example.com"},
            {"customer": "D", "vertical": "Retail", "source": "https://example.com"},
        ],
    )


def test_documented_defaults():
    assert scoring.CUSTOMER_REFERENCE_BONUS == 0.5
    assert scoring.CUSTOMER_REFERENCE_BONUS_CAP == 1.0


def test_bonus_is_per_reference_and_capped(references):
    assert scoring.customer_reference_bonus("Retail") == 0.5
    assert scoring.customer_reference_bonus("Public Sector") == 1.0
    assert scoring.customer_reference_bonus("Energy") == 0.0


def test_bonus_values_can_be_passed_or_configured(references, monkeypatch):
    assert scoring.customer_reference_bonus("Public Sector", per_reference=0.25) == 0.75
    assert scoring.customer_reference_bonus("Public Sector", per_reference=0.25, cap=0.5) == 0.5
    monkeypatch.setattr(scoring, "CUSTOMER_REFERENCE_BONUS", 0.75)
    assert scoring.customer_reference_bonus("Retail") == 0.75


def test_configured_bonus_reaches_the_stored_score(references, monkeypatch):
    monkeypatch.setattr(scoring, "CUSTOMER_REFERENCE_BONUS", 0.25)
    monkeypatch.setattr(
        scoring,
        "get_llm_json",
        lambda *a, **k: {
            "portfolio_distance": "L3",
            "right_to_win_score": 6.5,
            "matched_assets": [],
            "justification": "one capability to build",
        },
    )
    _, score, _, justification = scoring.llm_right_to_win("Public Sector", "X", "Y")
    assert score == 7.25
    assert sens.applied_bonus(justification) == 0.75


def test_applied_bonus_reads_old_and_new_wording():
    assert sens.applied_bonus("fits +1.0 calibration bonus (CRM customer overlap).") == 1.0
    assert sens.applied_bonus("fits +0.75 calibration bonus (3 named customer reference(s) in X).") == 0.75
    assert sens.applied_bonus("no bonus here") == 0.0
    assert sens.applied_bonus(None) == 0.0


def _row(label, vertical, total, rtw, bonus):
    note = f" +{bonus} calibration bonus (x)." if bonus else ""
    return {
        "label": label,
        "vertical": vertical,
        "total_score": total,
        "right_to_win_score": rtw,
        "portfolio_distance": "L3",
        "justification": "llm" + note,
    }


def test_sensitivity_counts_threshold_crossings_and_quadrants(references):
    rows = [
        # LLM said 6.5, +0.5 for Retail's one reference -> 7.0: strong at 0.5 only.
        _row("OS1", "Retail", 8.0, 7.0, 0.5),
        # LLM said 8.0, Public Sector capped at +1.0 -> 9.0: strong whatever the value.
        _row("OS2", "Public Sector", 8.0, 9.0, 1.0),
        # No reference: never moves.
        _row("OS3", "Energy", 8.0, 6.0, 0),
    ]
    by_value = {r["value"]: r for r in sens.sensitivity(rows, [0.0, 0.25, 0.5, 0.75])}
    assert by_value[0.5]["score_changed"] == 0
    assert by_value[0.0]["score_changed"] == 2
    assert by_value[0.0]["crossed_threshold"] == 1
    assert by_value[0.0]["quadrant_changed"] == 1
    assert by_value[0.0]["strong"] == 1
    assert by_value[0.25]["crossed_threshold"] == 1
    assert by_value[0.75]["crossed_threshold"] == 0
    assert by_value[0.75]["score_changed"] == 1
    assert all(r["level_changed"] == 0 for r in by_value.values())


def test_clipped_rows_are_counted(references):
    rows = [_row("OS1", "Public Sector", 8.0, 10.0, 1.0), _row("OS2", "Retail", 8.0, 7.0, 0.5)]
    assert sens.sensitivity(rows, [0.5])[0]["clipped_rows"] == 1
