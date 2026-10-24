import asyncio
import gzip
import json
from uuid import uuid4

import httpx
import pytest
import yaml

from opswatch.config import load_config
from opswatch.repairs import Repairs
from opswatch.selection import projects, set_selection
from opswatch.storage import Store
from opswatch.types import Observation


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "data_dir": "data",
                "repair": {
                    "enabled": True,
                    "repositories": [
                        {"id": "shop", "github": "owner/shop", "checks": [["true"]]},
                    ],
                },
                "targets": [
                    {
                        "id": "web",
                        "name": "Shop",
                        "kind": "http",
                        "url": "https://example.com",
                        "repair_repo": "shop",
                    },
                    {
                        "id": "other",
                        "name": "Other",
                        "kind": "http",
                        "enabled": False,
                        "url": "https://other.example.com",
                    },
                ],
            }
        )
    )
    config = load_config(path)
    store = Store(config.data_dir)
    yield path, config, store
    store.close()


def command(kind="services", target="web", payload=None, **extra):
    return {
        "id": str(uuid4()),
        "kind": kind,
        "target": target,
        "payload": {"enabled": False} if payload is None else payload,
        "expires_at": 1060.0,
        **extra,
    }


def test_snapshot_compaction_preserves_open_incidents_and_active_repairs(monkeypatch):
    from opswatch import cloud_bridge

    snapshot = {
        "incidents": [{"ref": f"OW-{n:04d}", "status": "resolved", "title": "x" * 180} for n in range(30)]
        + [{"ref": "OW-9999", "status": "open", "title": "Old open error"}],
        "repairs": [{"id": f"RF-{n:04d}", "status": "cancelled", "summary": "x" * 180} for n in range(30)]
        + [{"id": "RF-9999", "status": "coding", "summary": "Active"}],
        "queries": [],
    }
    monkeypatch.setattr(cloud_bridge, "snapshot", lambda config: snapshot)
    monkeypatch.setattr(cloud_bridge, "REQUEST_LIMIT", 4000)
    result = cloud_bridge.relay_snapshot(None)
    assert result is not None
    assert any(i["ref"] == "OW-9999" for i in result["incidents"])
    assert any(j["id"] == "RF-9999" for j in result["repairs"])
    assert len(snapshot["incidents"]) == 31


def bridge(config, store, client):
    from opswatch.cloud_bridge import CloudBridge

    return CloudBridge(
        config,
        store,
        client=client,
        clock=lambda: 1000.0,
        url="http://localhost",
        token="bridge-secret-test",
        publish_icons=False,
    )


def body(request):
    assert request.url.path == "/api/bridge/sync"
    assert request.headers["authorization"] == "Bearer bridge-secret-test"
    assert request.headers["content-encoding"] == "gzip"
    return json.loads(gzip.decompress(request.content))


