import json
import time
from uuid import uuid4

import pytest

from opswatch.agent import Agent
from opswatch.config import Config, Target
from opswatch.storage import Store
from opswatch.studio import Studio, StudioWorker, studio_snapshot
from opswatch.types import Observation


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    clock = [10000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    config = Config(
        data_dir=str(tmp_path),
        agent={"max_per_hour": 1},
        targets=[Target(id="web", name="Web", kind="http", url="https://example.com")],
    )
    store = Store(config.data_dir)
    studio = Studio(config, store)
    yield config, store, studio, clock
    store.close()


def dispatch(studio, action, payload=None):
    return studio.dispatch(action, payload or {}, str(uuid4()))


def seed_run(store):
    with store.db:
        store.db.execute(
            "INSERT INTO agent_runs(created,backend,model,response) "
            "VALUES (9900,'hermes','test','previous response')"
        )


async def test_exhausted_budget_keeps_work_queued_and_resumes_without_another_click(workspace, monkeypatch):
    config, store, studio, clock = workspace
    seed_run(store)
    request = dispatch(studio, "report_create")
    calls = []

    async def invoke(self, prompt):
        calls.append(prompt)
        return "Recorded evidence reviewed."

    monkeypatch.setattr(Agent, "invoke", invoke)
    assert await StudioWorker(config).run_once() is False
    read = dispatch(studio, "session_read", {"session_id": request["session"]["id"]})
    assert read["jobs"][0]["status"] == "queued"
    assert read["jobs"][0]["error"] is None
    assert read["jobs"][0]["retry_at"] == 13500
    assert "automatically" in read["messages"][-1]["content"]
    assert read["messages"][-1]["status"] == "queued"
    assert studio_snapshot(config)["worker"]["budget"]["available"] == 0
    assert calls == []
    clock[0] = 13501
    assert await StudioWorker(config).run_once() is True
    read = dispatch(studio, "session_read", {"session_id": request["session"]["id"]})
    assert read["jobs"][0]["status"] == "succeeded"
    assert read["messages"][-1]["content"] == "Recorded evidence reviewed."
    assert len(calls) == 1


async def test_new_budget_setting_unblocks_waiting_work_immediately(workspace, monkeypatch):
    config, store, studio, _ = workspace
    seed_run(store)
    queued = dispatch(studio, "report_create")
    await StudioWorker(config).run_once()
    config.agent.max_per_hour = 2

    async def invoke(self, prompt):
        return "Requested report."

    monkeypatch.setattr(Agent, "invoke", invoke)
    assert await StudioWorker(config).run_once()
    assert (
        dispatch(studio, "report_read", {"report_id": queued["report"]["id"]})["report"]["status"]
        == "succeeded"
    )


async def test_budget_race_returns_job_to_queue_instead_of_failure(workspace, monkeypatch):
    config, store, studio, _ = workspace
    request = dispatch(studio, "report_create")
    worker = StudioWorker(config)
    original = worker._reserve

    def reserve(studio):
        seed_run(store)
        return original(studio)

    monkeypatch.setattr(worker, "_reserve", reserve)
    await worker.run_once()
    read = dispatch(studio, "session_read", {"session_id": request["session"]["id"]})
    assert read["jobs"][0]["status"] == "queued"
    assert read["jobs"][0]["error"] is None
    assert store.db.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 1


async def test_existing_quota_failures_are_restored_without_duplicate_incident_runs(workspace):
    config, store, studio, _ = workspace
    item = store.observe(
        Observation("web", "probe", "security", False, "Repeated probes", {"requests": 12}, immediate=True),
        config,
    )
    requests = []
    for _ in range(2):
        queued = dispatch(studio, "incident_investigate", {"incident_ref": item["ref"]})
        with store.db:
            studio._finish(
                queued["job"],
                "failed",
                error=(
                    "Agent execution failed. Hourly agent limit reached. Recorded evidence remains available."
                ),
            )
        requests.append(queued)
    seed_run(store)
    await StudioWorker(config).run_once()
    jobs = [
        json.loads(row[0]) for row in store.db.execute("SELECT document FROM studio_jobs ORDER BY sequence")
    ]
    assert [job["status"] for job in jobs] == ["cancelled", "queued"]
    assert jobs[-1]["error"] is None
    assert store.db.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 1
    assert (
        dispatch(studio, "session_read", {"session_id": requests[-1]["session"]["id"]})["session"][
            "active_job_id"
        ]
        == jobs[-1]["id"]
    )


async def test_real_execution_errors_remain_failed(workspace, monkeypatch):
    config, _, studio, _ = workspace
    request = dispatch(studio, "report_create")

    async def invoke(self, prompt):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(Agent, "invoke", invoke)
    await StudioWorker(config).run_once()
    read = dispatch(studio, "session_read", {"session_id": request["session"]["id"]})
    assert read["jobs"][0]["status"] == "failed"
    assert "provider unavailable" in read["jobs"][0]["error"]


async def test_cancel_during_budget_reservation_stays_cancelled(workspace, monkeypatch):
    config, store, studio, _ = workspace
    request = dispatch(studio, "report_create")
    worker = StudioWorker(config)
    reserve = worker._reserve

    def cancel_then_reserve(current):
        other = Store(config.data_dir)
        try:
            dispatch(Studio(config, other), "job_cancel", {"job_id": request["job"]["id"]})
        finally:
            other.close()
        seed_run(store)
        return reserve(current)

    monkeypatch.setattr(worker, "_reserve", cancel_then_reserve)
    await worker.run_once()
    read = dispatch(studio, "session_read", {"session_id": request["session"]["id"]})
    assert read["jobs"][0]["status"] == "cancelled"
    assert read["session"]["active_job_id"] is None
    assert (
        dispatch(studio, "report_read", {"report_id": request["report"]["id"]})["report"]["status"]
        == "cancelled"
    )


async def test_superseded_quota_report_is_cancelled_consistently(workspace):
    config, store, studio, _ = workspace
    old = dispatch(studio, "report_create")
    with store.db:
        studio._finish(
            old["job"],
            "failed",
            error="Agent execution failed. Hourly agent limit reached. Recorded evidence remains available.",
        )
    new = dispatch(studio, "message_send", {"session_id": old["session"]["id"], "content": "Try again"})
    seed_run(store)
    await StudioWorker(config).run_once()
    report = dispatch(studio, "report_read", {"report_id": old["report"]["id"]})["report"]
    assert report["status"] == "cancelled"
    assert report["error"] is None
    read = dispatch(studio, "session_read", {"session_id": old["session"]["id"]})
    assert read["session"]["active_job_id"] == new["job"]["id"]
