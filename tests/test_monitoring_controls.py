import asyncio
import hashlib
import hmac
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import yaml

from opswatch.agent import Agent
from opswatch.config import load_config
from opswatch.dashboard import application
from opswatch.ingest import application as ingest_application
from opswatch.repairs import Repairs
from opswatch.selection import set_selection
from opswatch.service import Service
from opswatch.storage import Store
from opswatch.telegram import Telegram
from opswatch.types import Observation


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "config.local.yaml"
    raw = {
        "data_dir": "data",
        "repair": {
            "enabled": True,
            "auto_fix": True,
            "repositories": [
                {"id": "shop", "github": "owner/shop", "checks": [["true"]]},
            ],
        },
        "policy": {"dry_run": True, "allow_cidrs": ["192.0.2.1/32"]},
        "targets": [
            {
                "id": "api",
                "name": "Shop · API",
                "kind": "railway",
                "project": "shop",
                "service": "api",
                "repair_repo": "shop",
            },
            {
                "id": "web",
                "name": "Shop · Web",
                "kind": "railway",
                "project": "shop",
                "service": "web",
                "repair_repo": "shop",
            },
            {
                "id": "cache",
                "name": "Shop · Cache",
                "kind": "railway",
                "project": "shop",
                "service": "cache",
                "enabled": False,
            },
            {"id": "other", "name": "Other", "kind": "http", "url": "https://other.example.com"},
        ],
    }
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    config = load_config(path)
    store = Store(config.data_dir)
    yield path, config, store
    store.close()


async def headers(client):
    page = await client.get("/")
    return {
        "Origin": "http://localhost",
        "X-OpsWatch-CSRF": re.search(r'name="opswatch-csrf" content="([^"]+)"', page.text)[1],
    }


async def set_enabled(client, route, enabled):
    return await client.post(route, json={"enabled": enabled}, headers=await headers(client))


async def test_single_service_setting_persists_and_disabled_service_stays_visible(workspace):
    path, config, store = workspace
    original = yaml.safe_load(path.read_text())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        response = await set_enabled(client, "/api/services/api/monitoring", False)
        assert response.status_code == 200
        data = (await client.get("/api/snapshot")).json()
        assert "api" not in {s["id"] for s in data["services"]}
        saved = next(s for p in data["projects"] for s in p["services"] if s["id"] == "api")
        assert saved["enabled"] is False and saved["status"] == "disabled"
        assert (await set_enabled(client, "/api/services/api/monitoring", True)).status_code == 200
    written = yaml.safe_load(path.read_text())
    assert written["policy"] == original["policy"] and written["repair"] == original["repair"]
    assert {t.id: t.enabled for t in load_config(path).targets} == {
        "api": True,
        "web": True,
        "cache": False,
        "other": True,
    }
    assert list((path.parent / "data" / "backups").glob("config-before-selection-*.yaml"))


async def test_project_control_changes_only_its_services_and_can_restore_them(workspace):
    path, config, store = workspace
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        data = (await client.get("/api/snapshot")).json()
        project = next(p for p in data["projects"] if any(s["id"] == "api" for s in p["services"]))
        assert {s["id"] for s in project["services"]} == {"api", "web", "cache"}
        route = f"/api/projects/{project['id']}/monitoring"
        assert (await set_enabled(client, route, False)).status_code == 200
        assert {t.id: t.enabled for t in load_config(path).targets} == {
            "api": False,
            "web": False,
            "cache": False,
            "other": True,
        }
        assert (await set_enabled(client, route, True)).status_code == 200
        assert all(t.enabled for t in load_config(path).targets)


@pytest.mark.parametrize(
    "body", [{"enabled": "false"}, {"enabled": 0}, {}, [], {"enabled": False, "extra": True}]
)
async def test_invalid_selection_never_changes_configuration(workspace, body):
    path, config, store = workspace
    before = path.read_bytes()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        response = await client.post("/api/services/api/monitoring", json=body, headers=await headers(client))
        assert response.status_code == 400
    assert path.read_bytes() == before