async def test_selection_executes_locally_and_acknowledges_immediately(workspace):
    path, config, store = workspace
    item = command()
    sent = []

    def relay(request):
        sent.append(body(request))
        if len(sent) == 1:
            return httpx.Response(200, json={"acknowledged": [], "command": item})
        return httpx.Response(200, json={"acknowledged": [item["id"]], "command": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(relay)) as client:
        assert await bridge(config, store, client).sync_once()
    assert load_config(path).targets[0].enabled is False
    assert sent[1]["results"] == [
        {"id": item["id"], "result": {"enabled": False, "targets": ["web"]}, "error": None}
    ]
    assert "snapshot" in sent[0]
    assert not sent[1]["snapshot"]["services"]


async def test_response_loss_restart_and_duplicate_delivery_never_reexecutes(workspace):
    path, config, store = workspace
    item = command()
    sent = []

    def disconnected(request):
        sent.append(body(request))
        if len(sent) == 1:
            return httpx.Response(200, json={"acknowledged": [], "command": item})
        raise httpx.ReadError("remote-body-secret", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(disconnected)) as client:
        assert not await bridge(config, store, client).sync_once()
    set_selection(config, "services", "web", True)
    retried = []

    def recovered(request):
        retried.append(body(request))
        return httpx.Response(
            200, json={"acknowledged": [item["id"]], "command": item if len(retried) == 1 else None}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(recovered)) as client:
        resumed = bridge(load_config(path), store, client)
        assert await resumed.sync_once()
        assert await resumed.sync_once()
    assert load_config(path).targets[0].enabled is True
    assert retried[0]["results"][0]["id"] == item["id"]
    assert not retried[-1].get("results")


@pytest.mark.parametrize(
    "changes",
    [
        {"expires_at": 999},
        {"expires_at": True},
        {"expires_at": float("inf")},
        {"expires_at": 10**400},
        {"expires_at": 1201},
        {"kind": "shell"},
        {"payload": {"enabled": "false"}},
        {"payload": {"enabled": False, "command": "echo bad"}},
        {"target": "missing"},
        {"extra": "not allowed"},
    ],
)
async def test_invalid_commands_return_bounded_error_without_mutating(workspace, changes):
    path, config, store = workspace
    before = path.read_bytes()
    item = command(**changes)
    sent = []

    def relay(request):
        sent.append(body(request))
        return httpx.Response(
            200,
            content=json.dumps(
                {
                    "acknowledged": [r["id"] for r in sent[-1].get("results", [])],
                    "command": item if len(sent) == 1 else None,
                }
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(relay)) as client:
        assert await bridge(config, store, client).sync_once()
    assert path.read_bytes() == before
    assert sent[1]["results"][0]["result"] is None
    assert 0 < len(sent[1]["results"][0]["error"]) <= 500


async def test_invalid_uuid_is_ignored(workspace):
    path, config, store = workspace
    before = path.read_bytes()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"acknowledged": [], "command": command(id="../../config.yaml")}
            )
        )
    ) as client:
        assert await bridge(config, store, client).sync_once()
    assert path.read_bytes() == before


