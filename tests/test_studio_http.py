import re
from uuid import uuid4

import httpx
import pytest

from opswatch.config import Config, Target
from opswatch.dashboard import application


@pytest.fixture
async def studio_client(tmp_path, monkeypatch):
    from opswatch import dashboard

    assets = tmp_path / "assets"
    (assets / "web").mkdir(parents=True)
    (assets / "web" / "index.html").write_text('<meta name="opswatch-csrf" content="__OPSWATCH_CSRF__">')
    monkeypatch.setattr(dashboard, "ASSETS", assets)
    config = Config(
        data_dir=str(tmp_path / "data"),
        targets=[Target(id="api", name="API", kind="http", url="https://example.invalid")],
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        token = re.search(r'content="([^"]+)"', (await client.get("/")).text)[1]
        yield client, config, {"Origin": "http://localhost", "X-OpsWatch-CSRF": token}


async def test_studio_read_is_read_only_before_the_first_owner_action(studio_client):
    from pathlib import Path

    client, config, _ = studio_client
    data = (await client.get("/api/studio")).json()
    assert data["sessions"] == [] and data["counts"]["reports"] == 0
    assert data["worker"]["status"] == "not_started"
    assert not Path(config.data_dir).exists()


async def test_studio_http_requires_csrf_and_persists_idempotent_session_history(studio_client):
    client, _, headers = studio_client
    document = {"action": "session_create", "payload": {"title": "Investigate API"}}
    assert (await client.post("/api/studio/actions", json=document)).status_code == 403
    headers = {**headers, "X-OpsWatch-Request-ID": str(uuid4())}
    first = await client.post("/api/studio/actions", json=document, headers=headers)
    second = await client.post("/api/studio/actions", json=document, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    session_id = first.json()["session"]["id"]
    headers["X-OpsWatch-Request-ID"] = str(uuid4())
    response = await client.post(
        "/api/studio/actions",
        json={
            "action": "message_send",
            "payload": {"session_id": session_id, "content": "What needs attention?"},
        },
        headers=headers,
    )
    assert response.status_code == 202
    assert response.json()["job"]["status"] == "queued"
    headers["X-OpsWatch-Request-ID"] = str(uuid4())
    read = await client.post(
        "/api/studio/actions",
        json={"action": "session_read", "payload": {"session_id": session_id}},
        headers=headers,
    )
    assert read.status_code == 200
    assert read.json()["messages"][0]["content"] == "What needs attention?"
    assert read.json()["jobs"][0]["status"] == "queued"
    overview = (await client.get("/api/studio")).json()
    assert overview["counts"]["sessions"] == 1


async def test_studio_http_rejects_invalid_actions_and_preference_conflicts(studio_client):
    client, _, headers = studio_client
    bad = await client.post(
        "/api/studio/actions", json={"action": "shell", "payload": {"command": "env"}}, headers=headers
    )
    assert bad.status_code == 400
    invalid_identity = await client.post(
        "/api/studio/actions",
        json={"action": "session_create", "payload": {}},
        headers={**headers, "X-OpsWatch-Request-ID": "invalid"},
    )
    assert invalid_identity.status_code == 400
    document = {
        "action": "preferences_update",
        "payload": {"revision": 1, "notes": "Verify before reporting."},
    }
    assert (await client.post("/api/studio/actions", json=document, headers=headers)).status_code == 200
    response = await client.post("/api/studio/actions", json=document, headers=headers)
    assert response.status_code == 409
    assert "error" in response.json()
