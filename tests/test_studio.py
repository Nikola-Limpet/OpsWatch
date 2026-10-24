import asyncio
import importlib
import importlib.util
import json
import re
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from opswatch.agent import Agent
from opswatch.config import Config, Repair, RepairRepository, Target
from opswatch.storage import Store
from opswatch.types import Observation


def studio_module():
    assert importlib.util.find_spec("opswatch.studio"), "Persistent Studio is not implemented"
    return importlib.import_module("opswatch.studio")


def action(studio, name, payload=None, request_id=None):
    return studio.dispatch(name, payload or {}, request_id or str(uuid4()))


@pytest.fixture
def workspace(tmp_path):
    module = studio_module()
    config = Config(
        data_dir=str(tmp_path / "runtime"),
        targets=[
            Target(id="shop", name="Shop", kind="http", url="https://shop.example", repair_repo="shop"),
            Target(id="other", name="Other", kind="http", url="https://other.example"),
            Target(
                id="disabled", name="Disabled", kind="http", url="https://disabled.example", enabled=False
            ),
        ],
        repair=Repair(
            enabled=True,
            repositories=[
                RepairRepository(id="shop", github="owner/shop", checks=[["python", "-m", "pytest"]])
            ],
        ),
    )
    store = Store(config.data_dir)
    store.set("heartbeat", time.time())
    store.observe(
        Observation(
            "shop", "http", "availability", False, "Checkout unavailable", {"status": 503}, immediate=True
        ),
        config,
    )
    store.observe(
        Observation("other", "http", "availability", True, "Other is responding", {"latency_ms": 18}),
        config,
    )
    studio = module.Studio(config, store)
    yield module, config, store, studio
    store.close()


async def test_empty_reads_and_idle_worker_never_create_runtime_database(tmp_path):
    module = studio_module()
    config = Config(data_dir=str(tmp_path / "not-started"))
    result = module.studio_snapshot(config)
    assert result["sessions"] == []
    assert result["worker"]["status"] == "not_started"
    assert result["context"]["coverage"]["historical_measurements"] is False
    assert await module.StudioWorker(config).run_once() is False
    assert not Path(config.data_dir).exists()


def test_sessions_are_durable_and_request_retries_cannot_duplicate_or_change_actions(workspace):
    module, config, store, studio = workspace
    identity = str(uuid4())
    created = action(studio, "session_create", {"title": "Checkout"}, identity)
    assert action(studio, "session_create", {"title": "Checkout"}, identity) == created
    with pytest.raises(ValueError, match="already|different|conflict"):
        action(studio, "session_create", {"title": "Changed"}, identity)
    other = Store(config.data_dir)
    try:
        result = action(
            module.Studio(config, other), "session_read", {"session_id": created["session"]["id"]}
        )
        assert result["session"]["title"] == "Checkout"
        assert result["messages"] == []
        assert result["context"]["coverage"]["historical_measurements"] is False
    finally:
        other.close()
    assert module.studio_snapshot(config)["counts"]["sessions"] == 1


def test_removed_scope_and_unknown_identities_cannot_queue_work(workspace):
    _, _, _, studio = workspace
    with pytest.raises(ValueError, match="service|scope|configured"):
        action(studio, "session_create", {"scope": {"kind": "service", "id": "missing"}})
    with pytest.raises(ValueError, match="session|Session"):
        action(studio, "message_send", {"session_id": str(uuid4()), "content": "Investigate"})


async def test_messages_are_redacted_before_storage_and_long_replies_are_persistent(workspace, monkeypatch):
    module, config, store, studio = workspace
    monkeypatch.setenv("STUDIO_TEST_API_TOKEN", "private-studio-credential")
    session = action(studio, "session_create", {"scope": {"kind": "service", "id": "shop"}})["session"]
    request = str(uuid4())
    queued = action(
        studio,
        "message_send",
        {"session_id": session["id"], "content": "Inspect private-studio-credential and the checkout error"},
        request,
    )
    assert (
        action(
            studio,
            "message_send",
            {
                "session_id": session["id"],
                "content": "Inspect private-studio-credential and the checkout error",
            },
            request,
        )
        == queued
    )
    prompts = []

    async def invoke(self, prompt):
        prompts.append(prompt)
        return "Evidence supports checking the application exception. " * 70 + " private-studio-credential"

    monkeypatch.setattr(Agent, "invoke", invoke)
    assert await module.StudioWorker(config).run_once() is True
    read = action(studio, "session_read", {"session_id": session["id"]})
    assert len(read["messages"]) == 2
    assert read["jobs"][0]["status"] == "succeeded"
    assert len(read["messages"][1]["content"]) > 1800
    assert read["messages"][1]["status"] == "complete"
    assert "private-studio-credential" not in json.dumps(read)
    assert "private-studio-credential" not in prompts[0]
    assert "UNTRUSTED DATA" in prompts[0]
    assert "Checkout unavailable" in prompts[0]
    assert "Other is responding" not in prompts[0]
    assert store.db.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 1


