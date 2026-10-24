import importlib
import json
import sqlite3
import time

import httpx
import pytest

from opswatch.config import Config, Target
from opswatch.storage import Store
from opswatch.types import Observation


@pytest.fixture
def recorded(tmp_path):
    config = Config(
        data_dir=str(tmp_path),
        failures_before_alert=1,
        targets=[
            Target(id=name, name=name.title(), kind="http", url=f"https://{name}.example.com")
            for name in ["healthy", "slow", "unknown", "stale", "pending", "disabled"]
        ],
    )
    config.targets[-1].enabled = False
    store = Store(config.data_dir)
    now = time.time()
    store.set("heartbeat", now)
    for name, healthy, checked in [
        ("healthy", True, now),
        ("slow", False, now),
        ("unknown", None, now),
        ("stale", True, now - 1000),
        ("disabled", False, now),
    ]:
        store.observe(
            Observation(
                name,
                "http",
                "availability",
                healthy,
                "Health check",
                {"latency_ms": 250, "password": "do-not-expose"},
            ),
            config,
            checked,
        )
    yield config, store
    store.close()


def application(config):
    return importlib.import_module("opswatch.dashboard").application(config)


async def test_snapshot_distinguishes_unknown_stale_and_disabled_without_writing(recorded):
    config, store = recorded
    before = list(store.db.iterdump())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        response = await client.get("/api/snapshot")
    assert response.status_code == 200
    data = response.json()
    assert data["summary"] == {"total": 5, "healthy": 1, "attention": 1, "unknown": 3, "open_incidents": 1}
    assert {s["id"]: s["status"] for s in data["services"]} == {
        "healthy": "healthy",
        "slow": "warning",
        "unknown": "unknown",
        "stale": "stale",
        "pending": "unknown",
    }
    assert "do-not-expose" not in response.text
    assert all(item["target"] != "disabled" for item in data["incidents"])
    assert list(store.db.iterdump()) == before
    assert response.headers["cache-control"] == "no-store"


async def test_live_failure_stays_visible_when_another_check_is_old(recorded):
    config, store = recorded
    store.observe(
        Observation("healthy", "collector", "monitoring", False, "Collector down", severity="critical"),
        config,
    )
    store.observe(
        Observation("healthy", "old-event", "application error", True, "Quiet"), config, time.time() - 10000
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        data = (await client.get("/api/snapshot")).json()
    service = next(s for s in data["services"] if s["id"] == "healthy")
    assert service["status"] == "critical"


async def test_stale_monitor_never_claims_current_health(recorded):
    config, store = recorded
    store.set("heartbeat", time.time() - 10000)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        data = (await client.get("/api/snapshot")).json()
    assert data["monitor"]["status"] == "stale"
    assert data["summary"]["healthy"] == 0
    assert all(s["status"] != "healthy" for s in data["services"])


async def test_incident_reads_are_redacted_and_unsupported_routes_are_rejected(recorded, monkeypatch):
    config, store = recorded
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "a-private-token-value")
    item = store.observe(
        Observation(
            "healthy",
            "error",
            "application error",
            False,
            "Error",
            {
                "message": 'a-private-token-value {"password":"quoted-private-value",'
                '"api_key":"quoted-api-value"}',
                "api_key": "fixture-private-value",
                "api-key": "fixture-private-value",
                "apikey": "fixture-private-value",
            },
            immediate=True,
        ),
        config,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        response = await client.get(f"/api/incidents/{item['ref']}")
        assert response.status_code == 200
        assert "a-private-token-value" not in response.text
        assert "fixture-private-value" not in response.text
        assert "quoted-private-value" not in response.text
        assert "quoted-api-value" not in response.text
        snapshot = (await client.get("/api/snapshot")).text
        assert all(
            value not in snapshot
            for value in [
                "fixture-private-value",
                "quoted-private-value",
                "quoted-api-value",
            ]
        )
        assert response.json()["ref"] == item["ref"]
        assert (await client.get("/api/incidents/OW-9999")).status_code == 404
        assert (await client.get("/api/incidents/OW-²")).status_code == 404
        assert (await client.post(f"/api/incidents/{item['ref']}")).status_code == 405
        assert (await client.post("/api/fix")).status_code == 404
        assert (await client.get("/.env")).status_code == 404
        assert (await client.get("/assets/../../.env")).status_code == 404


async def test_dashboard_handles_an_uninitialized_monitor_without_creating_database(tmp_path):
    config = Config(data_dir=str(tmp_path / "missing"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        data = (await client.get("/api/snapshot")).json()
        page = await client.get("/")
        assert page.status_code == 200
        assert "OpsWatch" in page.text
    assert data["monitor"]["status"] == "not_started"
    assert data["services"] == []
    assert not (tmp_path / "missing").exists()


async def test_snapshot_preserves_more_than_twenty_targets_and_counts_all_incidents(recorded):
    config, store = recorded
    for number in range(30):
        name = f"service-{number}"
        config.targets.append(Target(id=name, name=name, kind="http", url="https://example.com"))
        store.observe(Observation(name, "http", "availability", False, "Unhealthy", immediate=True), config)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        data = (await client.get("/api/snapshot")).json()
    assert len(data["services"]) == 35
    assert data["summary"]["open_incidents"] == 31
    assert len(data["incidents"]) == 31


async def test_snapshot_omits_config_secrets_and_rejects_untrusted_host(recorded):
    config, store = recorded
    config.targets[0].url = "https://user:private@healthy.example.com/path?key=private"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        data = (await client.get("/api/snapshot")).json()
        assert "private" not in json.dumps(data)
        assert (await client.get("/", headers={"Host": "untrusted.example.com"})).status_code == 400
    other = sqlite3.connect(f"file:{config.data_dir}/opswatch.sqlite3?mode=ro", uri=True)
    assert other.execute("SELECT count(*) FROM incidents").fetchone()[0] == 2
    other.close()


async def test_open_incidents_survive_a_full_page_of_newer_resolved_history(recorded):
    config, store = recorded
    with store.db:
        store.db.executemany(
            "INSERT INTO incidents (target,key,kind,severity,title,evidence,status,opened,updated) "
            "VALUES ('healthy',?,'availability','warning','Recovered','{}','resolved',?,?)",
            [(f"past-{n}", time.time() + n, time.time() + n) for n in range(205)],
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        data = (await client.get("/api/snapshot")).json()
    assert len([i for i in data["incidents"] if i["status"] == "open"]) == 1
    assert len([i for i in data["incidents"] if i["status"] == "resolved"]) == 200
    assert data["summary"]["open_incidents"] == 1


async def test_snapshot_reports_worker_freshness_without_starting_it(recorded):
    config, store = recorded
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        assert (await client.get("/api/snapshot")).json()["repair_worker"]["status"] == "not_started"
        store.set("repair_heartbeat", time.time())
        assert (await client.get("/api/snapshot")).json()["repair_worker"]["status"] == "running"
        store.set("repair_heartbeat", time.time() - 300)
        assert (await client.get("/api/snapshot")).json()["repair_worker"]["status"] == "stale"
