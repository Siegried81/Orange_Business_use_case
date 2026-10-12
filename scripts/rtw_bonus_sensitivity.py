"""How many opportunity spaces move if the customer-reference bonus changes.

The right-to-win bonus (+CUSTOMER_REFERENCE_BONUS per named customer
reference in the vertical, capped at CUSTOMER_REFERENCE_BONUS_CAP) is a hand
choice with nothing to fit it against, so this script shows what hangs on it
instead. For each stored right-to-win score it reads back the bonus actually
applied (from the "+<value> calibration bonus" phrase of the justification),
recovers the LLM's own score, re-applies the bonus at each candidate value and
counts what changes. No LLM call: the LLM's score and level are reused as is.

The portfolio level (L0-L4) is the LLM's classification and never depends on
the bonus, so it cannot change here; what can change is the 0-10 score, its
side of STRONG_THRESHOLD, and with it the quadrant.

A score stored at 10.0 with a bonus was clipped, so the LLM's own score is
only known to be at least 10 - bonus; the lower bound is used and those rows
are counted apart.

Usage, from the repo root:
    python scripts/rtw_bonus_sensitivity.py [values...]   (default 0 0.25 0.5 0.75)
"""
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.config import STRONG_THRESHOLD, quadrant  # noqa: E402
from pipeline.scoring import (  # noqa: E402
    CUSTOMER_REFERENCE_BONUS,
    CUSTOMER_REFERENCE_BONUS_CAP,
    customer_reference_bonus,
)

APPLIED_BONUS = re.compile(r"\+([0-9]+(?:\.[0-9]+)?) calibration bonus")
DB_PATH = Path(__file__).resolve().parent.parent / "radar.db"


def applied_bonus(justification) -> float:
    match = APPLIED_BONUS.search(justification or "")
    return float(match.group(1)) if match else 0.0


def load_rows(conn):
    """Current attractiveness and right-to-win per OS, latest row of each."""
    return conn.execute(
        """SELECT os.label, os.vertical, s.total_score,
                  r.right_to_win_score, r.portfolio_distance, r.justification
           FROM opportunity_spaces os
           JOIN scores s ON s.id = (SELECT id FROM scores WHERE opportunity_space_id = os.id
                                    ORDER BY computed_at DESC, id DESC LIMIT 1)
           JOIN right_to_win_scores r ON r.id = (
                SELECT id FROM right_to_win_scores WHERE opportunity_space_id = os.id
                ORDER BY computed_at DESC, id DESC LIMIT 1)"""
    ).fetchall()


def sensitivity(rows, values, cap=CUSTOMER_REFERENCE_BONUS_CAP):
    """One dict per candidate value, compared with the stored scores."""
    results = []
    clipped = sum(
        1 for r in rows if applied_bonus(r["justification"]) and r["right_to_win_score"] >= 10.0
    )
    for value in values:
        moved = crossed = quadrant_changed = strong = 0
        for r in rows:
            stored = r["right_to_win_score"]
            raw = stored - applied_bonus(r["justification"])
            new = min(10.0, raw + customer_reference_bonus(r["vertical"], value, cap))
            moved += abs(new - stored) > 1e-9
            crossed += (new >= STRONG_THRESHOLD) != (stored >= STRONG_THRESHOLD)
            quadrant_changed += quadrant(r["total_score"], new) != quadrant(r["total_score"], stored)
            strong += quadrant(r["total_score"], new) == "strong"
        results.append(
            {
                "value": value,
                "score_changed": moved,
                "crossed_threshold": crossed,
                "quadrant_changed": quadrant_changed,
                "strong": strong,
                "level_changed": 0,
                "clipped_rows": clipped,
            }
        )
    return results


def main(argv):
    values = [float(v) for v in argv] or [0.0, 0.25, 0.5, 0.75]
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = load_rows(conn)
    conn.close()
    print(
        f"{len(rows)} OS; current bonus {CUSTOMER_REFERENCE_BONUS}/reference, cap "
        f"{CUSTOMER_REFERENCE_BONUS_CAP}; changes counted against the stored scores.\n"
    )
    print("| Bonus per reference | RTW score changes | Crosses RTW 7 | Quadrant changes | Strong | Level changes |")
    print("|---|---|---|---|---|---|")
    results = sensitivity(rows, values)
    for res in results:
        print(
            f"| {res['value']:g} | {res['score_changed']} | {res['crossed_threshold']} | "
            f"{res['quadrant_changed']} | {res['strong']} | {res['level_changed']} |"
        )
    print(f"\n{results[0]['clipped_rows']} stored score(s) were clipped at 10; their LLM score is a lower bound.")


if __name__ == "__main__":
    main(sys.argv[1:])
