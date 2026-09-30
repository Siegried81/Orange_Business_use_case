"""Tests for scripts/export_powerbi.py, the radar.db -> CSV export Power BI reads.

What would be expensive to get wrong here is silent: a date format that
vanishes, a missing value that turns into 0, a header that loses a column the
model types, or a model pointing at another machine's folder.
"""

import csv
import importlib.util
import json
import os
import sqlite3

import pytest

from pipeline.db import SCHEMA, migrate_schema

_SPEC = importlib.util.spec_from_file_location(
    "export_powerbi",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "export_powerbi.py"),
)
export_powerbi = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(export_powerbi)


def _read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.reader(f))


class TestToIso:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("2026-08-30T10:00:00+02:00", "2026-08-30 08:00:00"),
            ("Tue, 25 Aug 2026 14:30:00 GMT", "2026-08-25 14:30:00"),
            ("20260801T120000Z", "2026-08-01 12:00:00"),
            ("2026-08-01", "2026-08-01 00:00:00"),
        ],
    )
    def test_known_formats_become_utc(self, value, expected):
        assert export_powerbi.to_iso(value) == expected

    @pytest.mark.parametrize("value", [None, "", "not a date"])
    def test_missing_or_unparseable_stays_empty(self, value):
        assert export_powerbi.to_iso(value) == ""


class TestExportQuery:
    @pytest.fixture
    def out_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(export_powerbi, "OUT_DIR", str(tmp_path))
        return tmp_path

    def test_added_column_is_in_header_even_with_no_rows(self, out_dir):
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        migrate_schema(conn)
        export_powerbi.export_query(
            conn,
            "signals",
            export_powerbi.SIGNALS_SQL,
            extra=export_powerbi.clean_signal,
            added_columns=("published_on",),
        )
        header = _read(out_dir / "signals.csv")[0]
        assert header[-1] == "published_on"

    def test_unparseable_date_is_reported_and_exported_empty(self, out_dir, capsys):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (id INTEGER, collected_at TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'yesterday'), (2, NULL)")
        export_powerbi.export_query(conn, "t", "SELECT id, collected_at FROM t")
        rows = _read(out_dir / "t.csv")
        assert rows[1:] == [["1", ""], ["2", ""]]
        # Only the value that was present counts as unparsed; NULL is just missing.
        assert "1 date value(s) in t could not be parsed" in capsys.readouterr().out

    def test_missing_score_is_written_empty_not_zero(self, out_dir):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (id INTEGER, score REAL)")
        conn.execute("INSERT INTO t VALUES (1, NULL), (2, 0.0)")
        export_powerbi.export_query(conn, "t", "SELECT id, score FROM t")
        assert _read(out_dir / "t.csv")[1:] == [["1", ""], ["2", "0.0"]]


class TestCleanSignal:
    def test_html_summary_becomes_plain_text_and_date_is_added(self):
        row = {
            "published_date": "Tue, 25 Aug 2026 14:30:00 GMT",
            "summary": "<p>Cloud &amp; AI</p>\n<b>news</b>",
        }
        export_powerbi.clean_signal(row)
        assert row["summary"] == "Cloud & AI news"
        assert row["published_on"] == "2026-08-25"

    def test_undated_signal_keeps_an_empty_date(self):
        row = {"published_date": None, "summary": None}
        export_powerbi.clean_signal(row)
        assert row["published_on"] == ""
        assert row["summary"] == ""


class TestDataFolder:
    def _model(self, tmp_path, folder):
        expression = f'"{folder}" meta [IsParameterQuery = true, Type = "Text"]'
        path = tmp_path / "model.bim"
        path.write_text(
            json.dumps({"model": {"expressions": [{"name": "DataFolder", "expression": expression}]}}),
            encoding="utf-8",
        )
        return str(path)

    def test_matching_folder_passes(self, tmp_path):
        out = tmp_path / "powerbi_data"
        model = self._model(tmp_path, os.path.join(str(out), ""))
        assert export_powerbi.check_data_folder(model, str(out)) is True

    def test_folder_from_another_machine_is_flagged(self, tmp_path, capsys):
        model = self._model(tmp_path, r"D:\\somewhere\\else\\")
        assert export_powerbi.check_data_folder(model, str(tmp_path)) is False
        assert "DataFolder parameter points to" in capsys.readouterr().out

    def test_repo_model_declares_a_data_folder(self):
        assert export_powerbi.model_data_folder() is not None


class TestMainIsReadOnly:
    def test_missing_db_exits_without_creating_it(self, tmp_path, monkeypatch):
        db = tmp_path / "radar.db"
        monkeypatch.setattr(export_powerbi, "DB_PATH", str(db))
        monkeypatch.setattr(export_powerbi, "OUT_DIR", str(tmp_path / "out"))
        with pytest.raises(SystemExit):
            export_powerbi.main()
        assert not db.exists()

    def test_main_writes_the_six_files_the_model_reads(self, tmp_path, monkeypatch):
        db = tmp_path / "radar.db"
        conn = sqlite3.connect(db)
        conn.executescript(SCHEMA)
        migrate_schema(conn)
        conn.close()
        out = tmp_path / "out"
        monkeypatch.setattr(export_powerbi, "DB_PATH", str(db))
        monkeypatch.setattr(export_powerbi, "OUT_DIR", str(out))
        monkeypatch.setattr(export_powerbi, "check_data_folder", lambda: True)
        export_powerbi.main()
        assert sorted(os.listdir(out)) == sorted(
            f"{n}.csv"
            for n in (
                "opportunity_spaces",
                "latest_scores",
                "signals",
                "opportunity_signals",
                "opportunity_assets",
                "export_info",
            )
        )
