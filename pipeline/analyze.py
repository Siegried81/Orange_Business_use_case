import sys
from pipeline.db import (
    get_connection,
    add_to_watchlist,
    list_watchlist,
    update_watchlist_status,
    new_run_id,
    list_promotable_themes,
)
from pipeline.config import (
    USE_CASES_TAXONOMY,
    TECHNOLOGIES_TAXONOMY,
    RECURRING_THEME_PROMOTION_THRESHOLD,
)
from pipeline.taxonomy_validation import is_generic_taxonomy_term
from pipeline.theme_promotion import track_valid_themes
from llm.llm_client import get_llm_json

WATCHLIST_PROMOTION_THRESHOLD = 2


def summary(conn):
    print("=== Signals by vertical ===")
    rows = conn.execute(
        "SELECT vertical_hint, COUNT(*) as n FROM signals GROUP BY vertical_hint ORDER BY n DESC"
    ).fetchall()
    for r in rows:
        print(f"  {r['vertical_hint'] or '(no vertical)'}: {r['n']}")
    print("\n=== Signals by type ===")
    rows = conn.execute(
        "SELECT signal_type, COUNT(*) as n FROM signals GROUP BY signal_type ORDER BY n DESC"
    ).fetchall()
    for r in rows:
        print(f"  {r['signal_type']}: {r['n']}")
    print("\n=== Signals by source ===")
    rows = conn.execute(
        "SELECT source_name, COUNT(*) as n FROM signals GROUP BY source_name ORDER BY n DESC LIMIT 15"
    ).fetchall()
    for r in rows:
        print(f"  {r['source_name']}: {r['n']}")
    total = conn.execute("SELECT COUNT(*) as n FROM signals").fetchone()["n"]
    print(f"\nTOTAL signals in database: {total}")


def dump_titles(conn, vertical=None, signal_type=None, limit=100):
    query = "SELECT source_name, signal_type, title FROM signals WHERE 1=1"
    params = []
    if vertical:
        query += " AND vertical_hint = ?"
        params.append(vertical)
    if signal_type:
        query += " AND signal_type = ?"
        params.append(signal_type)
    query += " ORDER BY signal_type, collected_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    for r in rows:
        print(f"[{r['signal_type']:14}] ({r['source_name']}) {r['title']}")
    print(f"\n{len(rows)} titles shown.")


THEME_EXTRACTION_SYSTEM_PROMPT = 'You are analyzing market signals (news, research\npapers, vendor announcements, regulation) collected for one business vertical, to spot\ncandidate Opportunity Spaces for a B2B telecom/cloud provider (Orange Business).\n\nAn Opportunity Space = Vertical x Use Case x Technology, and must be SPECIFIC\n(e.g. "Manufacturing x Energy Optimization x Computer Vision"), never a generic\ntheme like "AI in industry" or "Cloud adoption".\n\nIMPORTANT: use_case and technology MUST be picked EXACTLY from these closed lists --\ndo not invent new terms, do not rephrase them:\n\nUse cases: {use_cases}\nTechnologies: {technologies}\n\nIf a real, recurring pattern in the signals genuinely does NOT fit any combination\nof the lists above, do NOT force it into a bad match. Instead put it in\nwatchlist_candidates so the team can review it for adding to the taxonomy later.\n\nRead the signal titles below and respond with ONLY a JSON object, no preamble,\nno markdown fences:\n{{\n  "themes": [\n    {{"use_case": "<exact match from the list>", "technology": "<exact match from the list>",\n      "supporting_signal_count": <int>, "rationale": "<one sentence>"}}\n  ],\n  "watchlist_candidates": [\n    {{"term": "<the new term you couldn\'t classify>", "category": "use_case"|"technology",\n      "rationale": "<why this looks like a real pattern despite not fitting the taxonomy>"}}\n  ]\n}}\nthemes: 3-6 items max, reject anything generic or supported by only one vague signal.\nwatchlist_candidates: only include genuinely recurring patterns, not one-off mentions.'


def _call_theme_extraction_llm(vertical, rows):
    titles = "\n".join((f"- [{r['signal_type']}] {r['title']}" for r in rows))
    prompt = f"Vertical: {vertical}\n\nSignals:\n{titles}"
    system_prompt = THEME_EXTRACTION_SYSTEM_PROMPT.format(
        use_cases=", ".join(USE_CASES_TAXONOMY),
        technologies=", ".join(TECHNOLOGIES_TAXONOMY),
    )
    return get_llm_json(prompt, system_prompt=system_prompt)