async def test_fix_uses_current_mapping_and_returns_only_public_job(workspace):
    path, config, store = workspace
    incident = store.observe(
        Observation(
            "web",
            "error",
            "application error",
            False,
            "Broken",
            {"token": "private-test-token"},
            immediate=True,
        ),
        config,
    )
    item = command("fix", incident["ref"], {})
    sent = []

    def relay(request):
        sent.append(body(request))
        return httpx.Response(200, json={"acknowledged": [], "command": item if len(sent) == 1 else None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(relay)) as client:
        assert await bridge(config, store, client).sync_once()
    jobs = Repairs(config, store).list()
    assert len(jobs) == 1 and jobs[0]["requested_by"] == "cloud-dashboard"
    result = sent[1]["results"][0]["result"]
    assert result["status"] == "queued"
    assert "evidence" not in result and "repository" not in result
    assert "private-test-token" not in json.dumps(sent)


async def test_fix_reloads_configuration_and_rejects_removed_mapping(workspace):
    path, config, store = workspace
    incident = store.observe(
        Observation("web", "error", "application error", False, "Broken", immediate=True), config
    )
    raw = yaml.safe_load(path.read_text())
    raw["targets"][0].pop("repair_repo")
    path.write_text(yaml.safe_dump(raw))
    item = command("fix", incident["ref"], {})
    sent = []

    def relay(request):
        sent.append(body(request))
        return httpx.Response(200, json={"acknowledged": [], "command": item if len(sent) == 1 else None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(relay)) as client:
        assert await bridge(config, store, client).sync_once()
    assert Repairs(config, store).list() == []
    assert sent[1]["results"][0]["error"]


async def test_project_command_resolves_current_local_group(workspace):
    path, config, store = workspace
    identity = next(p["id"] for p in projects(config) if p["name"] == "Shop")
    item = command("projects", identity)
    count = 0

    def relay(request):
        nonlocal count
        body(request)
        count += 1
        return httpx.Response(200, json={"acknowledged": [], "command": item if count == 1 else None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(relay)) as client:
        assert await bridge(config, store, client).sync_once()
    assert load_config(path).targets[0].enabled is False


async def test_snapshot_interval_does_not_send_credentials_or_local_paths(workspace, monkeypatch):
    path, config, store = workspace
    monkeypatch.setenv("TEST_API_TOKEN", "private-credential-test")
    store.observe(
        Observation(
            "web", "error", "application error", False, "private-credential-test failed", immediate=True
        ),
        config,
    )
    sent = []

    def relay(request):
        sent.append(body(request))
        return httpx.Response(200, json={"acknowledged": [], "command": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(relay)) as client:
        worker = bridge(config, store, client)
        assert await worker.sync_once()
        assert await worker.sync_once()
        worker.clock = lambda: 1031
        assert await worker.sync_once()
    assert ["snapshot" in payload for payload in sent] == [True, False, True]
    serialized = json.dumps(sent)
    assert "private-credential-test" not in serialized
    assert str(path.parent) not in serialized


@pytest.mark.parametrize(
    "url",
    [
        "http://relay.example.com",
        "https://user:password@example.com",
        "https://relay.example.com/?token=secret",
        "file:///tmp/relay",
    ],
)
def test_relay_url_rejects_insecure_or_credential_urls(workspace, url):
    from opswatch.cloud_bridge import CloudBridge

    _, config, store = workspace
    with pytest.raises(ValueError):
        CloudBridge(config, store, url=url, token="secret", publish_icons=False)


async def test_cli_installs_separate_private_bridge_unit_without_secrets(workspace, tmp_path, monkeypatch):
    from opswatch.cli import execute, parser

    path, config, store = workspace
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("OPSWATCH_RELAY_BRIDGE_TOKEN", "never-in-unit")
    args = parser().parse_args(["--config", str(path), "install-cloud-service"])
    await execute(args, config, store)
    unit = tmp_path / ".config/systemd/user/opswatch-cloud-bridge.service"
    assert unit.stat().st_mode & 0o777 == 0o600
    text = unit.read_text()
    assert " cloud-bridge\n" in text and "Restart=on-failure" in text
    assert "never-in-unit" not in text and "EnvironmentFile=" not in text
    import shutil
    import subprocess

    if shutil.which("systemd-analyze"):
        checked = subprocess.run(
            ["systemd-analyze", "--user", "verify", str(unit)], capture_output=True, text=True
        )
        assert checked.returncode == 0, checked.stderr
    assert not (unit.parent / "opswatch.service").exists()
    with pytest.raises(ValueError):
        await execute(args, config, store)


async def test_icons_do_not_block_commands_and_publish_only_enabled_targets(workspace, monkeypatch):
    from opswatch.favicons import Favicons, Icon

    path, config, store = workspace
    config.targets[1].repair_repo = "shop"
    release = asyncio.Event()
    calls = []
    sent = []

    async def icon(self, target):
        calls.append(target)
        await release.wait()
        return Icon(b"\x89PNG\r\n\x1a\ncontent", "image/png")

    monkeypatch.setattr(Favicons, "get", icon)

    def relay(request):
        sent.append(body(request))
        return httpx.Response(200, json={"acknowledged": [], "command": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(relay)) as client:
        worker = bridge(config, store, client)
        worker.publish_icons = True
        assert "https://other.example.com" not in worker.favicons.origins["web"]
        assert await asyncio.wait_for(worker.sync_once(), timeout=1)
        await asyncio.sleep(0)
        release.set()
        await worker.icon_task
        assert await worker.sync_once()
        assert await worker.sync_once()
    assert calls == ["web"]
    assert sent[1]["icons"][0]["target"] == "web"
    assert "icons" not in sent[2]


async def test_unexpected_remote_response_is_silent_and_does_not_mutate(workspace, caplog):
    path, config, store = workspace
    before = path.read_bytes()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500, text="private-remote-body-secret"))
    ) as client:
        assert not await bridge(config, store, client).sync_once()
    assert path.read_bytes() == before
    assert "private-remote-body-secret" not in caplog.text
    assert "bridge-secret-test" not in caplog.text


async def test_bridge_uses_single_process_lock_and_stops_gracefully(workspace):
    _, config, store = workspace
    entered = asyncio.Event()

    async def relay(request):
        body(request)
        entered.set()
        return httpx.Response(200, json={"acknowledged": [], "command": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(relay)) as client:
        first, second = bridge(config, store, client), bridge(config, store, client)
        task = asyncio.create_task(first.serve())
        await entered.wait()
        try:
            with pytest.raises(ValueError, match="already running"):
                await second.serve()
        finally:
            first.stop.set()
            await asyncio.wait_for(task, timeout=1)
        second.stop.set()
        await asyncio.wait_for(second.serve(), timeout=1)


async def test_crash_after_local_mutation_never_replays_on_restart(workspace, monkeypatch):
    from opswatch import cloud_bridge

    path, config, store = workspace
    item = command()
    before = set_selection

    class ProcessCrash(BaseException):
        pass

    def interrupted(*args):
        before(*args)
        raise ProcessCrash

    monkeypatch.setattr(cloud_bridge, "set_selection", interrupted)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"acknowledged": [], "command": item})
        )
    ) as client:
        with pytest.raises(ProcessCrash):
            await bridge(config, store, client).sync_once()
    monkeypatch.setattr(cloud_bridge, "set_selection", before)
    before(config, "services", "web", True)
    sent = []

    def relay(request):
        sent.append(body(request))
        return httpx.Response(
            200, json={"acknowledged": [item["id"]], "command": item if len(sent) == 1 else None}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(relay)) as client:
        assert await bridge(load_config(path), store, client).sync_once()
    assert load_config(path).targets[0].enabled is True
    assert sent[0]["results"][0]["error"] and sent[0]["results"][0]["result"] is None