async def test_selection_requires_same_origin_token_and_known_identity(workspace):
    path, config, store = workspace
    before = path.read_bytes()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        assert (await client.post("/api/services/api/monitoring", json={"enabled": False})).status_code == 403
        auth = await headers(client)
        auth["Origin"] = "https://untrusted.example.com"
        assert (
            await client.post("/api/services/api/monitoring", json={"enabled": False}, headers=auth)
        ).status_code == 403
        assert (await set_enabled(client, "/api/services/missing/monitoring", False)).status_code == 404
        assert (await set_enabled(client, "/api/projects/missing/monitoring", False)).status_code == 404
    assert path.read_bytes() == before


async def test_existing_monitor_reloads_selection_before_collecting(workspace, monkeypatch):
    path, config, store = workspace
    old = load_config(path)
    collected = []
    async with httpx.AsyncClient() as backend:
        service = Service(old, store, backend)

        async def collect(target):
            collected.append(target.id)
            return [], []

        monkeypatch.setattr(service.collector, "collect", collect)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
        ) as client:
            assert (await set_enabled(client, "/api/services/api/monitoring", False)).status_code == 200
        await service.cycle(all_targets=True)
    assert set(collected) == {"web", "other"}


@pytest.mark.parametrize("collector_fails", [False, True])
async def test_disabling_during_collection_discards_late_observations(
    workspace, monkeypatch, collector_fails
):
    path, config, store = workspace
    started, release = asyncio.Event(), asyncio.Event()
    async with httpx.AsyncClient() as backend:
        service = Service(load_config(path), store, backend)

        async def collect(target):
            if target.id != "api":
                return [], []
            started.set()
            await release.wait()
            if collector_fails:
                raise RuntimeError("Collector failed after monitoring was disabled")
            return [Observation("api", "http", "availability", False, "Failed", immediate=True)], []

        monkeypatch.setattr(service.collector, "collect", collect)
        task = asyncio.create_task(service.cycle(all_targets=True))
        await started.wait()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
        ) as client:
            assert (await set_enabled(client, "/api/services/api/monitoring", False)).status_code == 200
        release.set()
        await task
    assert store.incidents() == [] and store.pending() == []


async def test_queued_alert_for_disabled_service_is_silent_and_retains_evidence(workspace, monkeypatch):
    path, config, store = workspace
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_USER_ID"):
        monkeypatch.setenv(key, "1")
    store.observe(Observation("api", "http", "availability", False, "Outage", immediate=True), config)
    old = load_config(path)
    sent = []
    async with httpx.AsyncClient() as backend:
        bot = Telegram(old, store, backend, Agent(old, store))

        async def send(*args):
            sent.append(args)

        monkeypatch.setattr(bot, "send", send)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
        ) as client:
            assert (await set_enabled(client, "/api/services/api/monitoring", False)).status_code == 200
        await bot.deliver()
    assert not sent and not store.pending() and len(store.incidents()) == 1


async def test_queued_automatic_job_honors_selection_saved_by_another_process(workspace):
    import time

    path, config, store = workspace
    old = load_config(path)
    event = Observation(
        "api",
        "error",
        "application error",
        False,
        "TypeError",
        {"message": "TypeError: missing customer.id at src/order.ts:12"},
        immediate=True,
    )
    store.observe(event, config, time.time() - 180)
    store.observe(event, config)
    jobs = Repairs(old, store)
    job = jobs.enqueue_automatic()[0]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        assert (await set_enabled(client, "/api/services/api/monitoring", False)).status_code == 200
    assert jobs.claim() is None
    assert jobs.get(job["id"])["status"] == "cancelled"


def test_concurrent_service_changes_preserve_other_settings_and_remain_private(workspace):
    path, config, store = workspace
    before = yaml.safe_load(path.read_text())
    first, second = load_config(path), load_config(path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [
            pool.submit(set_selection, first, "services", "api", False),
            pool.submit(set_selection, second, "services", "web", False),
        ]
        for task in tasks:
            task.result()
    saved = load_config(path)
    assert {t.id: t.enabled for t in saved.targets} == {
        "api": False,
        "web": False,
        "cache": False,
        "other": True,
    }
    raw = yaml.safe_load(path.read_text())
    assert raw["policy"] == before["policy"] and raw["repair"] == before["repair"]
    assert path.stat().st_mode & 0o777 == 0o600
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in (path.parent / "data" / "backups").glob("*.yaml"))