async def test_queued_evidence_and_preference_revision_are_pinned(workspace, monkeypatch):
    module, config, _, studio = workspace
    session = action(studio, "session_create")["session"]
    action(studio, "message_send", {"session_id": session["id"], "content": "What should I check?"})
    saved = action(
        studio, "preferences_update", {"revision": 1, "notes": "Prefer explicit regression coverage."}
    )
    assert saved["preferences"]["revision"] == 2
    with pytest.raises(ValueError, match="revision|changed"):
        action(studio, "preferences_update", {"revision": 1, "notes": "overwrite"})
    prompts = []

    async def invoke(self, prompt):
        prompts.append(prompt)
        return "Check the recorded 503 and verify the deployment logs."

    monkeypatch.setattr(Agent, "invoke", invoke)
    await module.StudioWorker(config).run_once()
    document = json.loads(prompts[0].split("EVIDENCE DOCUMENT\n", 1)[1])
    assert document["preferences"]["revision"] == 1
    assert document["context"]["coverage"]["historical_measurements"] is False
    assert any(source["id"] == "OW-0001" for source in document["context"]["sources"])


async def test_reports_are_saved_only_after_successful_inference(workspace, monkeypatch):
    module, config, store, studio = workspace
    queued = action(studio, "report_create", {"period": "day"})
    assert queued["report"]["status"] == "queued"
    assert queued["report"]["content"] is None

    async def invoke(self, prompt):
        return "## Evidence\nShop returned 503.\n\n## Verify\nCheck the next health observation."

    monkeypatch.setattr(Agent, "invoke", invoke)
    await module.StudioWorker(config).run_once()
    report = action(studio, "report_read", {"report_id": queued["report"]["id"]})["report"]
    assert report["status"] == "succeeded"
    assert "Shop returned 503" in report["content"]
    assert report["sources"]
    assert report["preference_revision"] == 1
    store.prune(1)
    assert module.studio_snapshot(config)["reports"][0]["id"] == report["id"]


async def test_report_role_conversation_creates_a_real_saved_report(workspace, monkeypatch):
    module, config, _, studio = workspace
    session = action(studio, "session_create", {"role": "report"})["session"]
    queued = action(studio, "message_send", {"session_id": session["id"], "content": "Report on today"})
    assert queued["report"]["job_id"] == queued["job"]["id"]

    async def invoke(self, prompt):
        return "Recorded incident OW-0001 needs investigation. Historical uptime is unavailable."

    monkeypatch.setattr(Agent, "invoke", invoke)
    await module.StudioWorker(config).run_once()
    assert module.studio_snapshot(config)["reports"][0]["status"] == "succeeded"


async def test_improvements_require_valid_structured_evidence_and_owner_acceptance(workspace, monkeypatch):
    module, config, _, studio = workspace
    action(studio, "improvement_create", {"focus": "Recurring checkout reliability"})

    async def invoke(self, prompt):
        return json.dumps(
            {
                "summary": "Investigate the recorded availability incident.",
                "problems": [
                    {
                        "title": "Investigate checkout 503",
                        "summary": "Determine whether the unavailable checkout follows a failed deployment.",
                        "priority": "high",
                        "evidence_refs": ["OW-0001"],
                        "validation": "A new health check succeeds and the cause is recorded.",
                    }
                ],
            }
        )

    monkeypatch.setattr(Agent, "invoke", invoke)
    await module.StudioWorker(config).run_once()
    overview = module.studio_snapshot(config)
    problem = overview["problems"][0]
    assert problem["status"] == "proposed"
    assert problem["evidence_refs"] == ["OW-0001"]
    accepted = action(studio, "problem_update", {"problem_id": problem["id"], "status": "accepted"})
    assert accepted["problem"]["status"] == "accepted"
    assert accepted["problem"]["revision"] == 2
    with pytest.raises(ValueError, match="revision|changed"):
        action(studio, "problem_update", {"problem_id": problem["id"], "status": "dismissed", "revision": 1})
    feedback = action(
        studio,
        "feedback",
        {"subject_type": "problem", "subject_id": problem["id"], "rating": "helpful", "comment": "Useful"},
    )
    assert feedback["feedback"]["rating"] == "helpful"
    assert module.studio_snapshot(config)["preferences"]["revision"] == 1


@pytest.mark.parametrize(
    "response",
    [
        "Here are some ideas, but no structured report.",
        '{"summary":"Guess","problems":[{"title":"Invented issue"}]}',
        json.dumps(
            {
                "summary": "Guess",
                "problems": [
                    {
                        "title": "Invented issue",
                        "summary": "No supplied source supports this.",
                        "priority": "high",
                        "evidence_refs": ["OW-9999"],
                        "validation": "Run a check.",
                    }
                ],
            }
        ),
    ],
)
async def test_invalid_improvement_output_fails_without_fabricated_proposals(
    workspace, monkeypatch, response
):
    module, config, _, studio = workspace
    queued = action(studio, "improvement_create")

    async def invoke(self, prompt):
        return response

    monkeypatch.setattr(Agent, "invoke", invoke)
    await module.StudioWorker(config).run_once()
    read = action(studio, "session_read", {"session_id": queued["session"]["id"]})
    assert read["jobs"][0]["status"] == "failed"
    assert read["jobs"][0]["error"]
    assert module.studio_snapshot(config)["problems"] == []