def _classify_themes(themes, candidates):
    valid_themes = []
    watchlist_entries = []
    skipped_generic = []
    for t in themes:
        use_case = t.get("use_case")
        technology = t.get("technology")
        if use_case in USE_CASES_TAXONOMY and technology in TECHNOLOGIES_TAXONOMY:
            valid_themes.append(t)
            continue
        if use_case not in USE_CASES_TAXONOMY:
            watchlist_entries.append((use_case or "unknown", "use_case"))
        if technology not in TECHNOLOGIES_TAXONOMY:
            technology = technology or "unknown"
            if is_generic_taxonomy_term(technology, "technology"):
                skipped_generic.append((technology, "technology"))
            else:
                watchlist_entries.append((technology, "technology"))
    for c in candidates:
        if c.get("term") and c.get("category") in ("use_case", "technology"):
            if is_generic_taxonomy_term(c["term"], c["category"]):
                skipped_generic.append((c["term"], c["category"]))
            else:
                watchlist_entries.append((c["term"], c["category"]))
    return (valid_themes, watchlist_entries, skipped_generic)


def extract_themes(conn, vertical, max_signals=40):
    rows = conn.execute(
        "SELECT signal_type, title FROM signals WHERE vertical_hint = ? ORDER BY collected_at DESC LIMIT ?",
        (vertical, max_signals),
    ).fetchall()
    if len(rows) < 3:
        print(
            f"[{vertical}] only {len(rows)} signals -- too few for reliable theme extraction, skipping"
        )
        return ([], [])
    result = _call_theme_extraction_llm(vertical, rows)
    if not result or "themes" not in result:
        print(f"[{vertical}] theme extraction failed or returned nothing usable")
        return ([], [])
    themes = result.get("themes", [])
    candidates = result.get("watchlist_candidates", [])
    valid_themes, watchlist_entries, skipped_generic = _classify_themes(
        themes, candidates
    )
    for term, category in watchlist_entries:
        add_to_watchlist(conn, term, category, vertical)
    for term, category in skipped_generic:
        print(f"[{vertical}] skipped generic {category} candidate: {term!r}")
    return (valid_themes, candidates)


def run_full_analysis(from_vertical=None):
    conn = get_connection()
    summary(conn)
    run_id = new_run_id()
    verticals = [
        r["vertical_hint"]
        for r in conn.execute(
            "SELECT DISTINCT vertical_hint FROM signals WHERE vertical_hint IS NOT NULL"
        ).fetchall()
    ]
    if from_vertical:
        before = len(verticals)
        matched = [v for v in verticals if v.lower() == from_vertical.lower()]
        if not matched:
            print(
                f"--from={from_vertical}: no vertical matches this name exactly (available: {', '.join(verticals)}). Running everything instead."
            )
        else:
            start_index = verticals.index(matched[0])
            verticals = verticals[start_index:]
            print(
                f"--from={from_vertical}: skipping {before - len(verticals)} vertical(s) already processed before the interruption.\n"
            )
    for vertical in verticals:
        print(f"\n{'=' * 60}\nTheme extraction: {vertical}\n{'=' * 60}")
        themes, candidates = extract_themes(conn, vertical)
        for t in themes:
            print(
                f"  -> {vertical} x {t.get('use_case')} x {t.get('technology')} ({t.get('supporting_signal_count')} signals)"
            )
            print(f"     {t.get('rationale')}")
        if candidates:
            print(
                f"  [watchlist] {len(candidates)} out-of-taxonomy candidate(s) logged for review"
            )
        if themes:
            inserted, updated = track_valid_themes(
                conn, vertical, themes, run_id=run_id
            )
            print(f"  [recurring_themes] {inserted} new, {updated} bumped in frequency")
    print(
        f"\n{'=' * 60}\nWatchlist terms ready for team review (seen >= {WATCHLIST_PROMOTION_THRESHOLD}x)\n{'=' * 60}"
    )
    ready = list_watchlist(conn, min_frequency=WATCHLIST_PROMOTION_THRESHOLD)
    if not ready:
        print("  None yet -- check back after more ingest runs.")
    for term in ready:
        print(
            f'  [{term['category']}] "{term['term']}" -- seen {term['frequency']}x (vertical: {term['vertical']}) -- id={term['id']}'
        )
        update_watchlist_status(conn, term["id"], "proposed")
    print("\n  Below threshold, still accumulating:")
    for term in list_watchlist(conn, status="pending"):
        print(f'  [{term['category']}] "{term['term']}" -- seen {term['frequency']}x')
    print(
        f"\n{'=' * 60}\nRecurring themes ready for promotion (recurred >= {RECURRING_THEME_PROMOTION_THRESHOLD}x)\n{'=' * 60}"
    )
    promotable = list_promotable_themes(conn, RECURRING_THEME_PROMOTION_THRESHOLD)
    if not promotable:
        print(
            "  None yet -- run ingest + analyze a few more times, or lower RECURRING_THEME_PROMOTION_THRESHOLD in pipeline/config.py."
        )
    for theme in promotable:
        print(
            f"  {theme['vertical']} x {theme['use_case']} x {theme['technology']} -- seen {theme['frequency']}x -- run `python radar_cli.py promote` to register it"
        )
    conn.close()


if __name__ == "__main__":
    from_vertical = None
    for arg in sys.argv:
        if arg.startswith("--from="):
            from_vertical = arg.split("=", 1)[1]
    run_full_analysis(from_vertical=from_vertical)