import base64
import copy
import gzip
import json
from uuid import uuid4

import httpx
import pytest
from test_cloud_store import SNAPSHOT, MemoryRelay

from opswatch.cloud_relay import application

AUTH = {"Authorization": "Bearer bridge-secret"}
CSRF = {"Origin": "https://dashboard.example", "X-OpsWatch-CSRF": "stable-secret"}


@pytest.fixture
async def client():
    store = MemoryRelay()
    store.sync({"snapshot": copy.deepcopy(SNAPSHOT)})
    app = application(store, "bridge-secret", "stable-secret")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://dashboard.example",
    ) as http:
        yield http, store


async def test_bridge_bearer_auth_is_separate_from_browser_csrf(client):
    http, store = client
    for headers in ({}, CSRF, {"Authorization": "Bearer wrong"}):
        assert (await http.post("/api/bridge/sync", json={}, headers=headers)).status_code == 401
    reply = await http.post("/api/bridge/sync", json={}, headers=AUTH)
    assert reply.status_code == 200 and reply.json() == {"acknowledged": [], "command": None}
    page = await http.get("/")
    assert 'content="stable-secret"' in page.text
    assert "bridge-secret" not in page.text


async def test_retained_repair_evidence_is_reachable_outside_recent_history(client):
    http, store = client
    data = copy.deepcopy(SNAPSHOT)
    data["repair_incidents"] = [
        {
            "ref": "OW-0999",
            "target": "api",
            "status": "resolved",
            "repair": {"available": True},
            "evidence": {"message": "Original error"},
        }
    ]
    store.sync({"snapshot": data})
    response = await http.get("/api/incidents/OW-0999")
    assert response.status_code == 200
    assert response.json()["evidence"]["message"] == "Original error"
    assert (await http.post("/api/incidents/OW-0999/fix", json={}, headers=CSRF)).status_code == 202


async def test_commands_require_exact_origin_and_csrf_and_keep_idempotency(client):
    http, store = client
    route = "/api/services/api/monitoring"
    for headers in (
        {},
        AUTH,
        {**CSRF, "Origin": "https://evil.example"},
        {**CSRF, "Origin": "https://dashboard.example.evil"},
        {**CSRF, "X-OpsWatch-CSRF": "wrong"},
    ):
        assert (await http.post(route, json={"enabled": False}, headers=headers)).status_code == 403
    key = str(uuid4())
    headers = {**CSRF, "X-OpsWatch-Request-ID": key}
    for _ in range(2):
        response = await http.post(route, json={"enabled": False}, headers=headers)
        assert response.status_code == 202 and response.json() == {"command_id": key}
    state = await http.get(f"/api/commands/{key}")
    assert state.json() == {"id": key, "state": "pending", "result": None, "error": None}
    assert (await http.get("/api/commands/not-a-uuid")).status_code == 404
    assert (await http.get(f"/api/commands/{uuid4()}")).status_code == 404
    assert len(store.data.commands) == 1


async def test_browser_fix_and_project_routes_enqueue_only_published_targets(client):
    http, store = client
    for route, body in [
        ("/api/incidents/OW-0001/fix", {}),
        ("/api/projects/shop/monitoring", {"enabled": True}),
        ("/api/services/cache/monitoring", {"enabled": True}),
    ]:
        response = await http.post(route, json=body, headers=CSRF)
        assert response.status_code == 202
    for route in [
        "/api/incidents/OW-9999/fix",
        "/api/incidents/OW-²/fix",
        "/api/projects/missing/monitoring",
    ]:
        assert (await http.post(route, json={"enabled": True}, headers=CSRF)).status_code in {400, 404}
    assert (await http.get("/api/incidents/OW-0001")).json()["target"] == "api"
    assert (await http.get("/api/incidents/OW-9999")).status_code == 404


async def test_disconnected_bridge_makes_all_enabled_health_stale_and_rejects_actions(client):
    http, store = client
    store.now += 61
    reply = await http.get("/api/snapshot")
    data = reply.json()
    assert data["monitor"]["status"] == "stale"
    assert data["summary"]["healthy"] == 0 and data["summary"]["unknown"] == 1
    assert data["summary"]["attention"] == 0
    assert data["services"][0]["status"] == "stale"
    assert data["services"][0]["latency_ms"] is None
    assert data["projects"][0]["services"][0]["status"] == "stale"
    assert data["projects"][0]["services"][1]["status"] == "disabled"
    assert data["queries"][0]["stale"] is True
    assert data["queries"][0]["healthy"] is None
    response = await http.post("/api/incidents/OW-0001/fix", json={}, headers=CSRF)
    assert response.status_code == 503
    # Polling alone cannot refresh a snapshot that the bridge stopped publishing.
    await http.post("/api/bridge/sync", json={}, headers=AUTH)
    assert (await http.get("/api/snapshot")).json()["monitor"]["status"] == "stale"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"extra": True},
        {"snapshot": []},
        {"icons": {}},
        {"results": [{"id": "not-a-uuid", "result": {}, "error": None}]},
        {"results": [{"id": str(uuid4()), "result": {}, "error": "both"}]},
        {"icons": [{"target": "../.env", "content_type": "image/png", "data_base64": "YQ=="}]},
        {"icons": [{"target": "api", "content_type": "image/png", "data_base64": "bad"}]},
    ],
)
async def test_invalid_sync_payloads_never_replace_published_snapshot(client, payload):
    http, store = client
    before = copy.deepcopy(store.data)
    response = await http.post("/api/bridge/sync", json=payload, headers=AUTH)
    assert response.status_code == 400
    assert store.data == before