async def test_unavailable_provider_and_hourly_budget_fail_honestly(workspace, monkeypatch):
    module, config, store, studio = workspace
    config.agent.max_per_hour = 1
    first = action(studio, "report_create")

    async def invoke(self, prompt):
        raise RuntimeError("provider unavailable: token=private-value")

    monkeypatch.setattr(Agent, "invoke", invoke)
    await module.StudioWorker(config).run_once()
    report = action(studio, "report_read", {"report_id": first["report"]["id"]})["report"]
    assert report["status"] == "failed"
    assert report["content"] is None
    assert "private-value" not in json.dumps(report)
    second = action(studio, "report_create")
    await module.StudioWorker(config).run_once()
    read = action(studio, "session_read", {"session_id": second["session"]["id"]})
    assert read["jobs"][0]["status"] == "failed"
    assert "limit" in read["jobs"][0]["error"].lower()
    assert store.db.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 1


async def test_multiple_workers_cannot_execute_the_same_job(workspace, monkeypatch):
    module, config, store, studio = workspace
    queued = action(studio, "report_create")
    started, release = asyncio.Event(), asyncio.Event()

    async def invoke(self, prompt):
        started.set()
        await release.wait()
        return "Recorded evidence reviewed."

    monkeypatch.setattr(Agent, "invoke", invoke)
    first = asyncio.create_task(module.StudioWorker(config).run_once())
    await asyncio.wait_for(started.wait(), 2)
    try:
        assert await module.StudioWorker(config).run_once() is False
    finally:
        release.set()
        await first
    assert store.db.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 1
    assert (
        action(studio, "report_read", {"report_id": queued["report"]["id"]})["report"]["status"]
        == "succeeded"
    )


async def test_cancellation_during_inference_prevents_saving_an_answer(workspace, monkeypatch):
    module, config, _, studio = workspace
    queued = action(studio, "report_create")
    started, release = asyncio.Event(), asyncio.Event()

    async def invoke(self, prompt):
        started.set()
        await release.wait()
        return "This late answer must not overwrite cancellation."

    monkeypatch.setattr(Agent, "invoke", invoke)
    worker = asyncio.create_task(module.StudioWorker(config).run_once())
    await asyncio.wait_for(started.wait(), 2)
    action(studio, "job_cancel", {"job_id": queued["job"]["id"]})
    release.set()
    await worker
    read = action(studio, "report_read", {"report_id": queued["report"]["id"]})
    assert read["report"]["status"] == "cancelled"
    assert read["report"]["content"] is None


async def test_worker_shutdown_marks_the_inflight_job_interrupted(workspace, monkeypatch):
    module, config, _, studio = workspace
    queued = action(studio, "report_create")
    started = asyncio.Event()

    async def invoke(self, prompt):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(Agent, "invoke", invoke)
    task = asyncio.create_task(module.StudioWorker(config).run_once())
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (
        action(studio, "report_read", {"report_id": queued["report"]["id"]})["report"]["status"]
        == "interrupted"
    )


async def test_message_pages_keep_old_conversations_reachable(workspace, monkeypatch):
    module, config, _, studio = workspace
    config.agent.max_per_hour = 100
    session = action(studio, "session_create")["session"]

    async def invoke(self, prompt):
        return "Use the supplied evidence."

    monkeypatch.setattr(Agent, "invoke", invoke)
    for number in range(12):
        action(studio, "message_send", {"session_id": session["id"], "content": f"Question {number}"})
        await module.StudioWorker(config).run_once()
    first = action(studio, "session_read", {"session_id": session["id"], "limit": 20})
    second = action(studio, "session_read", {"session_id": session["id"], "before": first["next_before"]})
    assert len(first["messages"]) == 20
    assert first["has_more"] is True
    assert len(second["messages"]) == 4
    assert second["has_more"] is False
    assert second["messages"][0]["content"] == "Question 0"
    assert len({item["id"] for item in first["messages"] + second["messages"]}) == 24


def test_overview_is_read_only_and_does_not_hide_records_after_twenty_sessions(workspace):
    module, config, _, studio = workspace
    for number in range(25):
        action(studio, "session_create", {"title": f"Conversation {number}"})
    path = Path(config.data_dir) / "opswatch.sqlite3"
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        before = db.execute("PRAGMA data_version").fetchone()[0]
        result = module.studio_snapshot(config)
        assert len(result["sessions"]) == 25
        assert result["counts"]["sessions"] == 25
        assert result["has_more"]["sessions"] is False
        assert db.execute("PRAGMA data_version").fetchone()[0] == before


async def test_unicode_answers_and_pages_fit_the_cloud_result_limit(workspace, monkeypatch):
    module, config, _, studio = workspace
    config.agent.max_per_hour = 100
    session = action(studio, "session_create")["session"]

    async def invoke(self, prompt):
        return "調査" * 5900

    monkeypatch.setattr(Agent, "invoke", invoke)
    for _ in range(4):
        action(studio, "message_send", {"session_id": session["id"], "content": "Review the evidence"})
        await module.StudioWorker(config).run_once()
    first = action(studio, "session_read", {"session_id": session["id"]})
    assert len(json.dumps(first).encode()) <= 60000
    assert first["has_more"] is True
    assert first["next_before"]
    queued = action(studio, "report_create")
    await module.StudioWorker(config).run_once()
    report = action(studio, "report_read", {"report_id": queued["report"]["id"]})
    assert len(json.dumps(report).encode()) <= 60000
    assert report["report"]["status"] == "succeeded"