async def test_large_snapshot_and_icons_are_budgeted_without_stalling(workspace, monkeypatch):
    from opswatch import cloud_bridge

    _, config, store = workspace
    original = cloud_bridge.snapshot(config)
    original["incidents"] = [{"ref": f"OW-{n}", "evidence": {"detail": "x" * 30000}} for n in range(200)]
    monkeypatch.setattr(cloud_bridge, "snapshot", lambda _: original)
    sizes = []

    def relay(request):
        payload = body(request)
        sizes.append(len(gzip.decompress(request.content)))
        assert sizes[-1] <= 4 * 1024 * 1024
        assert payload["snapshot"]["projects"] == original["projects"] if "snapshot" in payload else True
        return httpx.Response(200, json={"acknowledged": [], "command": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(relay)) as client:
        worker = bridge(config, store, client)
        worker.icons = {"web": {"target": "web", "content_type": "image/png", "data_base64": "a" * 349528}}
        assert await worker.sync_once()
        assert await worker.sync_once()
    assert len(sizes) == 2
    assert worker.next_snapshot > 1000


async def test_multiple_large_icons_use_remaining_request_budget(workspace, monkeypatch):
    from opswatch import cloud_bridge

    _, config, store = workspace
    config.targets = [config.targets[0].model_copy(update={"id": f"web-{n}"}) for n in range(8)]
    config._source_path = None
    monkeypatch.setattr(cloud_bridge, "snapshot", lambda _: {"incidents": [{"detail": "x" * 1500000}]})
    counts = []

    def relay(request):
        payload = body(request)
        assert len(gzip.decompress(request.content)) <= 4 * 1024 * 1024
        counts.append(len(payload.get("icons", [])))
        return httpx.Response(200, json={"acknowledged": [], "command": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(relay)) as client:
        worker = bridge(config, store, client)
        worker.icons = {
            t.id: {"target": t.id, "content_type": "image/png", "data_base64": "a" * 349528}
            for t in config.targets
        }
        assert await worker.sync_once()
        assert await worker.sync_once()
    assert sum(counts) == 8
    assert counts[0] < 8
    assert not worker.icons
