import time

import pytest

from opswatch.config import Config
from opswatch.repairs import Repairs, RepairWorker
from opswatch.storage import Store
from opswatch.types import Observation


@pytest.fixture
def repairs(tmp_path):
    cfg = Config.model_validate(
        {
            "data_dir": str(tmp_path),
            "failures_before_alert": 1,
            "repair": {
                "enabled": True,
                "auto_fix": True,
                "repositories": [
                    {"id": "web", "github": "owner/web", "checks": [["python3", "-c", "print(1)"]]},
                ],
            },
            "targets": [
                {
                    "id": "web",
                    "name": "Web",
                    "kind": "http",
                    "url": "https://example.com",
                    "repair_repo": "web",
                }
            ],
        }
    )
    store = Store(cfg.data_dir)
    yield cfg, store, Repairs(cfg, store)
    store.close()


def repeated_error(
    cfg,
    store,
    key="log",
    kind="application error",
    message="TypeError: Cannot read properties of undefined at src/orders.ts:42",
):
    now = time.time()
    item = Observation("web", key, kind, False, "Unhandled exception", {"message": message}, immediate=True)
    store.observe(item, cfg, now - 180)
    store.observe(item, cfg, now)
    return store.incidents()[0]


def test_repeated_actionable_error_queues_one_automatic_job(repairs):
    cfg, store, jobs = repairs
    item = repeated_error(cfg, store)
    queued = jobs.enqueue_automatic()
    assert len(queued) == 1
    assert queued[0]["incident"] == item["ref"]
    assert queued[0]["requested_by"] == "automatic"
    assert queued[0]["status"] == "queued"
    assert jobs.enqueue_automatic() == []


def test_blocked_automatic_attempt_is_not_repeated_after_restart(repairs):
    cfg, store, jobs = repairs
    repeated_error(cfg, store)
    job = jobs.enqueue_automatic()[0]
    jobs.update(job["id"], "blocked", summary="Required tests failed")
    other = Store(cfg.data_dir)
    try:
        assert Repairs(cfg, other).enqueue_automatic(time.time() + 86400) == []
        assert len(jobs.list()) == 1
    finally:
        other.close()


@pytest.mark.parametrize(
    "kind,message",
    [
        ("monitoring", "Log collection reached 1000 records"),
        ("performance", "Health endpoint response exceeds the threshold"),
        ("security", "Repeated vulnerability probes were detected"),
        ("application error", "error"),
        ("application error", "Database connection refused while opening the production connection"),
        ("application error", "AuthenticationError: invalid API key for the payment provider"),
        ("application error", "HTTPError: HTTP 403 returned by the WAF security challenge"),
        ("application error", "Error: the application encountered an unexpected problem"),
        ("availability", "HTTP 503 with no application stack trace"),
    ],
)
def test_non_actionable_signals_never_start_coding(repairs, kind, message):
    cfg, store, jobs = repairs
    repeated_error(cfg, store, kind=kind, message=message)
    assert jobs.enqueue_automatic() == []
    assert jobs.list() == []


def test_single_recent_stale_disabled_or_unmapped_errors_do_not_queue(repairs):
    cfg, store, jobs = repairs
    now = time.time()
    observation = Observation(
        "web",
        "log",
        "application error",
        False,
        "Unhandled exception",
        {"message": "TypeError: missing customer.id at src/order.ts:12"},
        immediate=True,
    )
    store.observe(observation, cfg, now)
    assert jobs.enqueue_automatic() == []
    store.observe(observation, cfg, now + 1)
    assert jobs.enqueue_automatic(now + 2) == []
    assert jobs.enqueue_automatic(now + 1000) == []
    repeated_error(cfg, store, "eligible-error")
    cfg.targets[0].repair_repo = None
    assert jobs.enqueue_automatic() == []
    cfg.targets[0].repair_repo = "web"
    cfg.targets[0].enabled = False
    assert jobs.enqueue_automatic() == []
    cfg.targets[0].enabled = True
    assert len(jobs.enqueue_automatic()) == 1


def test_repository_cooldown_and_daily_budget_apply_to_automatic_jobs(repairs):
    cfg, store, jobs = repairs
    repeated_error(cfg, store)
    first = jobs.enqueue_automatic()[0]
    jobs.update(first["id"], "blocked")
    repeated_error(cfg, store, "different-error")
    assert jobs.enqueue_automatic() == []
    cfg.repair.auto_fix_cooldown_seconds = 0
    cfg.repair.max_per_day = 1
    assert jobs.enqueue_automatic() == []


def test_automatic_repair_policy_can_be_disabled(repairs):
    cfg, store, jobs = repairs
    repeated_error(cfg, store)
    cfg.repair.auto_fix = False
    assert jobs.enqueue_automatic() == []


@pytest.mark.parametrize("change", ["resolved", "stale", "disabled", "unmapped", "policy-off"])
def test_queued_automatic_job_revalidates_before_execution(repairs, change):
    cfg, store, jobs = repairs
    item = repeated_error(cfg, store)
    job = jobs.enqueue_automatic()[0]
    if change == "resolved":
        with store.db:
            store.db.execute("UPDATE incidents SET status='resolved' WHERE id=?", (int(item["ref"][3:]),))
    elif change == "stale":
        with store.db:
            store.db.execute(
                "UPDATE incidents SET updated=? WHERE id=?", (time.time() - 1000, int(item["ref"][3:]))
            )
    elif change == "disabled":
        cfg.targets[0].enabled = False
    elif change == "unmapped":
        cfg.targets[0].repair_repo = None
    else:
        cfg.repair.auto_fix = False
    assert jobs.claim() is None
    cancelled = jobs.get(job["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["summary"]


def test_manual_jobs_retain_explicit_execution_semantics(repairs):
    cfg, store, jobs = repairs
    repeated_error(cfg, store)
    job = jobs.enqueue(store.incidents()[0]["ref"], "owner")
    cfg.repair.auto_fix = False
    assert jobs.claim()["id"] == job["id"]


async def test_recovered_incident_does_not_reach_worker_build(repairs, monkeypatch):
    cfg, store, jobs = repairs
    item = repeated_error(cfg, store)
    jobs.enqueue_automatic()
    job = jobs.claim()
    with store.db:
        store.db.execute("UPDATE incidents SET status='resolved' WHERE id=?", (int(item["ref"][3:]),))
    worker = RepairWorker(cfg, store)

    async def build(_):
        pytest.fail("Recovered incident must not start a repair build")

    monkeypatch.setattr(worker, "build", build)
    await worker.execute(job)
    assert jobs.get(job["id"])["status"] == "cancelled"


async def test_automatic_job_reports_terminal_outcome_without_phase_spam(repairs):
    cfg, store, jobs = repairs
    cfg.telegram.notifications = "important"
    repeated_error(cfg, store)
    job = jobs.enqueue_automatic()[0]
    sent = []

    class Bot:
        async def send(self, message, markup):
            sent.append(message)

    worker = RepairWorker(cfg, store)
    await worker.notify_pending(Bot())
    jobs.update(job["id"], "coding")
    await worker.notify_pending(Bot())
    assert sent == []
    jobs.update(job["id"], "pr_open", pr_url="https://github.com/owner/web/pull/1")
    await worker.notify_pending(Bot())
    await worker.notify_pending(Bot())
    assert len(sent) == 1 and "https://github.com/owner/web/pull/1" in sent[0]
