"""Airflow DAG for the Innovation Radar batch pipeline.

Why this file exists: the pipeline already runs end to end from the command
line (`radar_cli.py all`, see `cmd_all`). This DAG models that same run as a
scheduled graph so it can be driven by Airflow instead of a manual CLI call,
without touching any pipeline logic. Each task is a thin wrapper that calls an
existing entry point; nothing here re-implements a stage.

The task order and dependencies mirror `radar_cli.cmd_all` exactly:

    init_db -> ingest -> analyze -> extend_taxonomy
            -> create -> promote -> link -> score -> summary

The stage functions are imported lazily inside each callable, not at module
top level, for two reasons. The scheduler re-parses every DAG file on a short
interval, and the pipeline imports (groq, feedparser, pandas, the SQLite layer)
are expensive and only needed when a task actually runs. It also lets the DAG
be imported for a structural test (task ids, dependencies) on a machine where
the pipeline's own dependencies are not installed.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG

# PythonOperator moved packages in Airflow 3; support both.
try:
    from airflow.providers.standard.operators.python import PythonOperator
except ImportError:  # Airflow 2.x
    from airflow.operators.python import PythonOperator

# The repo root (parent of this dags/ folder) must be importable so the task
# callables can reach `radar_cli` and the `pipeline` package regardless of how
# Airflow loads this file.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# --- task callables -------------------------------------------------------
# Each callable wraps one existing entry point. The CLI-command stages
# (create/promote/link/summary) are plain functions that take a live DB
# connection, so these wrappers open one and close it; the `pipeline.*` module
# stages manage their own connection internally, exactly as they do under
# `python -m pipeline.<stage>`.


def _run_init_db():
    """Create/migrate the SQLite schema (mirrors `python -m pipeline.db`)."""
    from pipeline.db import init_db

    init_db()


def _run_ingest():
    """Pull the ~9 market-signal sources (mirrors `python -m pipeline.ingest`)."""
    from pipeline.ingest import run_full_refresh

    run_full_refresh()


def _run_analyze():
    """Extract recurring themes from signals (mirrors `python -m pipeline.analyze`)."""
    from pipeline.analyze import run_full_analysis

    run_full_analysis()


def _run_extend_taxonomy():
    """Propose taxonomy extensions (mirrors `python -m pipeline.extend_taxonomy`)."""
    from pipeline.extend_taxonomy import run_extend_taxonomy

    run_extend_taxonomy()


def _run_create():
    """Create opportunity spaces from the taxonomy (mirrors `radar_cli.py create`)."""
    from pipeline.db import get_connection
    from radar_cli import cmd_create

    conn = get_connection()
    try:
        cmd_create(conn)
    finally:
        conn.close()


def _run_promote():
    """Promote promotable recurring themes (mirrors `radar_cli.py promote`)."""
    from pipeline.db import get_connection
    from radar_cli import cmd_promote

    conn = get_connection()
    try:
        cmd_promote(conn)
    finally:
        conn.close()


def _run_link():
    """Link signals to opportunity spaces (mirrors `radar_cli.py link`)."""
    from pipeline.db import get_connection
    from radar_cli import cmd_link

    conn = get_connection()
    try:
        cmd_link(conn)
    finally:
        conn.close()


def _run_score():
    """Score opportunity spaces (mirrors `python -m pipeline.scoring`).

    force=False scores only the unscored spaces and repairs incomplete ones,
    which is the safe-to-re-run default `cmd_all` uses without `--force`.

    score_all_opportunity_spaces returns a truthy "refused" when the LLM never
    answered. `python -m pipeline.scoring` treats that as a failed run (exit 1),
    and `cmd_all` then stops before summary. The DAG must do the same: raise so
    the task fails and the downstream summary does not run on an un-scored board.
    """
    from pipeline.scoring import score_all_opportunity_spaces

    refused = score_all_opportunity_spaces(force=False)
    if refused:
        raise RuntimeError("scoring refused: the LLM never answered (see logs)")


def _run_summary():
    """Write the client-facing markdown summary (mirrors `radar_cli.py summary`)."""
    from pipeline.db import get_connection
    from radar_cli import cmd_summary

    conn = get_connection()
    try:
        cmd_summary(conn)
    finally:
        conn.close()


# --- DAG definition -------------------------------------------------------

default_args = {
    "owner": "innovation-radar",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="innovation_radar",
    description="Innovation Radar batch pipeline: ingest -> link -> score -> promote.",
    # Daily is enough: the market-signal feeds refresh on the order of a day,
    # and the scoring stage calls a rate-limited LLM, so a tighter cadence buys
    # nothing. catchup=False because the pipeline reads the live state of each
    # source, not a per-day partition -- there is no backfill to replay.
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args=default_args,
    max_active_runs=1,
    tags=["innovation-radar", "batch"],
) as dag:
    init_db = PythonOperator(task_id="init_db", python_callable=_run_init_db)
    ingest = PythonOperator(task_id="ingest", python_callable=_run_ingest)
    analyze = PythonOperator(task_id="analyze", python_callable=_run_analyze)
    extend_taxonomy = PythonOperator(
        task_id="extend_taxonomy", python_callable=_run_extend_taxonomy
    )
    create = PythonOperator(task_id="create", python_callable=_run_create)
    promote = PythonOperator(task_id="promote", python_callable=_run_promote)
    link = PythonOperator(task_id="link", python_callable=_run_link)
    score = PythonOperator(task_id="score", python_callable=_run_score)
    summary = PythonOperator(task_id="summary", python_callable=_run_summary)

    (
        init_db
        >> ingest
        >> analyze
        >> extend_taxonomy
        >> create
        >> promote
        >> link
        >> score
        >> summary
    )
