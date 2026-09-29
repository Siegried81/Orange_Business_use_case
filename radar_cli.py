import argparse
import inspect
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pipeline.db import (
    get_connection,
    init_db,
    get_signals_for_vertical,
    get_linked_signals_for_opportunity_space,
    get_all_opportunity_spaces,
    get_latest_scores,
    upsert_opportunity_space,
    new_run_id,
    wipe_opportunity_spaces,
    delete_opportunity_spaces,
    link_signal_to_opportunity,
    find_opportunity_space_by_triple,
    next_opportunity_space_label,
    list_watchlist,
    list_promotable_themes,
    mark_theme_promoted,
)
from pipeline.config import (
    CANDIDATES,
    RECURRING_THEME_PROMOTION_THRESHOLD,
    CAPABILITY_STATS,
)
from pipeline.analyze import extract_themes
from pipeline.theme_promotion import track_valid_themes
import pipeline.extend_taxonomy as ext


def cmd_create(conn, wipe=False):
    init_db()
    if wipe:
        existing_count = conn.execute(
            "SELECT COUNT(*) as c FROM opportunity_spaces"
        ).fetchone()["c"]
        if existing_count > 0:
            print(
                f"--wipe: this will permanently delete {existing_count} existing opportunity space(s), their scores, right-to-win scores, and signal links."
            )
            print("Signals, watchlist_terms, and recurring_themes are NOT affected.")
            confirm = input('Type "yes" to confirm: ')
            if confirm.strip().lower() == "yes":
                wipe_opportunity_spaces(conn)
                print("Wiped (autoincrement counters reset too).\n")
            else:
                print("Wipe cancelled -- continuing without wiping.\n")
        else:
            print("--wipe: nothing to wipe, already clean.\n")
    run_id = new_run_id()
    print(f"Registering seed opportunity spaces under run_id={run_id}\n")
    for label, vertical, use_case, technology in CANDIDATES:
        dup = find_opportunity_space_by_triple(
            conn, vertical, use_case, technology, exclude_label=label
        )
        if dup:
            print(
                f"  SKIPPED {label}: {vertical} x {use_case} x {technology} is already registered as {dup['label']} -- not creating a duplicate OS for the same triple."
            )
            continue
        try:
            os_id = upsert_opportunity_space(
                conn, run_id, label, vertical, use_case, technology
            )
        except sqlite3.IntegrityError:
            print(f"  SKIPPED {label}: duplicate triple rejected by the database.")
            continue
        print(f"{label} (id={os_id}): {vertical} x {use_case} x {technology}")


def cmd_delete(conn, labels):
    print(
        f"About to permanently delete {len(labels)} opportunity space(s): {', '.join(labels)}"
    )
    print(
        "This also removes their scores, right-to-win scores, and signal links. Signals themselves are NOT deleted."
    )
    confirm = input('Type "yes" to confirm: ')
    if confirm.strip().lower() != "yes":
        print("Cancelled -- nothing deleted.")
        return
    deleted = delete_opportunity_spaces(conn, labels)
    not_found = [l for l in labels if l not in deleted]
    if deleted:
        print(f"Deleted: {', '.join(deleted)}")
    if not_found:
        print(f"Not found, skipped: {', '.join(not_found)}")