async def test_large_source_labels_do_not_overflow_report_detail(workspace, monkeypatch):
    module, config, _, studio = workspace
    for target in config.targets:
        target.name = "調査" * 100
    config.targets.extend(
        Target(id=f"service-{number}", name="調査" * 100, kind="http", url="https://example.com")
        for number in range(30)
    )
    queued = action(studio, "report_create")

    async def invoke(self, prompt):
        return "調査" * 5900

    monkeypatch.setattr(Agent, "invoke", invoke)
    await module.StudioWorker(config).run_once()
    result = action(studio, "report_read", {"report_id": queued["report"]["id"]})
    assert result["report"]["status"] == "succeeded"
    assert len(json.dumps(result).encode()) <= 60000


async def test_context_includes_applications_queries_repairs_and_feedback_without_private_paths(
    workspace, monkeypatch
):
    from opswatch.repairs import Repairs

    module, config, store, studio = workspace
    config.targets.extend(
        Target(id=f"service-{number}", name=f"App {number}", kind="http", url="https://example.com")
        for number in range(25)
    )
    store.observe(
        Observation("shop", "query:one", "query performance", False, "Slow order query", {"sql": "select 1"}),
        config,
    )
    repair = Repairs(config, store).enqueue("OW-0001", "owner")
    Repairs(config, store).update(
        repair["id"], "blocked", worktree="/private/worktree", summary="No code evidence"
    )
    session = action(studio, "session_create")["session"]
    queued = action(
        studio, "message_send", {"session_id": session["id"], "content": "Inspect my applications"}
    )

    async def invoke(self, prompt):
        return "Inspect the supplied evidence."

    monkeypatch.setattr(Agent, "invoke", invoke)
    await module.StudioWorker(config).run_once()
    action(
        studio,
        "feedback",
        {
            "subject_type": "message",
            "subject_id": queued["message"]["id"],
            "rating": "unhelpful",
            "comment": "Name the relevant service.",
        },
    )
    action(studio, "message_send", {"session_id": session["id"], "content": "Try again"})
    prompts = []

    async def revised(self, prompt):
        prompts.append(prompt)
        return "Shop has recorded availability evidence."

    monkeypatch.setattr(Agent, "invoke", revised)
    await module.StudioWorker(config).run_once()
    document = json.loads(prompts[0].split("EVIDENCE DOCUMENT\n", 1)[1])
    assert document["context"]["coverage"]["counts"]["services"] == 28
    assert len(document["context"]["services"]) == 28
    assert document["context"]["queries"][0]["title"] == "Slow order query"
    assert document["context"]["repairs"][0]["status"] == "blocked"
    assert document["feedback"][0]["comment"] == "Name the relevant service."
    assert "/private/worktree" not in prompts[0]
    assert document["preferences"]["revision"] == 1


async def test_removed_application_fails_queued_work_before_spending_agent_budget(workspace, monkeypatch):
    module, config, store, studio = workspace
    session = action(studio, "session_create", {"scope": {"kind": "service", "id": "shop"}})["session"]
    action(studio, "message_send", {"session_id": session["id"], "content": "Investigate"})
    config.targets = [target for target in config.targets if target.id != "shop"]

    async def invoke(self, prompt):
        raise AssertionError("Removed applications must not invoke a provider")

    monkeypatch.setattr(Agent, "invoke", invoke)
    await module.StudioWorker(config).run_once()
    read = action(studio, "session_read", {"session_id": session["id"]})
    assert read["jobs"][0]["status"] == "failed"
    assert store.db.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 0


async def test_normal_worker_stop_interrupts_work_without_raising(workspace, monkeypatch):
    module, config, _, studio = workspace
    queued = action(studio, "report_create")
    started, stop = asyncio.Event(), asyncio.Event()

    async def invoke(self, prompt):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(Agent, "invoke", invoke)
    task = asyncio.create_task(module.StudioWorker(config).serve(stop))
    await asyncio.wait_for(started.wait(), 2)
    stop.set()
    await asyncio.wait_for(task, 2)
    read = action(studio, "report_read", {"report_id": queued["report"]["id"]})
    assert read["report"]["status"] == "interrupted"


async def test_orphaned_running_job_is_recovered_after_process_restart(workspace, monkeypatch):
    module, config, store, studio = workspace
    queued = action(studio, "report_create")
    job = queued["job"] | {"status": "running"}
    with store.db:
        store.db.execute(
            "UPDATE studio_jobs SET status='running',document=? WHERE id=?", (json.dumps(job), job["id"])
        )

    async def invoke(self, prompt):
        raise AssertionError("Interrupted work must not invoke the provider again")

    monkeypatch.setattr(Agent, "invoke", invoke)
    assert await module.StudioWorker(config).run_once() is False
    report = action(studio, "report_read", {"report_id": queued["report"]["id"]})["report"]
    assert report["status"] == "interrupted"
    assert report["content"] is None


