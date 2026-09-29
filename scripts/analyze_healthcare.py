import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.db import (
    get_connection,
    get_latest_scores,
    get_linked_signals_for_opportunity_space,
)


def main():
    conn = get_connection()
    rows = [r for r in get_latest_scores(conn) if r["vertical"] == "Healthcare"]
    rows.sort(key=lambda r: r["total_score"], reverse=True)
    if not rows:
        print("No Healthcare OS found -- check the vertical name matches exactly.")
        return
    print(
        f"{'=' * 70}\nHEALTHCARE -- {len(rows)} opportunity space(s), sorted by attractiveness\n{'=' * 70}\n"
    )
    for r in rows:
        os_row = conn.execute(
            "SELECT * FROM opportunity_spaces WHERE id = ?", (r["id"],)
        ).fetchone()
        signals = get_linked_signals_for_opportunity_space(conn, r["id"])
        sources = sorted({s["source_name"] for s in signals})
        print(f"{r['label']} -- {r['use_case']} x {r['technology']}")
        print(f"  Attractiveness: {r['total_score']}/10")
        print(
            f"    market_signal_strength={r['market_signal_strength']}  source_diversity={r['source_diversity']}  evidence_quality={r['evidence_quality']}  novelty_momentum={r['novelty_momentum']}  strategic_relevance={r['strategic_relevance']}"
        )
        print(f"    evidence: {r['evidence_quality_justification']}")
        print(f"    strategic: {r['strategic_relevance_justification']}")
        print(
            f"  Right-to-win: {r['right_to_win_score']}/10  [{r['portfolio_distance']}]"
        )
        print(f"    matched assets: {r['matched_assets'] or 'none'}")
        print(f"    -> {r['justification']}")
        print(f"  Urgency: {r['urgency_score']}/10")
        print(
            f"  Geography: {os_row['geography']}   Horizon: {os_row['horizon']}   Owning team: {os_row['persona']}   Buyer: {os_row['buyer_persona']}"
        )
        print(
            f"  Grounded by {len(signals)} signal(s), {len(sources)} distinct source(s): {', '.join(sources[:6])}{('...' if len(sources) > 6 else '')}"
        )
        print(f"  Next action (Strategist): {os_row['next_action_strategist']}")
        print(f"  Next action (Sales):      {os_row['next_action_sales']}")
        print(f"  Next action (Presales):   {os_row['next_action_presales']}")
        print()
    avg_attract = sum((r["total_score"] for r in rows)) / len(rows)
    avg_rtw = sum((r["right_to_win_score"] for r in rows)) / len(rows)
    l0_l1 = sum((1 for r in rows if r["portfolio_distance"] in ("L0", "L1")))
    print(f"{'=' * 70}")
    print(
        f"Summary: avg attractiveness {avg_attract:.2f}/10, avg right-to-win {avg_rtw:.2f}/10, {l0_l1}/{len(rows)} OS already at L0/L1 (close to sellable today)."
    )
    conn.close()


if __name__ == "__main__":
    main()