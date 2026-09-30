"""
radar_cli.py 
======================================

Single entry point for every one-off Innovation Radar pipeline tool.

Run:
    python radar_cli.py all [--force]        # runs the entire pipeline in order, one command
    python radar_cli.py create [--wipe]     # register CANDIDATES (pipeline/config.py) as opportunity spaces
    python radar_cli.py delete OS001 OS024  # permanently delete specific opportunity spaces (asks to confirm)
    python radar_cli.py promote             # auto-register recurring themes that hit the promotion threshold
    python radar_cli.py calibrate           # signal counts / source diversity, for tuning scoring.py's caps
    python radar_cli.py dedupe [--apply]    # find/remove near-duplicate signals (dry-run by default)
    python radar_cli.py watchlist           # show out-of-taxonomy terms + recurring themes, read-only
    python radar_cli.py link                # link the strongest real signals to each opportunity space
    python radar_cli.py themes              # regenerate candidate_opportunity_spaces.md, unfiltered
    python radar_cli.py scores              # print latest scores to console
    python radar_cli.py summary             # write opportunity_spaces_summary.md -- the file to bring to a meeting

WHAT CHANGED IN THIS REVISION
-------------------------------
- `create` now warns (doesn't block) when the vertical+use_case+technology
  triple you're about to register already exists under a different label. Adapted from a
  teammate's duplicate-check idea; reimplemented as a parameterized query
  (db.find_opportunity_space_by_triple) instead of an f-string-built SQL
  query, since the original interpolated vertical/use_case/technology
  straight into the SQL text.
- `discover` (which read emerging_themes.json) is integrated. Replaced by
  `promote`, which reads pipeline/db.py's recurring_themes table directly. 
- `watchlist` is a new read-only command to see what's pending/promotable
  in both watchlist_terms (out-of-taxonomy single terms) and
  recurring_themes (recurring valid themes) without re-running analyze.py.
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher

from pipeline.db import (
    get_connection, init_db, get_signals_for_vertical, get_all_opportunity_spaces,
    get_latest_scores, upsert_opportunity_space, new_run_id, wipe_opportunity_spaces,
    delete_opportunity_spaces, link_signal_to_opportunity, find_opportunity_space_by_triple,
    next_opportunity_space_label, list_watchlist, list_promotable_themes, mark_theme_promoted,
)
from pipeline.config import CANDIDATES, RECURRING_THEME_PROMOTION_THRESHOLD, CAPABILITY_STATS
from pipeline.analyze import extract_themes
from pipeline.signals_discovery import track_valid_themes


# ---------- create ----------

def cmd_create(conn, wipe=False):
    init_db()  # safe to re-run -- CREATE TABLE IF NOT EXISTS only, no data loss

    if wipe:
        existing_count = conn.execute("SELECT COUNT(*) as c FROM opportunity_spaces").fetchone()["c"]
        if existing_count > 0:
            print(f"--wipe: this will permanently delete {existing_count} existing opportunity space(s), "
                  f"their scores, right-to-win scores, and signal links.")
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
        # Duplicate check: same (vertical, use_case, technology) already
        # registered under a DIFFERENT label -- warn, don't block, so the
        # team can decide which to keep.
        dup = find_opportunity_space_by_triple(conn, vertical, use_case, technology, exclude_label=label)
        if dup:
            print(f"  WARNING: {vertical} x {use_case} x {technology} is already registered "
                  f"as {dup['label']} -- {label} looks like a duplicate.")
        os_id = upsert_opportunity_space(conn, run_id, label, vertical, use_case, technology)
        print(f"{label} (id={os_id}): {vertical} x {use_case} x {technology}")


# ---------- delete (targeted, unlike create --wipe which deletes everything) ----------

def cmd_delete(conn, labels):
    """Deletes specific opportunity spaces by label -- e.g. to resolve the
    OS001/OS013/OS024 duplicate without wiping the other 24. Asks for
    confirmation first since this is not undoable (no soft-delete)."""
    print(f"About to permanently delete {len(labels)} opportunity space(s): {', '.join(labels)}")
    print("This also removes their scores, right-to-win scores, and signal links. "
          "Signals themselves are NOT deleted.")
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


# ---------- promote (replaces the old JSON-based `discover`) ----------

def cmd_promote(conn):
    """Auto-registers every recurring theme that has hit
    RECURRING_THEME_PROMOTION_THRESHOLD as a new opportunity space. This is
    the growth mechanism for going past config.CANDIDATES' hand-picked
    list -- run this periodically as `radar_cli.py themes` (or
    `python -m pipeline.analyze`) keeps feeding recurring_themes."""
    promotable = list_promotable_themes(conn, RECURRING_THEME_PROMOTION_THRESHOLD)
    if not promotable:
        print(f"Nothing to promote yet (need frequency >= {RECURRING_THEME_PROMOTION_THRESHOLD}). "
              "Run `python -m pipeline.analyze` a few more times as new signals come in, "
              "or lower RECURRING_THEME_PROMOTION_THRESHOLD in pipeline/config.py.")
        return

    run_id = new_run_id()
    promoted_count = 0
    for theme in promotable:
        vertical, use_case, technology = theme["vertical"], theme["use_case"], theme["technology"]

        dup = find_opportunity_space_by_triple(conn, vertical, use_case, technology)
        if dup:
            # Already registered (e.g. via `create`'s CANDIDATES seed) --
            # mark promoted so it stops showing up here, but don't duplicate it.
            print(f"  {vertical} x {use_case} x {technology} already exists as {dup['label']} -- marking promoted, no new OS created.")
            mark_theme_promoted(conn, theme["id"])
            continue

        label = next_opportunity_space_label(conn)
        os_id = upsert_opportunity_space(conn, run_id, label, vertical, use_case, technology)
        mark_theme_promoted(conn, theme["id"])
        promoted_count += 1
        print(f"  PROMOTED {label} (id={os_id}): {vertical} x {use_case} x {technology} "
              f"(recurred {theme['frequency']}x)")

    print(f"\n{promoted_count} new opportunity space(s) promoted. "
          f"Run `python -m pipeline.scoring` to score them.")


# ---------- watchlist (read-only status) ----------

def cmd_watchlist(conn):
    print("=== Out-of-taxonomy terms (watchlist_terms) ===")
    pending = list_watchlist(conn, status="pending")
    proposed = list_watchlist(conn, status="proposed")
    if not pending and not proposed:
        print("  Nothing tracked yet.")
    for t in proposed:
        print(f"  [PROPOSED] [{t['category']}] \"{t['term']}\" -- seen {t['frequency']}x (vertical: {t['vertical']})")
    for t in pending:
        print(f"  [pending]  [{t['category']}] \"{t['term']}\" -- seen {t['frequency']}x (vertical: {t['vertical']})")

    print(f"\n=== Recurring themes (recurring_themes, promotion threshold = {RECURRING_THEME_PROMOTION_THRESHOLD}) ===")
    rows = conn.execute("SELECT * FROM recurring_themes ORDER BY frequency DESC").fetchall()
    if not rows:
        print("  Nothing tracked yet -- run `python -m pipeline.analyze` first.")
    for r in rows:
        status = "[already promoted]" if r["promoted"] else (
            "[PROMOTABLE -- run `radar_cli.py promote`]" if r["frequency"] >= RECURRING_THEME_PROMOTION_THRESHOLD
            else "[accumulating]"
        )
        print(f"  {r['vertical']} x {r['use_case']} x {r['technology']} -- seen {r['frequency']}x {status}")


# ---------- calibrate ----------

def cmd_calibrate(conn):
    print("Raw signal counts by vertical_hint (quick sanity check)")
    for r in conn.execute(
        "SELECT vertical_hint, COUNT(*) as n FROM signals GROUP BY vertical_hint ORDER BY n DESC"
    ):
        print(f"  {r['vertical_hint'] or '(untagged)'}: {r['n']}")
    total = conn.execute("SELECT COUNT(*) as n FROM signals").fetchone()["n"]
    print(f"  TOTAL: {total}")

    print("\n=== What scoring.py actually sees per vertical (use this to set caps) ===")
    verticals = [
        r["vertical"] for r in conn.execute("SELECT DISTINCT vertical FROM opportunity_spaces").fetchall()
    ]
    print(f"{'Vertical':<30} {'Signal count':<15} {'Distinct sources':<18}")
    print("-" * 65)
    for v in verticals:
        signals = get_signals_for_vertical(conn, v)
        distinct_sources = {s["source_name"] for s in signals}
        print(f"{v:<30} {len(signals):<15} {len(distinct_sources):<18}")


# ---------- dedupe ----------

SIMILARITY_THRESHOLD = 0.85  # 0-1, higher = stricter match required


def _normalize_title(title):
    t = title.lower()
    t = re.sub(r"\s*-\s*[a-z0-9 .]+$", "", t)  # strip trailing " - Some Source"
    t = re.sub(r"\s+", " ", t).strip()
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
        for sig_b, norm_b in normalized[i + 1:]:
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
    total_removable = sum(len(g) - 1 for g in groups)
    print(f"Found {len(groups)} duplicate groups, {total_removable} removable signals:\n")
    for group in groups:
        print(f"  KEEP: [{group[0]['source_name']}] {group[0]['title']}  ({group[0]['collected_at']})")
        for dup in group[1:]:
            print(f"  DROP: [{dup['source_name']}] {dup['title']}  ({dup['collected_at']})")
        print()

    if apply:
        total = 0
        for group in groups:
            for dup in group[1:]:
                conn.execute("DELETE FROM signals WHERE id = ?", (dup["id"],))
                conn.execute("DELETE FROM opportunity_signals WHERE signal_id = ?", (dup["id"],))
                total += 1
        conn.commit()
        print(f"Deleted {total} near-duplicate signals.")
        print("\nRe-run `radar_cli.py calibrate`, `python -m pipeline.scoring`, "
              "`radar_cli.py link` and `radar_cli.py summary` to refresh with the cleaned data.")
    else:
        print(f"Total: {total_removable} signals would be removed. Re-run with --apply to actually delete them.")


# ---------- link ----------

STOPWORDS = {"and", "the", "for", "with", "of", "in", "on", "a", "an", "to", "x"}


def _keywords(text):
    words = re.findall(r"[a-zA-Z]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


def cmd_link(conn, top_n=15):
    spaces = get_all_opportunity_spaces(conn)
    for os_row in spaces:
        target_keywords = _keywords(f"{os_row['use_case']} {os_row['technology']}")
        signals = get_signals_for_vertical(conn, os_row["vertical"])

        scored = []
        for s in signals:
            overlap = len(target_keywords & _keywords(s["title"] or ""))
            if overlap > 0:
                scored.append((overlap, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_n]
        for _, s in top:
            link_signal_to_opportunity(conn, os_row["id"], s["id"])

        print(f"{os_row['label']} ({os_row['use_case']} x {os_row['technology']}): linked {len(top)} signals")
        for overlap, s in top:
            print(f"  [{overlap} kw match] ({s['source_name']}) {s['title']}")
        if not top:
            print("  -- no keyword overlap found, this OS has no grounded evidence yet")
        print()


# ---------- themes ----------

def cmd_themes(conn, output_path="candidate_opportunity_spaces.md"):
    verticals = [r["vertical_hint"] for r in conn.execute(
        "SELECT DISTINCT vertical_hint FROM signals WHERE vertical_hint IS NOT NULL"
    ).fetchall()]
    already_registered = {
        (r["vertical"], r["use_case"], r["technology"])
        for r in conn.execute("SELECT vertical, use_case, technology FROM opportunity_spaces").fetchall()
    }

    run_id = new_run_id()
    lines = [
        "# Candidate Opportunity Spaces — unfiltered",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from current signals, "
        f"no pre-selection applied._",
    ]
    for vertical in verticals:
        print(f"Extracting themes for {vertical}...")
        themes, candidates = extract_themes(conn, vertical)
        lines.append(f"## {vertical}")
        lines.append("")
        if not themes:
            lines.append("_No themes extracted (too few signals, or LLM call failed) -- see console output._")
            lines.append("")
            continue
        for t in themes:
            use_case = t.get("use_case", "?")
            technology = t.get("technology", "?")
            count = t.get("supporting_signal_count", "?")
            rationale = t.get("rationale", "")
            tag = " **[already registered]**" if (vertical, use_case, technology) in already_registered else ""
            lines.append(f"- **{use_case} × {technology}**{tag} ({count} signals)")
            lines.append(f"  {rationale}")
        lines.append("")

        # Track valid themes in-memory (no JSON file) so recurring ones can
        # later be auto-registered with `radar_cli.py promote`.
        if themes:
            track_valid_themes(conn, vertical, themes, run_id=run_id)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nWritten: {output_path}")
    print("Recurring themes updated -- run `radar_cli.py watchlist` to see what's promotable, "
          "or `radar_cli.py promote` to register anything that's ready.")


# ---------- scores ----------

def cmd_scores(conn):
    rows = get_latest_scores(conn)
    if not rows:
        print("No scored opportunity spaces yet -- run `radar_cli.py create` then `python -m pipeline.scoring`.")
        return
    for r in rows:
        print(f"{r['label']} ({r['vertical']} x {r['use_case']} x {r['technology']})")
        print(f"  Attractiveness: {r['total_score']}/10  "
              f"(strategic_relevance={r['strategic_relevance']}, evidence_quality={r['evidence_quality']})")
        print(f"  Urgency:        {r['urgency_score']}/10")
        print(f"  Right-to-win:   {r['right_to_win_score']}/10  [{r['portfolio_distance']}]  "
              f"assets: {r['matched_assets'] or 'none'}")
        print(f"  -> {r['justification']}")
        print()


# ---------- summary ----------

# 23/08 Sieg, summary was dumping ALL scored OS (62 and growing) into the
# client-facing doc -- added --top so a 10-min pitch doesn't have to scroll
# past every single one. Sorted by attractiveness (total_score) descending;
# None (default) keeps the old "show everything" behavior for internal use.
def cmd_summary(conn, output_path="opportunity_spaces_summary.md", top_n=None):
    rows = get_latest_scores(conn)
    if not rows:
        print("No scored opportunity spaces yet -- run `radar_cli.py create` then `python -m pipeline.scoring`.")
        return

    if top_n:
        rows = sorted(rows, key=lambda r: r["total_score"], reverse=True)[:top_n]  # or rows = sorted(rows, key=lambda r: r["right_to_win_score"], reverse=True)[:top_n] --> (right to win)
        print(f"--top {top_n}: keeping the {len(rows)} highest-attractiveness opportunity space(s) "
              f"out of {len(get_latest_scores(conn))} scored.")

    lines = [
        "# Innovation Radar — Opportunity Spaces Summary",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "| OS | Attractiveness | Right-to-win | Distance | Urgency |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['label']} | {r['total_score']}/10 | {r['right_to_win_score']}/10 | "
                      f"{r['portfolio_distance']} | {r['urgency_score']}/10 |")
    lines.append("")

    # --- Orange Business at a glance -- previously CAPABILITY_STATS only ever
    # fed the right-to-win LLM prompt and never appeared in this client-facing
    # document at all. Surfaced here once, up front, as shared reference facts
    # rather than repeated inside every single OS section below.
    lines.append("## Orange Business at a glance")
    lines.append("")
    for stat in CAPABILITY_STATS:
        lines.append(f"- **{stat['stat']}** _(source: {stat['source']})_")
    lines.append("")

    for r in rows:
        lines.append(f"## {r['label']} — {r['vertical']} × {r['use_case']} × {r['technology']}")
        lines.append("")
        lines.append(f"**Attractiveness: {r['total_score']}/10**")
        lines.append(f"- Market signal strength: {r['market_signal_strength']}")
        lines.append(f"- Source diversity: {r['source_diversity']}")
        lines.append(f"- Evidence quality: {r['evidence_quality']} — {r['evidence_quality_justification']}")
        lines.append(f"- Novelty / momentum: {r['novelty_momentum']}")
        lines.append(f"- Strategic relevance: {r['strategic_relevance']} — {r['strategic_relevance_justification']}")
        lines.append("")
        # Same wording as radar_cli.py; describes pipeline/scoring.py::_urgency_weighted.
        lines.append(f"**Urgency: {r['urgency_score']}/10** — deterministic: each linked regulation signal counts "
                      f"in full, each buying signal (tender) fades with age, plus a novelty term; scaled against "
                      f"the 95th percentile across opportunity spaces (is there a real deadline, separate from "
                      f"attractiveness).")
        lines.append("")
        lines.append(f"**Right-to-win: {r['right_to_win_score']}/10 [{r['portfolio_distance']}]**")
        lines.append(f"- Matched assets: {r['matched_assets'] or 'none'}")
        lines.append(f"- {r['justification']}")
        lines.append("")

        signals = conn.execute(
            """SELECT s.source_name, s.title FROM opportunity_signals link
               JOIN signals s ON s.id = link.signal_id
               WHERE link.opportunity_space_id = ?""",
            (r["id"],),
        ).fetchall()
        lines.append(f"**Grounding signals ({len(signals)}):**")
        if signals:
            for s in signals:
                lines.append(f"- [{s['source_name']}] {s['title']}")
        else:
            lines.append("- _none linked yet — run `radar_cli.py link` first_")
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Written: {output_path}")


# ---------- all (runs the full pipeline in one command) ----------

def cmd_all(force=False):
    """Runs the full pipeline end to end, in order -- same steps as
    run_pipeline.ps1, but as a real subcommand so there's nothing extra to
    maintain. Each step is a separate process (mirrors running them by hand),
    and the whole thing stops at the first failure instead of silently
    continuing with stale or partial data."""
    steps = [
        [sys.executable, "-m", "pipeline.db"],
        [sys.executable, "-m", "pipeline.ingest"],
        [sys.executable, "-m", "pipeline.analyze"],
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


# ---------- dispatch ----------

def main():
    parser = argparse.ArgumentParser(description="Innovation Radar pipeline tools")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("create").add_argument("--wipe", action="store_true")
    p_delete = sub.add_parser("delete")
    p_delete.add_argument("labels", nargs="+", help='OS labels to delete, e.g. "OS001 OS024"')
    sub.add_parser("promote")
    sub.add_parser("watchlist")
    sub.add_parser("calibrate")
    sub.add_parser("dedupe").add_argument("--apply", action="store_true")
    sub.add_parser("link")
    sub.add_parser("themes")
    sub.add_parser("scores")
    sub.add_parser("summary").add_argument("--top", type=int, default=None,
        help="keep only the N highest-attractiveness opportunity spaces (default: all)")
    sub.add_parser("all").add_argument("--force", action="store_true")

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

    conn.close()


if __name__ == "__main__":
    main()