async def test_selected_pstack_playbook_is_pinned_and_used_as_read_only_guidance(workspace, monkeypatch):
    module, config, store, studio = workspace
    session = action(studio, "session_create", {"title": "Checkout bug", "playbook": "bug-fix"})["session"]
    assert session["playbook"] == "bug-fix"
    queued = action(
        studio, "message_send", {"session_id": session["id"], "content": "Plan the investigation"}
    )
    pinned = json.loads(
        store.db.execute("SELECT private FROM studio_jobs WHERE id=?", (queued["job"]["id"],)).fetchone()[0]
    )
    assert "9580c23696ac8d7abed0c166f2f9918800afb819" in json.dumps(pinned["pstack"])
    assert "Binary-search the cause" in json.dumps(pinned["pstack"])
    prompts = []

    async def invoke(self, prompt):
        prompts.append(prompt)
        return "OW-0001 establishes an availability symptom. The application cause needs more evidence."

    monkeypatch.setattr(Agent, "invoke", invoke)
    await module.StudioWorker(config).run_once()
    assert "Binary-search the cause" in prompts[0]
    assert "read-only" in prompts[0]
    assert "Cursor-only" in prompts[0]
    read = action(studio, "session_read", {"session_id": session["id"]})
    assert read["context"]["pstack"]["playbook"] == "bug-fix"
    assert "source" not in read["context"]["pstack"]
    assert module.studio_snapshot(config)["pstack"]["name"] == "pstack"


async def test_custom_report_distinguishes_current_checks_from_its_historical_window(workspace, monkeypatch):
    module, config, store, studio = workspace
    with store.db:
        store.db.execute("UPDATE incidents SET status='resolved',opened=200,updated=300 WHERE id=1")
    store.observe(
        Observation(
            "shop", "later", "availability", False, "An incident after the requested period", immediate=True
        ),
        config,
    )
    action(studio, "report_create", {"period": "custom", "window_start": 100, "window_end": 500})
    prompts = []

    async def invoke(self, prompt):
        prompts.append(prompt)
        return "The reporting window includes incident OW-0001. Current checks are outside that window."

    monkeypatch.setattr(Agent, "invoke", invoke)
    await module.StudioWorker(config).run_once()
    document = json.loads(prompts[0].split("EVIDENCE DOCUMENT\n", 1)[1])
    assert [incident["ref"] for incident in document["context"]["incidents"]] == ["OW-0001"]
    assert document["context"]["report_window"] == {"start": 100, "end": 500}
    assert all(check["in_report_window"] is False for check in document["context"]["checks"])


async def test_context_bounds_large_incident_history_and_reports_what_was_omitted(workspace, monkeypatch):
    module, config, store, studio = workspace
    for number in range(45):
        store.observe(
            Observation(
                "shop",
                f"error-{number}",
                "application error",
                False,
                f"Recorded failure {number}",
                immediate=True,
            ),
            config,
        )
    action(studio, "report_create")
    documents = []

    async def invoke(self, prompt):
        documents.append(json.loads(prompt.split("EVIDENCE DOCUMENT\n", 1)[1]))
        return "Review the included failures. Additional recorded incidents were outside the bounded context."

    monkeypatch.setattr(Agent, "invoke", invoke)
    await module.StudioWorker(config).run_once()
    coverage = documents[0]["context"]["coverage"]
    assert coverage["counts"]["incidents"] == 46
    assert coverage["included"]["incidents"] == 30
    assert coverage["truncated"] is True


async def test_improvement_review_can_conclude_that_no_proposal_is_justified(workspace, monkeypatch):
    module, config, _, studio = workspace
    queued = action(studio, "improvement_create")

    async def invoke(self, prompt):
        return (
            '{"summary":"The recorded evidence does not establish a code change to propose.","problems":[]}'
        )

    monkeypatch.setattr(Agent, "invoke", invoke)
    await module.StudioWorker(config).run_once()
    read = action(studio, "session_read", {"session_id": queued["session"]["id"]})
    assert read["jobs"][0]["status"] == "succeeded"
    assert read["jobs"][0]["problem_ids"] == []
    assert "does not establish" in read["messages"][-1]["content"]
    assert module.studio_snapshot(config)["problems"] == []


async def test_latest_saved_feedback_survives_reload_for_messages_reports_and_problems(
    workspace, monkeypatch
):
    module, config, _, studio = workspace
    queued = action(studio, "report_create")

    async def invoke(self, prompt):
        if '"problems":[' in prompt:
            return json.dumps(
                {
                    "summary": "A reliability review is justified.",
                    "problems": [
                        {
                            "title": "Investigate checkout availability",
                            "summary": "Shop has recorded incident OW-0001.",
                            "priority": "high",
                            "evidence_refs": ["OW-0001"],
                            "validation": "A fresh health observation succeeds.",
                        }
                    ],
                }
            )
        return "Recorded incident OW-0001 needs investigation."

    monkeypatch.setattr(Agent, "invoke", invoke)
    await module.StudioWorker(config).run_once()
    report_id, message_id = queued["report"]["id"], queued["message"]["id"]
    for subject_type, subject_id in (("message", message_id), ("report", report_id)):
        action(
            studio, "feedback", {"subject_type": subject_type, "subject_id": subject_id, "rating": "helpful"}
        )
        action(
            studio,
            "feedback",
            {"subject_type": subject_type, "subject_id": subject_id, "rating": "unhelpful"},
        )
    action(studio, "improvement_create")
    await module.StudioWorker(config).run_once()
    problem_id = module.studio_snapshot(config)["problems"][0]["id"]
    action(studio, "feedback", {"subject_type": "problem", "subject_id": problem_id, "rating": "helpful"})
    other = Store(config.data_dir)
    try:
        reopened = module.Studio(config, other)
        read = action(reopened, "session_read", {"session_id": queued["session"]["id"]})
        assert read["messages"][0]["feedback"] is None
        assert read["messages"][1]["feedback"] == "unhelpful"
        assert action(reopened, "report_read", {"report_id": report_id})["report"]["feedback"] == "unhelpful"
    finally:
        other.close()
    overview = module.studio_snapshot(config)
    assert overview["reports"][0]["feedback"] == "unhelpful"
    assert overview["problems"][0]["feedback"] == "helpful"


