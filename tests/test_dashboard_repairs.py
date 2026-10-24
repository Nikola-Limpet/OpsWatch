import re

import httpx
import pytest

from opswatch.config import Config
from opswatch.dashboard import application
from opswatch.repairs import Repairs
from opswatch.storage import Store
from opswatch.types import Observation


@pytest.fixture
def repair_dashboard(tmp_path):
    config = Config.model_validate(
        {
            "data_dir": str(tmp_path),
            "repair": {
                "enabled": True,
                "repositories": [{"id": "web", "github": "owner/web", "checks": [["true"]]}],
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
    store = Store(config.data_dir)
    item = store.observe(
        Observation(
            "web",
            "error",
            "application error",
            False,
            "Unhandled error",
            {"message": "TypeError: missing customer.id at src/order.ts:12"},
            immediate=True,
        ),
        config,
    )
    yield config, store, item
    store.close()


async def action_headers(client):
    page = await client.get("/")
    token = re.search(r'name="opswatch-csrf" content="([^"]+)"', page.text)
    assert token, "The dashboard must provide its local action token"
    return {"Origin": "http://localhost", "X-OpsWatch-CSRF": token[1]}


async def test_agent_work_includes_jobs_outside_incident_history(repair_dashboard):
    config, store, item = repair_dashboard
    jobs = Repairs(config, store)
    job = jobs.enqueue(item["ref"], "test")
    jobs.update(job["id"], "pr_open", pr_url="https://github.com/owner/web/pull/1")
    with store.db:
        store.db.execute("UPDATE incidents SET status='resolved',updated=1 WHERE id=?", (item["id"],))
        store.db.executemany(
            "INSERT INTO incidents (target,key,kind,severity,title,evidence,status,opened,updated) "
            "VALUES ('web',?,'availability','warning','Recovered','{}','resolved',?,?)",
            [(f"past-{n}", n + 100, n + 100) for n in range(205)],
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        data = (await client.get("/api/snapshot")).json()
        assert item["ref"] not in {i["ref"] for i in data["incidents"]}
        assert data["repairs"][0]["id"] == job["id"]
        assert data["repairs"][0]["service"] == "Web"
        assert data["repairs"][0]["title"] == "Unhandled error"
        assert data["repair_incidents"][0]["ref"] == item["ref"]
        config.targets[0].enabled = False
        assert (await client.get("/api/snapshot")).json()["repairs"] == []


async def test_start_fix_queues_once_and_exposes_only_public_job_fields(repair_dashboard):
    config, store, item = repair_dashboard
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        headers = await action_headers(client)
        response = await client.post(f"/api/incidents/{item['ref']}/fix", headers=headers)
        assert response.status_code == 202
        job = response.json()
        assert job["id"] == "RF-0001" and job["status"] == "queued"
        assert set(job) == {"id", "incident", "status", "summary", "pr_url", "updated"}
        again = await client.post(f"/api/incidents/{item['ref']}/fix", headers=headers)
        assert again.json()["id"] == job["id"]
        assert len(Repairs(config, store).list()) == 1
        assert Repairs(config, store).get(job["id"])["requested_by"] == "dashboard"
        data = (await client.get("/api/snapshot")).json()
        assert data["repairs"][0]["id"] == job["id"]
        assert data["incidents"][0]["repair"]["available"] is True


@pytest.mark.parametrize("violation", ["missing-token", "wrong-token", "cross-origin", "missing-origin"])
async def test_untrusted_action_cannot_create_a_repair(repair_dashboard, violation):
    config, store, item = repair_dashboard
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        headers = await action_headers(client)
        if violation == "missing-token":
            headers.pop("X-OpsWatch-CSRF")
        elif violation == "wrong-token":
            headers["X-OpsWatch-CSRF"] = "incorrect"
        elif violation == "cross-origin":
            headers["Origin"] = "https://untrusted.example.com"
        else:
            headers.pop("Origin")
        response = await client.post(f"/api/incidents/{item['ref']}/fix", headers=headers)
        assert response.status_code == 403
    assert not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='repair_jobs'").fetchone()


@pytest.mark.parametrize("reason", ["monitoring", "security", "unmapped", "disabled", "repairs-off"])
async def test_unavailable_fix_has_an_explanation_and_cannot_queue(repair_dashboard, reason):
    config, store, item = repair_dashboard
    if reason in {"monitoring", "security"}:
        with store.db:
            store.db.execute("UPDATE incidents SET kind=? WHERE id=?", (reason, item["id"]))
    elif reason == "unmapped":
        config.targets[0].repair_repo = None
    elif reason == "disabled":
        config.targets[0].enabled = False
    else:
        config.repair.enabled = False
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        headers = await action_headers(client)
        response = await client.post(f"/api/incidents/{item['ref']}/fix", headers=headers)
        assert response.status_code == (404 if reason == "disabled" else 409)
        assert response.json()["error"]
        data = (await client.get("/api/snapshot")).json()
        if reason != "disabled":
            capability = data["incidents"][0]["repair"]
            assert capability["available"] is False and capability["reason"]
    assert not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='repair_jobs'").fetchone()


async def test_daily_budget_error_leaves_existing_jobs_unchanged(repair_dashboard):
    config, store, item = repair_dashboard
    jobs = Repairs(config, store)
    first = jobs.enqueue(item["ref"], "owner")
    jobs.update(first["id"], "blocked")
    config.repair.max_per_day = 1
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        response = await client.post(
            f"/api/incidents/{item['ref']}/fix", headers=await action_headers(client)
        )
        assert response.status_code == 409
        assert "limit" in response.json()["error"].lower()
    assert len(jobs.list()) == 1


async def test_older_draft_pr_remains_visible_after_other_incidents_receive_repairs(repair_dashboard):
    config, store, item = repair_dashboard
    config.repair.max_per_day = 100
    jobs = Repairs(config, store)
    first = jobs.enqueue(item["ref"], "owner")
    jobs.update(first["id"], "pr_open", pr_url="https://github.com/owner/web/pull/1")
    for number in range(21):
        other = store.observe(
            Observation(
                "web", f"error-{number}", "application error", False, "Other error", {}, immediate=True
            ),
            config,
        )
        job = jobs.enqueue(other["ref"], "owner")
        jobs.update(job["id"], "blocked")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        data = (await client.get("/api/snapshot")).json()
    saved = next((job for job in data["repairs"] if job["incident"] == item["ref"]), None)
    assert saved and saved["status"] == "pr_open"
    assert saved["pr_url"] == "https://github.com/owner/web/pull/1"