def cmd_promote(conn):
    promotable = list_promotable_themes(conn, RECURRING_THEME_PROMOTION_THRESHOLD)
    if not promotable:
        print(
            f"Nothing to promote yet (need frequency >= {RECURRING_THEME_PROMOTION_THRESHOLD}). Run `python -m pipeline.analyze` a few more times as new signals come in, or lower RECURRING_THEME_PROMOTION_THRESHOLD in pipeline/config.py."
        )
        return
    run_id = new_run_id()
    promoted_count = 0
    for theme in promotable:
        vertical, use_case, technology = (
            theme["vertical"],
            theme["use_case"],
            theme["technology"],
        )
        dup = find_opportunity_space_by_triple(conn, vertical, use_case, technology)
        if dup:
            print(
                f"  {vertical} x {use_case} x {technology} already exists as {dup['label']} -- marking promoted, no new OS created."
            )
            mark_theme_promoted(conn, theme["id"])
            continue
        label = next_opportunity_space_label(conn)
        try:
            os_id = upsert_opportunity_space(
                conn, run_id, label, vertical, use_case, technology
            )
        except sqlite3.IntegrityError:
            print(
                f"  SKIPPED: {vertical} x {use_case} x {technology} rejected by the database as a duplicate triple -- marking promoted, no new OS created."
            )
            mark_theme_promoted(conn, theme["id"])
            continue
        mark_theme_promoted(conn, theme["id"])
        promoted_count += 1
        print(
            f"  PROMOTED {label} (id={os_id}): {vertical} x {use_case} x {technology} (recurred {theme['frequency']}x)"
        )
    print(
        f"\n{promoted_count} new opportunity space(s) promoted. Run `python -m pipeline.scoring` to score them."
    )


def cmd_watchlist(conn):
    print("=== Out-of-taxonomy terms (watchlist_terms) ===")
    pending = list_watchlist(conn, status="pending")
    proposed = list_watchlist(conn, status="proposed")
    if not pending and (not proposed):
        print("  Nothing tracked yet.")
    for t in proposed:
        print(
            f'  [PROPOSED] [{t['category']}] "{t['term']}" -- seen {t['frequency']}x (vertical: {t['vertical']})'
        )
    for t in pending:
        print(
            f'  [pending]  [{t['category']}] "{t['term']}" -- seen {t['frequency']}x (vertical: {t['vertical']})'
        )
    print(
        f"\n=== Recurring themes (recurring_themes, promotion threshold = {RECURRING_THEME_PROMOTION_THRESHOLD}) ==="
    )
    rows = conn.execute(
        "SELECT * FROM recurring_themes ORDER BY frequency DESC"
    ).fetchall()
    if not rows:
        print("  Nothing tracked yet -- run `python -m pipeline.analyze` first.")
    for r in rows:
        status = (
            "[already promoted]"
            if r["promoted"]
            else (
                "[PROMOTABLE -- run `radar_cli.py promote`]"
                if r["frequency"] >= RECURRING_THEME_PROMOTION_THRESHOLD
                else "[accumulating]"
            )
        )
        print(
            f"  {r['vertical']} x {r['use_case']} x {r['technology']} -- seen {r['frequency']}x {status}"
        )


def cmd_review(conn):
    ext.init_proposals_table(conn)
    ext.run_review(conn)