def test_collection_pages_reach_old_work_beyond_the_overview_limit(workspace):
    module, config, store, studio = workspace
    created = [
        action(studio, "session_create", {"title": f"Conversation {number}"})["session"]
        for number in range(125)
    ]
    # Ties require the UUID secondary key to avoid skipped or repeated records.
    with store.db:
        for session in created[-5:]:
            session["updated"] = 2000000000
            store.db.execute(
                "UPDATE studio_sessions SET document=? WHERE id=?", (json.dumps(session), session["id"])
            )
    overview = module.studio_snapshot(config)
    assert len(overview["sessions"]) == 100
    assert overview["has_more"]["sessions"] is True
    identities = []
    payload = {"collection": "sessions"}
    while True:
        page = action(studio, "collection_read", payload)
        assert len(json.dumps(page).encode()) <= 60000
        identities.extend(item["id"] for item in page["items"])
        if not page["has_more"]:
            break
        payload["before"] = page["next_before"]
    assert len(identities) == len(set(identities)) == 125
    assert created[0]["id"] in identities
    assert identities[:5] == sorted([item["id"] for item in created[-5:]], reverse=True)
    assert action(studio, "collection_read", {"collection": "reports"})["items"] == []
    with pytest.raises(ValueError, match="cursor|Cursor"):
        action(studio, "collection_read", {"collection": "sessions", "before": str(uuid4())})


async def test_saved_vote_is_returned_after_reopening_the_http_application(workspace, monkeypatch, tmp_path):
    from opswatch import dashboard

    module, config, _, studio = workspace
    assets = tmp_path / "assets"
    (assets / "web").mkdir(parents=True)
    (assets / "web" / "index.html").write_text('<meta name="opswatch-csrf" content="__OPSWATCH_CSRF__">')
    monkeypatch.setattr(dashboard, "ASSETS", assets)
    queued = action(studio, "report_create")

    async def invoke(self, prompt):
        return "Recorded incident OW-0001 needs investigation."

    monkeypatch.setattr(Agent, "invoke", invoke)
    await module.StudioWorker(config).run_once()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=dashboard.application(config)), base_url="http://localhost"
    ) as client:
        token = re.search(r'content="([^"]+)"', (await client.get("/")).text)[1]
        response = await client.post(
            "/api/studio/actions",
            json={
                "action": "feedback",
                "payload": {
                    "subject_type": "message",
                    "subject_id": queued["message"]["id"],
                    "rating": "helpful",
                },
            },
            headers={"Origin": "http://localhost", "X-OpsWatch-CSRF": token},
        )
        assert response.status_code == 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=dashboard.application(config)), base_url="http://localhost"
    ) as client:
        token = re.search(r'content="([^"]+)"', (await client.get("/")).text)[1]
        response = await client.post(
            "/api/studio/actions",
            json={"action": "session_read", "payload": {"session_id": queued["session"]["id"]}},
            headers={"Origin": "http://localhost", "X-OpsWatch-CSRF": token},
        )
        assert response.status_code == 200
        assert response.json()["messages"][-1]["feedback"] == "helpful"


