import json
import time
from uuid import uuid4

import httpx
import pytest

from opswatch.agent import Agent
from opswatch.config import Config, Target
from opswatch.dashboard import application, snapshot
from opswatch.storage import Store
from opswatch.studio import Studio, StudioWorker
from opswatch.studio_protocol import validate_action
from opswatch.types import Observation


@pytest.fixture
def workspace(tmp_path):
    config = Config(
        data_dir=str(tmp_path), targets=[Target(id="web", name="Web", kind="http", url="https://example.com")]
    )
    store = Store(config.data_dir)
    store.set("heartbeat", time.time())
    item = store.observe(
        Observation(
            "web",
            "probe",
            "security",
            False,
            "Repeated probes",
            {"requests": 12, "message": "untrusted evidence"},
            immediate=True,
        ),
        config,
    )
    yield config, store, item, Studio(config, store)
    store.close()


def investigate(studio, ref, identity=None):
    return studio.dispatch("incident_investigate", {"incident_ref": ref}, identity or str(uuid4()))


def test_incident_investigation_is_atomic_durable_and_deduplicated(workspace):
    config, store, item, studio = workspace
    identity = str(uuid4())
    result = investigate(studio, item["ref"], identity)
    assert result["job"]["status"] == "queued"
    assert result["job"]["incident_ref"] == item["ref"]
    assert result["session"]["scope"] == {"kind": "service", "id": "web"}
    assert investigate(studio, item["ref"], identity) == result
    assert investigate(studio, item["ref"])["job"]["id"] == result["job"]["id"]
    assert store.db.execute("SELECT count(*) FROM studio_sessions").fetchone()[0] == 1
    saved = snapshot(config)["investigations"]
    assert saved[0]["session_id"] == result["session"]["id"]
    assert saved[0]["incident"] == item["ref"]
    assert "evidence" not in saved[0]


async def test_selected_older_incident_is_pinned_and_result_stays_in_dashboard(workspace, monkeypatch):
    config, store, item, studio = workspace
    with store.db:
        store.db.execute("UPDATE incidents SET updated=1 WHERE id=?", (item["id"],))
        store.db.executemany(
            "INSERT INTO incidents(target,key,kind,severity,title,evidence,status,opened,updated) "
            "VALUES('web',?,'security','warning','Newer','{}','open',?,?)",
            [(f"other-{n}", time.time(), time.time()) for n in range(35)],
        )
    result = investigate(studio, item["ref"])
    prompts = []

    async def invoke(self, prompt):
        prompts.append(prompt)
        return (
            "Finding: repeated probes. Suggested fix: review the configured edge policy. No changes applied."
        )

    monkeypatch.setattr(Agent, "invoke", invoke)
    assert await StudioWorker(config).run_once()
    assert item["ref"] in prompts[0] and "untrusted evidence" in prompts[0]
    assert "UNTRUSTED DATA" in prompts[0]
    completed = snapshot(config)["investigations"][0]
    assert completed["status"] == "succeeded"
    assert "Suggested fix" in completed["summary"]
    assert store.incident(item["ref"])["status"] == "open"
    read = studio.dispatch("session_read", {"session_id": result["session"]["id"]}, str(uuid4()))
    assert "No changes applied" in read["messages"][-1]["content"]


@pytest.mark.parametrize("ref", ["OW-x", "OW-1/../../", "OW-0001;sh", "", None])
def test_incident_action_rejects_invalid_identity(ref):
    with pytest.raises(ValueError, match="incident|Incident"):
        validate_action("incident_investigate", {"incident_ref": ref})


@pytest.mark.parametrize("state", ["disabled", "missing", "no-evidence"])
def test_incident_action_rejects_unavailable_evidence_or_target(workspace, state):
    config, store, item, studio = workspace
    if state == "disabled":
        config.targets[0].enabled = False
    elif state == "missing":
        item["ref"] = "OW-9999"
    else:
        with store.db:
            store.db.execute("UPDATE incidents SET evidence='{}' WHERE id=?", (item["id"],))
    with pytest.raises(ValueError):
        investigate(studio, item["ref"])
    assert store.db.execute("SELECT count(*) FROM studio_jobs").fetchone()[0] == 0


async def test_dashboard_can_start_incident_agent_without_telegram(workspace):
    config, store, item, _ = workspace
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        token = (await client.get("/api/csrf")).json()["token"]
        body = {"action": "incident_investigate", "payload": {"incident_ref": item["ref"]}}
        assert (await client.post("/api/studio/actions", json=body)).status_code == 403
        response = await client.post(
            "/api/studio/actions", json=body, headers={"Origin": "http://localhost", "X-OpsWatch-CSRF": token}
        )
        assert response.status_code == 202
        assert response.json()["job"]["status"] == "queued"
    assert not config.telegram.enabled
    assert store.db.execute("SELECT count(*) FROM studio_jobs").fetchone()[0] == 1


def test_incident_aliases_share_a_single_visible_job(workspace):
    config, store, item, studio = workspace
    original = investigate(studio, item["ref"])
    alias = investigate(studio, "OW-00001")
    assert alias["job"]["id"] == original["job"]["id"]
    assert alias["job"]["incident_ref"] == item["ref"]
    assert store.db.execute("SELECT count(*) FROM studio_jobs").fetchone()[0] == 1
    assert len(snapshot(config)["investigations"]) == 1


def test_large_selected_evidence_remains_in_prompt_with_explicit_omission(workspace):
    _, store, item, studio = workspace
    evidence = {
        key: [f"{key} recorded error {n}: " + "e" * 1980 for n in range(20)]
        for key in ["paths", "events", "traces"]
    }
    with store.db:
        store.db.execute("UPDATE incidents SET evidence=? WHERE id=?", (json.dumps(evidence), item["id"]))
    queued = investigate(studio, item["ref"])
    private = json.loads(
        store.db.execute("SELECT private FROM studio_jobs WHERE id=?", (queued["job"]["id"],)).fetchone()[0]
    )
    assert private["context"]["incidents"][0]["ref"] == item["ref"]
    assert "recorded error" in json.dumps(private["context"]["incidents"][0]["evidence"])
    assert private["context"]["coverage"]["selected_incident"]["evidence_truncated"] is True


def test_followup_keeps_old_incident_evidence(workspace):
    _, store, item, studio = workspace
    with store.db:
        store.db.execute("UPDATE incidents SET updated=1 WHERE id=?", (item["id"],))
        store.db.executemany(
            "INSERT INTO incidents(target,key,kind,severity,title,evidence,status,opened,updated) "
            "VALUES('web',?,'security','warning','Newer','{}','open',?,?)",
            [(f"new-{n}", time.time(), time.time()) for n in range(35)],
        )
    initial = investigate(studio, item["ref"])
    with store.db:
        studio._finish(initial["job"], "succeeded", content="Review the policy.")
    followup = studio.dispatch(
        "message_send",
        {"session_id": initial["session"]["id"], "content": "Explain the failure evidence in more detail."},
        str(uuid4()),
    )
    private = json.loads(
        store.db.execute("SELECT private FROM studio_jobs WHERE id=?", (followup["job"]["id"],)).fetchone()[0]
    )
    assert private["context"]["incidents"][0]["ref"] == item["ref"]
    assert "untrusted evidence" in json.dumps(private["context"]["incidents"])


def test_historical_missing_next_step_has_dashboard_guidance():
    from opswatch.formatting import next_step_text

    assert next_step_text(None) == "Review the recorded evidence."
