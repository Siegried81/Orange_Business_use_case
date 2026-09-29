from pipeline.db import get_connection, add_or_update_recurring_theme


def track_valid_themes(conn, vertical, themes, run_id=None):
    inserted = 0
    updated = 0
    for t in themes:
        use_case = t.get("use_case")
        technology = t.get("technology")
        if not use_case or not technology:
            continue
        result, _frequency = add_or_update_recurring_theme(
            conn,
            vertical,
            use_case,
            technology,
            rationale=t.get("rationale", ""),
            supporting_signal_count=t.get("supporting_signal_count", 0),
            run_id=run_id,
        )
        if result == "inserted":
            inserted += 1
        else:
            updated += 1
    return (inserted, updated)


if __name__ == "__main__":
    from pipeline.config import RECURRING_THEME_PROMOTION_THRESHOLD

    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM recurring_themes ORDER BY frequency DESC"
    ).fetchall()
    print(f"{len(rows)} recurring theme(s) tracked:\n")
    for r in rows:
        flag = (
            " [PROMOTABLE]"
            if r["frequency"] >= RECURRING_THEME_PROMOTION_THRESHOLD
            and (not r["promoted"])
            else ""
        )
        promoted_flag = " [already promoted]" if r["promoted"] else ""
        print(
            f"  {r['vertical']} x {r['use_case']} x {r['technology']} -- seen {r['frequency']}x{flag}{promoted_flag}"
        )
    conn.close()