async def test_display_group_can_include_different_providers_without_changing_code_ownership(workspace):
    path, config, store = workspace
    raw = yaml.safe_load(path.read_text())
    for target in raw["targets"]:
        target["project_group"] = "Shop"
    path.write_text(yaml.safe_dump(raw))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
    ) as client:
        data = (await client.get("/api/snapshot")).json()
    assert len(data["projects"]) == 1
    assert {s["id"] for s in data["projects"][0]["services"]} == {"api", "web", "cache", "other"}
    assert data["projects"][0]["enabled_count"] == 3
    assert load_config(path).targets[-1].repair_repo is None


async def test_ingest_disabled_during_security_inspection_stops_late_events(workspace, monkeypatch):
    path, config, store = workspace
    raw = yaml.safe_load(path.read_text())
    raw["targets"][0]["ingest_secret_env"] = "TEST_INGEST_SECRET"
    path.write_text(yaml.safe_dump(raw))
    monkeypatch.setenv("TEST_INGEST_SECRET", "test-signing-secret")
    started, release = asyncio.Event(), asyncio.Event()
    inspected = []
    async with httpx.AsyncClient() as backend:
        service = Service(load_config(path), store, backend)

        async def inspect(target, event):
            inspected.append(event.path)
            started.set()
            await release.wait()
            return Observation("api", "security", "security", False, "Probe", immediate=True)

        monkeypatch.setattr(service.security, "inspect", inspect)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=ingest_application(service)), base_url="http://ingest"
        ) as source:
            stamp = str(int(time.time()))
            body = json.dumps(
                [
                    {
                        "timestamp": int(stamp),
                        "path": f"/probe-{n}",
                        "status": 500,
                        "message": "TypeError: missing customer.id",
                    }
                    for n in range(2)
                ]
            ).encode()
            signature = hmac.new(
                b"test-signing-secret", stamp.encode() + b"." + body, hashlib.sha256
            ).hexdigest()
            task = asyncio.create_task(
                source.post(
                    "/events/api",
                    content=body,
                    headers={
                        "x-opswatch-timestamp": stamp,
                        "x-opswatch-signature": signature,
                    },
                )
            )
            await started.wait()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost"
            ) as client:
                assert (await set_enabled(client, "/api/services/api/monitoring", False)).status_code == 200
            release.set()
            assert (await task).status_code == 404
    assert inspected == ["/probe-0"]
    assert store.incidents() == [] and store.pending() == []


async def test_diagnosis_waiting_for_lock_does_not_start_after_disable(workspace, monkeypatch):
    path, config, store = workspace
    item = store.observe(
        Observation("api", "error", "application error", False, "TypeError", immediate=True), config
    )
    calls = []
    async with httpx.AsyncClient() as backend:
        service = Service(load_config(path), store, backend)

        async def diagnose(*args):
            calls.append(args)
            return "Suggestion"

        monkeypatch.setattr(service.agent, "diagnose", diagnose)
        await service.auto_lock.acquire()
        task = asyncio.create_task(service.auto_diagnose(item))
        await asyncio.sleep(0)
        set_selection(config, "services", "api", False)
        service.auto_lock.release()
        await task
    assert calls == []


async def test_newly_enabled_service_does_not_keep_an_old_schedule(workspace, monkeypatch):
    path, config, store = workspace
    async with httpx.AsyncClient() as backend:
        service = Service(load_config(path), store, backend)
        service.next_check["cache"] = time.monotonic() + 86400
        collected = []

        async def collect(target):
            collected.append(target.id)
            return [], []

        monkeypatch.setattr(service.collector, "collect", collect)
        set_selection(config, "services", "cache", True)
        await service.cycle()
    assert "cache" in collected