async def test_scoped_jobs_only_receive_matching_operational_problems_and_subject_feedback(
    workspace, monkeypatch
):
    from opswatch.selection import projects

    module, config, store, studio = workspace
    config.agent.max_per_hour = 100
    config.targets[0].project_group = "Commerce"
    config.targets.append(
        Target(
            id="storefront",
            name="Storefront",
            kind="http",
            url="https://storefront.example",
            project_group="Commerce",
        )
    )

    async def invoke(self, prompt):
        document = json.loads(prompt.split("EVIDENCE DOCUMENT\n", 1)[1])
        identity = document["context"]["scope"]["id"] or "workspace"
        if '"problems":[' in prompt:
            return json.dumps(
                {
                    "summary": "Review the scoped evidence.",
                    "problems": [
                        {
                            "title": f"{identity} operational problem",
                            "summary": f"Recorded evidence for {identity}.",
                            "priority": "medium",
                            "evidence_refs": [f"service:{identity}"],
                            "validation": "Collect fresh evidence.",
                        }
                    ],
                }
            )
        return f"Recorded context for {identity}."

    monkeypatch.setattr(Agent, "invoke", invoke)
    for identity in ("shop", "storefront", "other"):
        scope = {"kind": "service", "id": identity}
        queued = action(studio, "report_create", {"scope": scope})
        await module.StudioWorker(config).run_once()
        for subject_type, subject_id in (
            ("message", queued["message"]["id"]),
            ("report", queued["report"]["id"]),
        ):
            action(
                studio,
                "feedback",
                {
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "rating": "helpful",
                    "comment": f"{identity} {subject_type} review",
                },
            )
        action(studio, "improvement_create", {"scope": scope})
        await module.StudioWorker(config).run_once()
        problem = next(
            problem for problem in module.studio_snapshot(config)["problems"] if problem["scope"] == scope
        )
        action(
            studio,
            "feedback",
            {
                "subject_type": "problem",
                "subject_id": problem["id"],
                "rating": "helpful",
                "comment": f"{identity} problem review",
            },
        )
    action(
        studio, "preferences_update", {"revision": 1, "notes": "Owner preference shared across applications."}
    )
    project_id = next(group["id"] for group in projects(config) if group["name"] == "Commerce")
    documents = []

    async def inspect(self, prompt):
        documents.append(json.loads(prompt.split("EVIDENCE DOCUMENT\n", 1)[1]))
        return "Only the selected scope supplied operational context."

    monkeypatch.setattr(Agent, "invoke", inspect)
    for scope in (
        {"kind": "service", "id": "shop"},
        {"kind": "project", "id": project_id},
        {"kind": "workspace", "id": None},
    ):
        session = action(studio, "session_create", {"scope": scope})["session"]
        action(studio, "message_send", {"session_id": session["id"], "content": "Review this scope"})
        await module.StudioWorker(config).run_once()
    assert [problem["scope"]["id"] for problem in documents[0]["existing_problems"]] == ["shop"]
    assert {feedback["comment"] for feedback in documents[0]["feedback"]} == {
        "shop message review",
        "shop report review",
        "shop problem review",
    }
    assert {problem["scope"]["id"] for problem in documents[1]["existing_problems"]} == {"shop", "storefront"}
    assert all("other" not in feedback["comment"] for feedback in documents[1]["feedback"])
    assert {problem["scope"]["id"] for problem in documents[2]["existing_problems"]} == {
        "shop",
        "storefront",
        "other",
    }
    assert all(
        document["preferences"]["notes"] == "Owner preference shared across applications."
        for document in documents
    )
    assert documents[0]["context"]["projects"][0]["service_ids"] == ["shop"]
    # Retired and malformed source scopes must be skipped without disrupting valid work.
    other_problem = next(
        problem for problem in module.studio_snapshot(config)["problems"] if problem["scope"]["id"] == "other"
    )
    other_problem["scope"] = {"kind": "project", "id": "missing-project"}
    with store.db:
        store.db.execute(
            "UPDATE studio_problems SET document=? WHERE id=?",
            (json.dumps(other_problem), other_problem["id"]),
        )
    config.targets = [target for target in config.targets if target.id != "other"]
    session = action(studio, "session_create", {"scope": {"kind": "project", "id": project_id}})["session"]
    action(
        studio, "message_send", {"session_id": session["id"], "content": "Review current Commerce services"}
    )
    await module.StudioWorker(config).run_once()
    assert all(problem["scope"]["id"] != "missing-project" for problem in documents[-1]["existing_problems"])
    assert all("other" not in feedback["comment"] for feedback in documents[-1]["feedback"])


async def test_improvement_source_report_must_match_the_requested_scope(workspace, monkeypatch):
    module, config, store, studio = workspace
    source = action(studio, "report_create", {"scope": {"kind": "service", "id": "other"}})

    async def invoke(self, prompt):
        return "Private Other-service report content."

    monkeypatch.setattr(Agent, "invoke", invoke)
    await module.StudioWorker(config).run_once()
    before = store.db.execute("SELECT count(*) FROM studio_jobs").fetchone()[0]
    with pytest.raises(ValueError, match="scope|Scope"):
        action(
            studio,
            "improvement_create",
            {"scope": {"kind": "service", "id": "shop"}, "report_id": source["report"]["id"]},
        )
    assert store.db.execute("SELECT count(*) FROM studio_jobs").fetchone()[0] == before


@pytest.mark.parametrize("change", ["removed", "invalid", "unreadable", "paused", "added", "data_dir"])
@pytest.mark.parametrize("scope_kind", ["service", "project"])
async def test_worker_resolves_scopes_against_the_current_saved_configuration(
    tmp_path, monkeypatch, change, scope_kind
):
    import yaml

    from opswatch.config import load_config

    module = studio_module()
    path = tmp_path / "config.yaml"
    raw = {
        "data_dir": "runtime",
        "targets": [{"id": "shop", "name": "Shop", "kind": "http", "url": "https://shop.example"}],
    }
    path.write_text(yaml.safe_dump(raw))
    original = load_config(path)
    worker = module.StudioWorker(original)
    store = Store(original.data_dir)
    try:
        if change == "added":
            raw["targets"].append(
                {"id": "added", "name": "Added", "kind": "http", "url": "https://added.example"}
            )
            path.write_text(yaml.safe_dump(raw))
            current = load_config(path)
        else:
            current = original
        studio = module.Studio(current, store)
        identity = "added" if change == "added" else "shop"
        if scope_kind == "project":
            identity = next(
                group["id"]
                for group in module.projects(current)
                if any(target.id == identity for target in group["targets"])
            )
        session = action(studio, "session_create", {"scope": {"kind": scope_kind, "id": identity}})["session"]
        action(studio, "message_send", {"session_id": session["id"], "content": "Investigate this service"})
        if change == "removed":
            raw["targets"] = []
            path.write_text(yaml.safe_dump(raw))
        elif change == "invalid":
            path.write_text("targets: [invalid")
        elif change == "unreadable":
            path.unlink()
        elif change == "paused":
            raw["targets"][0]["enabled"] = False
            path.write_text(yaml.safe_dump(raw))
        elif change == "data_dir":
            raw["data_dir"] = "different-runtime"
            path.write_text(yaml.safe_dump(raw))
        invoked = []

        async def invoke(self, prompt):
            invoked.append(prompt)
            return "The currently configured scope was reviewed."

        monkeypatch.setattr(Agent, "invoke", invoke)
        await worker.run_once()
        result = action(studio, "session_read", {"session_id": session["id"]})
        if change in {"removed", "invalid", "unreadable", "data_dir"}:
            assert result["jobs"][0]["status"] == "failed"
            assert result["jobs"][0]["error"]
            assert invoked == []
            assert store.db.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 0
        else:
            assert result["jobs"][0]["status"] == "succeeded"
            assert len(invoked) == 1
    finally:
        store.close()


