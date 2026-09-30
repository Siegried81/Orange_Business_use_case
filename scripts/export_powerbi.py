"""
Exports radar.db to the CSV files the Power BI dashboard reads.

Power BI has no built-in SQLite connector, so the dashboard reads
app/powerbi_data/*.csv instead. Run this after every pipeline run, then hit
Refresh in Power BI Desktop.

Run:
    python scripts/export_powerbi.py
"""

import csv
import html
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "radar.db")
OUT_DIR = os.path.join(ROOT, "app", "powerbi_data")
MODEL_PATH = os.path.join(
    ROOT, "app", "innovation_radar_dashboard.SemanticModel", "model.bim"
)


def to_iso(value):
    """Normalise the mixed date formats found in the DB (ISO with offset,
    RFC 822 from RSS feeds, plain dates) to 'YYYY-MM-DD HH:MM:SS' in UTC.
    Returns '' when the value cannot be parsed, so Power BI loads a null."""
    if not value:
        return ""
    text = str(value).strip()
    dt = None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            pass
    if dt is None:
        try:
            dt = datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


LATEST_SCORES_SQL = """
    SELECT s.id, os.id AS opportunity_space_id,
           s.market_signal_strength, s.source_diversity, s.evidence_quality,
           s.evidence_quality_justification, s.novelty_momentum,
           s.strategic_relevance, s.strategic_relevance_justification,
           s.urgency_score, s.total_score, s.computed_at,
           r.id AS right_to_win_id, r.portfolio_distance, r.right_to_win_score,
           r.matched_assets, r.justification,
           r.computed_at AS right_to_win_computed_at
    FROM opportunity_spaces os
    JOIN scores s ON s.id = (
        SELECT id FROM scores WHERE opportunity_space_id = os.id
        ORDER BY computed_at DESC, id DESC LIMIT 1
    )
    LEFT JOIN right_to_win_scores r ON r.id = (
        SELECT id FROM right_to_win_scores WHERE opportunity_space_id = os.id
        ORDER BY computed_at DESC, id DESC LIMIT 1
    )
    ORDER BY os.id
"""

OPPORTUNITY_SPACES_SQL = """
    SELECT id, run_id, label, vertical, use_case, technology, created_at,
           last_refreshed, persona, geography, horizon, next_action, domain,
           buyer_persona, next_action_strategist, next_action_sales,
           next_action_presales
    FROM opportunity_spaces ORDER BY id
"""

SIGNALS_SQL = """
    SELECT id, source_name, source_url, signal_type, title, summary,
           published_date, collected_at, vertical_hint
    FROM signals ORDER BY id
"""

OPPORTUNITY_SIGNALS_SQL = """
    SELECT opportunity_space_id, signal_id FROM opportunity_signals
    ORDER BY opportunity_space_id, signal_id
"""

DATE_COLUMNS = {
    "run_id", "created_at", "last_refreshed", "computed_at",
    "right_to_win_computed_at", "collected_at",
}


def write_csv(name, header, rows):
    """Write one CSV into OUT_DIR. None is written as an empty cell, which the
    Power Query steps turn into null: a missing value never becomes 0."""
    path = os.path.join(OUT_DIR, f"{name}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"  {name}.csv: {len(rows)} rows")


def export_query(conn, name, sql, extra=None, added_columns=()):
    """Run one SELECT and write it as <name>.csv, with dates normalised.

    `extra` may edit each row and add the columns named in `added_columns`.
    Those columns are declared up front so the header is the same even when
    the query returns no row (the model types every column it expects).
    Dates that are present but cannot be parsed are counted and reported,
    because they are exported as empty and would otherwise vanish unseen.
    """
    cursor = conn.execute(sql)
    header = [d[0] for d in cursor.description] + list(added_columns)
    rows = []
    unparsed = 0
    for raw in cursor.fetchall():
        row = dict(zip(header, raw))
        if extra:
            extra(row)
        for col in DATE_COLUMNS & row.keys():
            value = row[col]
            row[col] = to_iso(value)
            if value and not row[col]:
                unparsed += 1
        rows.append(row)
    write_csv(name, header, [[r.get(c) for c in header] for r in rows])
    if unparsed:
        print(f"  WARNING: {unparsed} date value(s) in {name} could not be parsed and were exported as empty.")
    return rows


def clean_signal(row):
    """Add `published_on` (a real date for time axes, empty when unknown) and
    turn the HTML snippets RSS feeds put in `summary` into plain text.
    The raw `published_date` text is kept for display."""
    iso = to_iso(row["published_date"])
    row["published_on"] = iso[:10] if iso else ""
    # RSS summaries arrive as HTML snippets; tables show them as plain text.
    text = re.sub(r"<[^>]+>", " ", row["summary"] or "")
    row["summary"] = re.sub(r"\s+", " ", html.unescape(text)).strip()


def model_data_folder(model_path=MODEL_PATH):
    """Return the DataFolder path stored in the Power BI model, or None.

    Power Query has no relative file paths, so the model holds an absolute
    folder that is only right on the machine that saved it.
    """
    if not os.path.exists(model_path):
        return None
    with open(model_path, encoding="utf-8") as f:
        model = json.load(f)
    for expr in model.get("model", {}).get("expressions", []):
        if expr.get("name") == "DataFolder":
            text = expr["expression"]
            text = "".join(text) if isinstance(text, list) else text
            match = re.match(r'\s*"([^"]*)"', text)
            return match.group(1) if match else None
    return None


def check_data_folder(model_path=MODEL_PATH, out_dir=OUT_DIR):
    """Warn when the model reads CSVs from another folder than this export.

    Returns True when they match. On a fresh clone they will not, and the
    report would refresh from a folder that does not exist; the warning
    prints the exact value to paste into the DataFolder parameter.
    """
    folder = model_data_folder(model_path)
    expected = os.path.normcase(os.path.normpath(out_dir))
    if folder and os.path.normcase(os.path.normpath(folder)) == expected:
        return True
    print(
        "  WARNING: the Power BI DataFolder parameter points to\n"
        f"    {folder}\n"
        "  Set it (Transform data > Manage parameters) to\n"
        f"    {os.path.join(out_dir, '')}"
    )
    return False


def main():
    """Export every table the report uses. The DB is opened read-only: this
    script must never be able to change the pipeline's data."""
    if not os.path.exists(DB_PATH):
        sys.exit(f"{DB_PATH} not found -- run the pipeline first.")
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = sqlite3.connect(Path(DB_PATH).as_uri() + "?mode=ro", uri=True)
    print(f"Exporting {DB_PATH} -> {OUT_DIR}")

    export_query(conn, "opportunity_spaces", OPPORTUNITY_SPACES_SQL)
    scores = export_query(conn, "latest_scores", LATEST_SCORES_SQL)
    export_query(
        conn, "signals", SIGNALS_SQL, extra=clean_signal, added_columns=("published_on",)
    )
    export_query(conn, "opportunity_signals", OPPORTUNITY_SIGNALS_SQL)

    # matched_assets is a comma-separated list; one row per asset lets a
    # slicer filter on a single asset instead of on the whole combination.
    asset_rows = sorted({
        (s["opportunity_space_id"], asset.strip())
        for s in scores
        for asset in (s["matched_assets"] or "").split(",")
        if asset.strip()
    })
    write_csv("opportunity_assets", ["opportunity_space_id", "asset"], asset_rows)

    write_csv("export_info", ["exported_at"], [[to_iso(datetime.now(timezone.utc).isoformat())]])
    conn.close()
    check_data_folder()


if __name__ == "__main__":
    main()
