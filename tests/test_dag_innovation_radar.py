"""Structural check for the Innovation Radar Airflow DAG.

This does not run the scheduler, touch the network or execute any stage. It
imports the DAG object and asserts the two things that break silently when the
file is edited: the set of task ids, and the dependency chain between them.

Airflow is an optional, heavy dependency that is not part of the pipeline's own
requirements (see requirements-airflow.txt), so the whole module skips cleanly
when it is not installed rather than failing the suite.
"""
import pytest

pytest.importorskip("airflow")


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


@pytest.fixture(scope="module")
def dag():
    from dags.innovation_radar import dag as radar_dag

    return radar_dag


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
