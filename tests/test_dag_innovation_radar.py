"""Structural checks for the Innovation Radar Airflow DAG.

This does not run the scheduler, touch the network or execute any stage. It
imports the DAG object and asserts what breaks silently when the file is
edited: the set of task ids, the dependency chain between them, and that every
task callable moves to the repo root before it runs (the pipeline's
`DB_PATH` is the relative "radar.db").

Airflow is a heavy, optional dependency kept out of requirements.txt (see
requirements-airflow.txt), and the repo's own venv runs a Python it does not
support. So when `airflow` is not importable, the `dag` fixture installs a
minimal stub into sys.modules: a `DAG` context manager and a `PythonOperator`
that records its task id and callable and supports `>>`. The stub covers only
what dags/innovation_radar.py and these tests use. With the real Airflow
installed (the dag job in .github/workflows/tests.yml), the stub is skipped
and the same tests run against the real classes.
"""
import importlib
import os
import sys
import types

import pytest


EXPECTED_CHAIN = [
    "init_db",
    "ingest",
    "analyze",
    "extend_taxonomy",
    "create",
    "promote",
    "link",
    "score",
    "summary",
]


# --- minimal Airflow stand-in --------------------------------------------


class _StubDAG:
    """`with DAG(...) as dag:` plus the attributes the tests read."""

    current = None

    def __init__(self, dag_id, **kwargs):
        self.dag_id = dag_id
        self.catchup = kwargs.get("catchup", True)
        self.tasks = []

    def __enter__(self):
        _StubDAG.current = self
        return self

    def __exit__(self, *exc):
        _StubDAG.current = None
        return False

    @property
    def task_ids(self):
        return [t.task_id for t in self.tasks]

    def get_task(self, task_id):
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(task_id)


class _StubPythonOperator:
    """Records task_id and python_callable, registers on the open DAG, chains with `>>`."""

    def __init__(self, task_id, python_callable, **kwargs):
        self.task_id = task_id
        self.python_callable = python_callable
        self.upstream_task_ids = set()
        self.downstream_task_ids = set()
        if _StubDAG.current is not None:
            _StubDAG.current.tasks.append(self)

    def __rshift__(self, other):
        self.downstream_task_ids.add(other.task_id)
        other.upstream_task_ids.add(self.task_id)
        return other


def _install_airflow_stub(monkeypatch):
    """Put a stub `airflow` package into sys.modules, unless the real one imports."""
    try:
        importlib.import_module("airflow")
        return False
    except ImportError:
        pass
    airflow = types.ModuleType("airflow")
    airflow.DAG = _StubDAG
    operators = types.ModuleType("airflow.operators")
    python = types.ModuleType("airflow.operators.python")
    python.PythonOperator = _StubPythonOperator
    airflow.operators = operators
    operators.python = python
    monkeypatch.setitem(sys.modules, "airflow", airflow)
    monkeypatch.setitem(sys.modules, "airflow.operators", operators)
    monkeypatch.setitem(sys.modules, "airflow.operators.python", python)
    return True


@pytest.fixture(scope="module")
def dag():
    with pytest.MonkeyPatch.context() as mp:
        _install_airflow_stub(mp)
        # A fresh import, so the DAG binds to whichever Airflow is in place.
        mp.delitem(sys.modules, "dags.innovation_radar", raising=False)
        from dags.innovation_radar import dag as radar_dag

        yield radar_dag


# --- tests ------------------------------------------------------------------


def test_dag_id(dag):
    assert dag.dag_id == "innovation_radar"


def test_task_ids_match_pipeline_stages(dag):
    assert set(dag.task_ids) == set(EXPECTED_CHAIN)


def test_catchup_disabled(dag):
    assert dag.catchup is False


def test_dependency_chain(dag):
    """Each stage must run strictly after the previous one."""
    for upstream_id, downstream_id in zip(EXPECTED_CHAIN, EXPECTED_CHAIN[1:]):
        upstream = dag.get_task(upstream_id)
        downstream = dag.get_task(downstream_id)
        assert downstream_id in upstream.downstream_task_ids, (
            f"{downstream_id} should depend on {upstream_id}"
        )
        assert upstream_id in downstream.upstream_task_ids


def test_chain_endpoints(dag):
    """The chain is linear: one root (init_db) and one leaf (summary)."""
    roots = [t.task_id for t in dag.tasks if not t.upstream_task_ids]
    leaves = [t.task_id for t in dag.tasks if not t.downstream_task_ids]
    assert roots == ["init_db"]
    assert leaves == ["summary"]


def test_every_task_callable_is_wrapped_to_run_from_repo_root(dag):
    """All nine callables go through `_from_repo_root`, which keeps the stage name."""
    for task_id in EXPECTED_CHAIN:
        fn = dag.get_task(task_id).python_callable
        assert hasattr(fn, "__wrapped__"), f"{task_id} is not wrapped"
        assert fn.__name__ == f"_run_{task_id}"


def test_task_callable_moves_to_repo_root_before_the_stage_runs(dag, monkeypatch):
    """The chdir must happen first, or init_db creates an empty radar.db in the worker's cwd."""
    import pipeline.db
    from dags.innovation_radar import REPO_ROOT

    calls = []
    monkeypatch.setattr(os, "chdir", calls.append)
    monkeypatch.setattr(pipeline.db, "init_db", lambda: calls.append("init_db"))

    dag.get_task("init_db").python_callable()

    assert calls == [REPO_ROOT, "init_db"]
    assert (REPO_ROOT / "radar_cli.py").is_file()