@pytest.mark.parametrize(
    "body", [{"enabled": 1}, {"enabled": "true"}, [], {}, {"enabled": True, "target": "other"}]
)
async def test_monitoring_payload_is_strict(client, body):
    http, store = client
    response = await http.post("/api/services/api/monitoring", json=body, headers=CSRF)
    assert response.status_code == 400 and store.data.commands == []


async def test_gzip_is_supported_and_decompression_and_plain_inputs_are_bounded(client):
    http, store = client
    compressed = gzip.compress(json.dumps({"snapshot": SNAPSHOT}).encode())
    assert (
        await http.post("/api/bridge/sync", content=compressed, headers={**AUTH, "Content-Encoding": "gzip"})
    ).status_code == 200
    for body, headers in [
        (gzip.compress(b"x" * (4 * 1024 * 1024 + 1)), {"Content-Encoding": "gzip"}),
        (b"x" * (4 * 1024 * 1024 + 1), {}),
        (b"invalid gzip", {"Content-Encoding": "gzip"}),
    ]:
        response = await http.post("/api/bridge/sync", content=body, headers={**AUTH, **headers})
        assert response.status_code in {400, 413}
    assert (
        await http.post("/api/bridge/sync", content=b"{}", headers={**AUTH, "Content-Encoding": "br"})
    ).status_code == 415


async def test_only_safe_cached_icons_and_known_assets_are_served(client):
    http, store = client
    data = b"\x89PNG\r\n\x1a\nicon"
    icon = {"target": "api", "content_type": "image/png", "data_base64": base64.b64encode(data).decode()}
    assert (await http.post("/api/bridge/sync", json={"icons": [icon]}, headers=AUTH)).status_code == 200
    response = await http.get("/favicons/api")
    assert response.content == data and response.headers["content-type"] == "image/png"
    assert "sandbox" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"].startswith("private,")
    malicious = {
        **icon,
        "content_type": "image/svg+xml",
        "data_base64": base64.b64encode(
            b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>'
        ).decode(),
    }
    assert (await http.post("/api/bridge/sync", json={"icons": [malicious]}, headers=AUTH)).status_code == 400
    assert (await http.get("/favicons/missing")).status_code == 404
    assert (await http.get("/assets/app.js")).status_code == 200
    for route in ["/.env", "/assets/.env", "/assets/../../.env", "/favicons/../../.env"]:
        assert (await http.get(route)).status_code == 404


async def test_store_failures_do_not_expose_database_details(client):
    http, store = client

    def broken():
        raise RuntimeError("postgres://owner:password@private.internal/database")

    store.read_snapshot = broken
    response = await http.get("/api/snapshot")
    assert response.status_code == 503
    assert "password" not in response.text and "private.internal" not in response.text


@pytest.mark.parametrize(
    "snapshot",
    [
        {**SNAPSHOT, "services": [{"id": "api", "checks": "invalid"}]},
        {**SNAPSHOT, "projects": [{"id": "shop", "services": [{"id": "../arbitrary"}]}]},
        {**SNAPSHOT, "incidents": [{"ref": "OW-0001", "repair": []}]},
        {**SNAPSHOT, "summary": {"total": "invalid"}},
        {**SNAPSHOT, "monitor": {"status": []}},
        {**SNAPSHOT, "bad\x00key": "value"},
        {**SNAPSHOT, "timezone": "\ud800"},
    ],
)
async def test_malformed_snapshot_fields_are_rejected_before_persistence(client, snapshot):
    http, store = client
    response = await http.post("/api/bridge/sync", content=json.dumps({"snapshot": snapshot}), headers=AUTH)
    assert response.status_code == 400
    assert store.data.snapshot == SNAPSHOT


async def test_fix_body_supports_empty_request_and_rejects_encoding_and_size(client):
    http, store = client
    assert (await http.post("/api/incidents/OW-0001/fix", headers=CSRF)).status_code == 202
    response = await http.post(
        "/api/incidents/OW-0001/fix", content=b"{}", headers={**CSRF, "Content-Encoding": "gzip"}
    )
    assert response.status_code == 415
    response = await http.post("/api/incidents/OW-0001/fix", content=b" " * 129, headers=CSRF)
    assert response.status_code == 413


async def test_empty_relay_returns_ui_compatible_stale_startup():
    app = application(MemoryRelay(), "bridge-secret", "stable-secret")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://example.test",
    ) as http:
        data = (await http.get("/api/snapshot")).json()
    assert data["monitor"]["status"] == "stale"
    assert data["summary"]["total"] == 0 and data["summary"]["healthy"] == 0
    assert all(data[key] == [] for key in ("services", "projects", "queries", "incidents", "repairs"))
