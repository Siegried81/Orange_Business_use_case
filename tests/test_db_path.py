"""`pipeline.config.DB_PATH` must point at the repo's radar.db from any cwd.

A relative "radar.db" resolves against the working directory of whoever
imports the pipeline: an Airflow worker, a script started from scripts/, a
Streamlit server launched from elsewhere. sqlite3.connect() then creates an
empty database there without a word, and every stage reads and scores it.
These tests import the config in a fresh interpreter started from a temporary
directory, so the result does not depend on where pytest itself was run.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_PRINT_DB_PATH = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from pipeline.config import DB_PATH; print(DB_PATH)"
)


def _db_path_seen_from(cwd):
    out = subprocess.run(
        [sys.executable, "-c", _PRINT_DB_PATH, str(ROOT)],
        cwd=cwd, capture_output=True, text=True, check=True,
    )
    return Path(out.stdout.strip())


def test_db_path_is_the_repo_database_from_another_cwd(tmp_path):
    seen = _db_path_seen_from(tmp_path)
    assert seen.is_absolute()
    assert seen.resolve() == (ROOT / "radar.db").resolve()


def test_get_connection_from_another_cwd_creates_no_stray_database(tmp_path):
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from pipeline.db import get_connection; c = get_connection(); "
        "print(c.execute('SELECT COUNT(*) FROM opportunity_spaces').fetchone()[0]); "
        "c.close()"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, str(ROOT)],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert not (tmp_path / "radar.db").exists()
    assert out.returncode == 0, out.stderr
    assert int(out.stdout.strip()) > 0


def test_ingest_cooldown_file_is_in_the_repo_logs(tmp_path):
    """The source cooldowns live in logs/; relative, a run from elsewhere forgot them."""
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "import os; from pipeline import ingest; print(os.path.abspath(ingest.COOLDOWN_FILE))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, str(ROOT)],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    assert Path(out.stdout.strip()).resolve().parent == (ROOT / "logs").resolve()