async def test_whole_prompt_budget_handles_large_publicly_valid_workspace_history(workspace, monkeypatch):
    module, config, store, studio = workspace
    config.agent.max_per_hour = 100
    prompts = []

    async def invoke(self, prompt):
        prompts.append(prompt)
        if '"problems":[' in prompt:
            return json.dumps(
                {
                    "summary": "Review the recorded evidence.",
                    "problems": [
                        {
                            "title": "Recorded reliability work",
                            "summary": "S" * 3999,
                            "priority": "medium",
                            "evidence_refs": ["OW-0001"],
                            "validation": "V" * 2000,
                        }
                    ],
                }
            )
        return "The recorded evidence still supports a focused investigation."

    monkeypatch.setattr(Agent, "invoke", invoke)
    for _ in range(20):
        queued = action(studio, "improvement_create")
        await module.StudioWorker(config).run_once()
        read = action(studio, "session_read", {"session_id": queued["session"]["id"]})
        action(
            studio, "problem_update", {"problem_id": read["jobs"][0]["problem_ids"][0], "notes": "N" * 4000}
        )
    action(
        studio,
        "preferences_update",
        {
            "revision": 1,
            "notes": "P" * 4000,
            "principles": ["A" * 300] * 20,
            "stack": ["B" * 300] * 20,
            "review_focus": ["C" * 300] * 20,
            "verification": ["D" * 300] * 20,
        },
    )
    session = action(studio, "session_create")["session"]
    question = "界" * 8000
    queued = action(studio, "message_send", {"session_id": session["id"], "content": question})
    await module.StudioWorker(config).run_once()
    result = action(studio, "session_read", {"session_id": session["id"]})
    assert result["jobs"][0]["status"] == "succeeded"
    assert all(len(prompt.encode("utf-8")) <= 102400 for prompt in prompts)
    pinned = json.loads(
        store.db.execute("SELECT private FROM studio_jobs WHERE id=?", (queued["job"]["id"],)).fetchone()[0]
    )
    sent = json.loads(prompts[-1].split("EVIDENCE DOCUMENT\n", 1)[1])
    assert sent["question"] == pinned["question"] == question
    assert sent == {key: value for key, value in pinned.items() if key != "pstack"}
    coverage = sent["context"]["coverage"]["prompt"]
    assert coverage["bytes"] == len(prompts[-1].encode("utf-8"))
    assert coverage["truncated"] is True
    assert coverage["included"]["existing_problems"] < coverage["available"]["existing_problems"] == 20
    assert "9580c23696ac8d7abed0c166f2f9918800afb819" in prompts[-1]


async def test_unicode_preferences_and_source_report_do_not_crowd_out_core_scoped_evidence(
    workspace, monkeypatch
):
    module, config, store, studio = workspace
    for number in range(25):
        store.observe(
            Observation(
                "shop",
                f"detailed-{number}",
                "application error",
                False,
                f"Checkout exception {number}",
                {"details": "界" * 1900},
                immediate=True,
            ),
            config,
        )
    scope = {"kind": "service", "id": "shop"}
    prompts = []

    async def invoke(self, prompt):
        prompts.append(prompt)
        if '"problems":[' in prompt:
            return '{"summary":"Gather more evidence before proposing a change.","problems":[]}'
        return "R" * 11900

    monkeypatch.setattr(Agent, "invoke", invoke)
    report = action(studio, "report_create", {"scope": scope})
    await module.StudioWorker(config).run_once()
    action(
        studio, "preferences_update", {"revision": 1, "principles": ["界" * 300] * 20, "notes": "界" * 1900}
    )
    queued = action(
        studio,
        "improvement_create",
        {"scope": scope, "report_id": report["report"]["id"], "focus": "界" * 4000},
    )
    await module.StudioWorker(config).run_once()
    read = action(studio, "session_read", {"session_id": queued["session"]["id"]})
    assert read["jobs"][0]["status"] == "succeeded"
    document = json.loads(prompts[-1].split("EVIDENCE DOCUMENT\n", 1)[1])
    assert len(prompts[-1].encode("utf-8")) <= 102400
    assert document["context"]["services"][0]["id"] == "shop"
    assert document["context"]["checks"] or document["context"]["incidents"]
    assert document["preferences"]["revision"] == 2
    coverage = document["context"]["coverage"]["prompt"]
    assert coverage["included"]["preference_bytes"] < coverage["selected"]["preference_bytes"]
    assert coverage["truncated"] is True