def cmd_calibrate(conn):
    print("Raw signal counts by vertical_hint (quick sanity check)")
    for r in conn.execute(
        "SELECT vertical_hint, COUNT(*) as n FROM signals GROUP BY vertical_hint ORDER BY n DESC"
    ):
        print(f"  {r['vertical_hint'] or '(untagged)'}: {r['n']}")
    total = conn.execute("SELECT COUNT(*) as n FROM signals").fetchone()["n"]
    print(f"  TOTAL: {total}")
    print(
        "\n=== What scoring.py actually sees per OS, via its linked signals (use this to set caps -- run `radar_cli.py link` first) ==="
    )
    spaces = get_all_opportunity_spaces(conn)
    print(f"{'OS':<8} {'Vertical':<30} {'Linked signals':<16} {'Distinct sources':<18}")
    print("-" * 75)
    signal_counts, source_counts = ([], [])
    for os_row in spaces:
        linked = get_linked_signals_for_opportunity_space(conn, os_row["id"])
        distinct_sources = {s["source_name"] for s in linked}
        signal_counts.append(len(linked))
        source_counts.append(len(distinct_sources))
        print(
            f"{os_row['label']:<8} {os_row['vertical']:<30} {len(linked):<16} {len(distinct_sources):<18}"
        )
    if signal_counts:
        print(
            f"\nAcross {len(spaces)} OS: signal count min={min(signal_counts)} max={max(signal_counts)} avg={sum(signal_counts) / len(signal_counts):.1f}  |  distinct sources min={min(source_counts)} max={max(source_counts)} avg={sum(source_counts) / len(source_counts):.1f}"
        )
        print(
            "Set MARKET_SIGNAL_CAP near the high end of the linked-signal-count range (not the average -- a cap at the average would already saturate half the OS at 10/10) and SOURCE_DIVERSITY_CAP the same way, using distinct sources."
        )
    current_top_n = inspect.signature(cmd_link).parameters["top_n"].default
    print(
        f"\n=== Signals with keyword overlap > 0, per OS, BEFORE link's top_n cutoff (current top_n={current_top_n}) ==="
    )
    overlap_counts = []
    for os_row in spaces:
        target_keywords = _keywords(f"{os_row['use_case']} {os_row['technology']}")
        vertical_signals = get_signals_for_vertical(conn, os_row["vertical"])
        vertical_signals = [
            s
            for s in vertical_signals
            if (s["source_name"] or "").strip().lower() not in NON_TECH_SOURCES
        ]
        matching = sum(
            (
                1
                for s in vertical_signals
                if len(target_keywords & _keywords(s["title"] or "")) > 0
            )
        )
        overlap_counts.append(matching)
        flag = (
            f"  <-- exceeds current top_n={current_top_n}, signals ARE being cut"
            if matching > current_top_n
            else ""
        )
        print(f"{os_row['label']:<8} {os_row['vertical']:<30} {matching:<16}{flag}")
    if overlap_counts:
        overlap_counts.sort()
        n = len(overlap_counts)
        p50 = overlap_counts[n // 2]
        p90 = overlap_counts[min(n - 1, int(n * 0.9))]
        exceeding = sum((1 for c in overlap_counts if c > current_top_n))
        print(
            f"\nAcross {n} OS: median={p50}  90th percentile={p90}  max={max(overlap_counts)}  |  {exceeding} OS ({exceeding / n * 100:.0f}%) exceed the current top_n={current_top_n} and are having relevant signals cut."
        )
        print(
            "A defensible top_n covers most real OS without keeping near-irrelevant, low-overlap signals just to hit a round number -- e.g. the 90th percentile above, not a guess."
        )


SIMILARITY_THRESHOLD = 0.85


def _normalize_title(title):
    t = title.lower()
    t = re.sub("\\s*-\\s*[a-z0-9 .]+$", "", t)
    t = re.sub("\\s+", " ", t).strip()
    return t


def _find_duplicate_groups(conn):
    signals = conn.execute(
        "SELECT id, source_name, title, collected_at FROM signals ORDER BY collected_at ASC"
    ).fetchall()
    normalized = [(s, _normalize_title(s["title"] or "")) for s in signals]
    used = set()
    groups = []
    for i, (sig_a, norm_a) in enumerate(normalized):
        if sig_a["id"] in used or not norm_a:
            continue
        group = [sig_a]
        used.add(sig_a["id"])
        for sig_b, norm_b in normalized[i + 1 :]:
            if sig_b["id"] in used or not norm_b:
                continue
            ratio = SequenceMatcher(None, norm_a, norm_b).ratio()
            if ratio >= SIMILARITY_THRESHOLD:
                group.append(sig_b)
                used.add(sig_b["id"])
        if len(group) > 1:
            groups.append(group)
    return groups


def cmd_dedupe(conn, apply=False):
    groups = _find_duplicate_groups(conn)
    if not groups:
        print("No near-duplicate signals found.")
        return
    total_removable = sum((len(g) - 1 for g in groups))
    print(
        f"Found {len(groups)} duplicate groups, {total_removable} removable signals:\n"
    )
    for group in groups:
        print(
            f"  KEEP: [{group[0]['source_name']}] {group[0]['title']}  ({group[0]['collected_at']})"
        )
        for dup in group[1:]:
            print(
                f"  DROP: [{dup['source_name']}] {dup['title']}  ({dup['collected_at']})"
            )
        print()
    if apply:
        total = 0
        for group in groups:
            for dup in group[1:]:
                conn.execute("DELETE FROM signals WHERE id = ?", (dup["id"],))
                conn.execute(
                    "DELETE FROM opportunity_signals WHERE signal_id = ?", (dup["id"],)
                )
                total += 1
        conn.commit()
        print(f"Deleted {total} near-duplicate signals.")
        print(
            "\nRe-run `radar_cli.py calibrate`, `python -m pipeline.scoring`, `radar_cli.py link` and `radar_cli.py summary` to refresh with the cleaned data."
        )
    else:
        print(
            f"Total: {total_removable} signals would be removed. Re-run with --apply to actually delete them."
        )


STOPWORDS = {"and", "the", "for", "with", "of", "in", "on", "a", "an", "to", "x"}
NON_TECH_SOURCES = {
    "latestly",
    "yahoo sports",
    "mix 94.9",
    "narooma news",
    "sports illustrated",
    "bradenton herald",
    "reno gazette journal",
    "yakima herald-republic",
    "the berkshire eagle",
    "brattleboro reformer",
}


def _keywords(text):
    words = re.findall("[a-zA-Z]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


def cmd_link(conn, top_n=56):
    """Attach to each OS its top_n signals by keyword overlap.

    Linking only ever adds rows, so links made before a source joined
    NON_TECH_SOURCES are removed first; otherwise the filter would only
    apply to new links.
    """
    placeholders = ",".join("?" * len(NON_TECH_SOURCES))
    removed = conn.execute(
        f"""DELETE FROM opportunity_signals WHERE signal_id IN (
               SELECT id FROM signals
               WHERE lower(trim(coalesce(source_name, ''))) IN ({placeholders}))""",
        sorted(NON_TECH_SOURCES),
    ).rowcount
    conn.commit()
    if removed:
        print(f"Removed {removed} existing link(s) to non-tech sources.\n")
    spaces = get_all_opportunity_spaces(conn)
    for os_row in spaces:
        target_keywords = _keywords(f"{os_row['use_case']} {os_row['technology']}")
        signals = get_signals_for_vertical(conn, os_row["vertical"])
        signals = [
            s
            for s in signals
            if (s["source_name"] or "").strip().lower() not in NON_TECH_SOURCES
        ]
        scored = []
        for s in signals:
            overlap = len(target_keywords & _keywords(s["title"] or ""))
            if overlap > 0:
                scored.append((overlap, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_n]
        for _, s in top:
            link_signal_to_opportunity(conn, os_row["id"], s["id"])
        print(
            f"{os_row['label']} ({os_row['use_case']} x {os_row['technology']}): linked {len(top)} signals"
        )
        for overlap, s in top:
            print(f"  [{overlap} kw match] ({s['source_name']}) {s['title']}")
        if not top:
            print("  -- no keyword overlap found, this OS has no grounded evidence yet")
        print()


def cmd_themes(conn, output_path="candidate_opportunity_spaces.md"):
    verticals = [
        r["vertical_hint"]
        for r in conn.execute(
            "SELECT DISTINCT vertical_hint FROM signals WHERE vertical_hint IS NOT NULL"
        ).fetchall()
    ]
    already_registered = {
        (r["vertical"], r["use_case"], r["technology"])
        for r in conn.execute(
            "SELECT vertical, use_case, technology FROM opportunity_spaces"
        ).fetchall()
    }
    run_id = new_run_id()
    lines = [
        "# Candidate Opportunity Spaces — unfiltered",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from current signals, no pre-selection applied._",
    ]
    for vertical in verticals:
        print(f"Extracting themes for {vertical}...")
        themes, candidates = extract_themes(conn, vertical)
        lines.append(f"## {vertical}")
        lines.append("")
        if not themes:
            lines.append(
                "_No themes extracted (too few signals, or LLM call failed) -- see console output._"
            )
            lines.append("")
            continue
        for t in themes:
            use_case = t.get("use_case", "?")
            technology = t.get("technology", "?")
            count = t.get("supporting_signal_count", "?")
            rationale = t.get("rationale", "")
            tag = (
                " **[already registered]**"
                if (vertical, use_case, technology) in already_registered
                else ""
            )
            lines.append(f"- **{use_case} × {technology}**{tag} ({count} signals)")
            lines.append(f"  {rationale}")
        lines.append("")
        if themes:
            track_valid_themes(conn, vertical, themes, run_id=run_id)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nWritten: {output_path}")
    print(
        "Recurring themes updated -- run `radar_cli.py watchlist` to see what's promotable, or `radar_cli.py promote` to register anything that's ready."
    )


def cmd_scores(conn):
    rows = get_latest_scores(conn)
    if not rows:
        print(
            "No scored opportunity spaces yet -- run `radar_cli.py create` then `python -m pipeline.scoring`."
        )
        return
    for r in rows:
        print(f"{r['label']} ({r['vertical']} x {r['use_case']} x {r['technology']})")
        print(
            f"  Attractiveness: {r['total_score']}/10  (strategic_relevance={r['strategic_relevance']}, evidence_quality={r['evidence_quality']})"
        )
        print(f"  Urgency:        {r['urgency_score']}/10")
        print(
            f"  Right-to-win:   {r['right_to_win_score']}/10  [{r['portfolio_distance']}]  assets: {r['matched_assets'] or 'none'}"
        )
        print(f"  -> {r['justification']}")
        print()


def cmd_summary(conn, output_path="opportunity_spaces_summary.md", top_n=None):
    """Write the client-facing markdown summary of scored opportunity spaces.

    "Not yet scored" is computed against every scored OS, before the --top
    cut, so an OS left out by --top is never reported as unscored.
    """
    scored_rows = get_latest_scores(conn)
    if not scored_rows:
        print(
            "No scored opportunity spaces yet -- run `radar_cli.py create` then `python -m pipeline.scoring`."
        )
        return
    rows = scored_rows
    if top_n:
        rows = sorted(rows, key=lambda r: r["total_score"], reverse=True)[:top_n]
        print(
            f"--top {top_n}: keeping the {len(rows)} highest-attractiveness opportunity space(s) out of {len(scored_rows)} scored."
        )
    all_spaces = get_all_opportunity_spaces(conn)
    scored_ids = {r["id"] for r in scored_rows}
    unscored = [s for s in all_spaces if s["id"] not in scored_ids]
    header = f"_{len(scored_rows)}/{len(all_spaces)} opportunity spaces scored"
    if len(rows) < len(scored_rows):
        header += f", top {len(rows)} by attractiveness shown"
    if unscored:
        header += f" -- {len(unscored)} not yet scored, see bottom of file"
    lines = [
        "# Innovation Radar — Opportunity Spaces Summary",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        header + "._",
        "",
        "| OS | Attractiveness | Right-to-win | Distance | Urgency |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['label']} | {r['total_score']}/10 | {r['right_to_win_score']}/10 | {r['portfolio_distance']} | {r['urgency_score']}/10 |"
        )
    lines.append("")
    lines.append("## Orange Business at a glance")
    lines.append("")
    for stat in CAPABILITY_STATS:
        lines.append(f"- **{stat['stat']}** _(source: {stat['source']})_")
    lines.append("")
    for r in rows:
        lines.append(
            f"## {r['label']} — {r['vertical']} × {r['use_case']} × {r['technology']}"
        )
        lines.append("")
        lines.append(f"**Attractiveness: {r['total_score']}/10**")
        lines.append(f"- Market signal strength: {round(r['market_signal_strength'], 2)}")
        lines.append(f"- Source diversity: {round(r['source_diversity'], 2)}")
        lines.append(
            f"- Evidence quality: {r['evidence_quality']} — {r['evidence_quality_justification']}"
        )
        lines.append(f"- Novelty / momentum: {r['novelty_momentum']}")
        lines.append(
            f"- Strategic relevance: {r['strategic_relevance']} — {r['strategic_relevance_justification']}"
        )
        lines.append("")
        lines.append(
            f"**Urgency: {r['urgency_score']}/10** — deterministic, +2 per regulation/buying_signal signal linked to this OS (is there a real deadline, separate from attractiveness)."
        )
        lines.append("")
        lines.append(
            f"**Right-to-win: {r['right_to_win_score']}/10 [{r['portfolio_distance']}]**"
        )
        lines.append(f"- Matched assets: {r['matched_assets'] or 'none'}")
        lines.append(f"- {r['justification']}")
        lines.append("")
        signals = conn.execute(
            "SELECT s.source_name, s.title FROM opportunity_signals link\n               JOIN signals s ON s.id = link.signal_id\n               WHERE link.opportunity_space_id = ?",
            (r["id"],),
        ).fetchall()
        lines.append(f"**Grounding signals ({len(signals)}):**")
        if signals:
            for s in signals:
                lines.append(f"- [{s['source_name']}] {s['title']}")
        else:
            lines.append("- _none linked yet — run `radar_cli.py link` first_")
        lines.append("")
    if unscored:
        lines.append("## Not yet scored")
        lines.append("")
        lines.append(
            f"{len(unscored)} opportunity space(s) have no score yet -- usually an interrupted `python -m pipeline.scoring` run (Groq quota). Run it again (unscored-only mode, safe to re-run) to fill these in before the next `summary`."
        )
        lines.append("")
        for s in unscored:
            lines.append(
                f"- {s['label']} — {s['vertical']} × {s['use_case']} × {s['technology']}"
            )
        lines.append("")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Written: {output_path}")


def cmd_all(force=False):
    steps = [
        [sys.executable, "-m", "pipeline.db"],
        [sys.executable, "-m", "pipeline.ingest"],
        [sys.executable, "-m", "pipeline.analyze"],
        [sys.executable, "-m", "pipeline.extend_taxonomy"],
        [sys.executable, __file__, "create"],
        [sys.executable, __file__, "promote"],
        [sys.executable, __file__, "link"],
        [sys.executable, "-m", "pipeline.scoring"] + (["--force"] if force else []),
        [sys.executable, __file__, "summary"],
    ]
    for i, step in enumerate(steps, 1):
        label = " ".join(step[1:])
        print(f"\n{'=' * 60}\nStep {i}/{len(steps)}: {label}\n{'=' * 60}")
        result = subprocess.run(step)
        if result.returncode != 0:
            print(f"\nStep {i} failed (exit code {result.returncode}) -- stopping.")
            sys.exit(result.returncode)
    print("\nDone. See opportunity_spaces_summary.md")


def main():
    parser = argparse.ArgumentParser(description="Innovation Radar pipeline tools")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("create").add_argument("--wipe", action="store_true")
    p_delete = sub.add_parser("delete")
    p_delete.add_argument(
        "labels", nargs="+", help='OS labels to delete, e.g. "OS001 OS024"'
    )
    sub.add_parser("promote")
    sub.add_parser("watchlist")
    sub.add_parser("calibrate")
    sub.add_parser("dedupe").add_argument("--apply", action="store_true")
    sub.add_parser("link")
    sub.add_parser("themes")
    sub.add_parser("scores")
    sub.add_parser("summary").add_argument(
        "--top",
        type=int,
        default=None,
        help="keep only the N highest-attractiveness opportunity spaces (default: all)",
    )
    sub.add_parser("all").add_argument("--force", action="store_true")
    sub.add_parser("review")
    args = parser.parse_args()
    if args.command == "all":
        cmd_all(force=args.force)
        return
    conn = get_connection()
    if args.command == "create":
        cmd_create(conn, wipe=args.wipe)
    elif args.command == "delete":
        cmd_delete(conn, args.labels)
    elif args.command == "promote":
        cmd_promote(conn)
    elif args.command == "watchlist":
        cmd_watchlist(conn)
    elif args.command == "calibrate":
        cmd_calibrate(conn)
    elif args.command == "dedupe":
        cmd_dedupe(conn, apply=args.apply)
    elif args.command == "link":
        cmd_link(conn)
    elif args.command == "themes":
        cmd_themes(conn)
    elif args.command == "scores":
        cmd_scores(conn)
    elif args.command == "summary":
        cmd_summary(conn, top_n=args.top)
    elif args.command == "review":
        cmd_review(conn)
    conn.close()


if __name__ == "__main__":
    main()