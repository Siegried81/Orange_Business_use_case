"""
signals_discovery.py
=====================

ingest.py  ->  analyze.py  ->  (signals_discovery, in-memory)  ->  radar_cli.py promote  ->  scoring.py

Tracks how often a VALID Vertical x Use Case x Technology theme (i.e.
already matching the closed taxonomy, not a watchlist_terms candidate)
recurs across separate analyze.py runs. Once a theme has recurred
config.RECURRING_THEME_PROMOTION_THRESHOLD times, `radar_cli.py promote`
turns it into a real, registered opportunity space -- this is what lets the
pipeline grow past a fixed, hand-picked list of opportunity spaces (see
config.CANDIDATES) as more signals get ingested over time.

Run via radar_cli.py:
    python radar_cli.py promote
"""

from pipeline.db import get_connection, add_or_update_recurring_theme


def track_valid_themes(conn, vertical, themes, run_id=None):
    """Upserts every valid theme extract_themes() found for one vertical
    into recurring_themes, bumping frequency for anything seen before.
    Returns (inserted_count, updated_count)."""
    inserted = 0
    updated = 0
    for t in themes:
        use_case = t.get("use_case")
        technology = t.get("technology")
        if not use_case or not technology:
            continue  # malformed theme, nothing to track
        result, _frequency = add_or_update_recurring_theme(
            conn, vertical, use_case, technology,
            rationale=t.get("rationale", ""),
            supporting_signal_count=t.get("supporting_signal_count", 0),
            run_id=run_id,
        )
        if result == "inserted":
            inserted += 1
        else:
            updated += 1
    return inserted, updated


if __name__ == "__main__":
    # Quick manual check: what's currently tracked, and what's promotable?
    from pipeline.config import RECURRING_THEME_PROMOTION_THRESHOLD

    conn = get_connection()
    rows = conn.execute("SELECT * FROM recurring_themes ORDER BY frequency DESC").fetchall()
    print(f"{len(rows)} recurring theme(s) tracked:\n")
    for r in rows:
        flag = " [PROMOTABLE]" if r["frequency"] >= RECURRING_THEME_PROMOTION_THRESHOLD and not r["promoted"] else ""
        promoted_flag = " [already promoted]" if r["promoted"] else ""
        print(f"  {r['vertical']} x {r['use_case']} x {r['technology']} "
              f"-- seen {r['frequency']}x{flag}{promoted_flag}")
    conn.close()
