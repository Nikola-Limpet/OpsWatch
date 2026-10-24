import json
import time

import httpx
import pytest

from opswatch.config import Config, Target
from opswatch.connectors import Collector, parse_logs
from opswatch.service import Service
from opswatch.storage import Store
from opswatch.types import Observation


@pytest.mark.parametrize("status,severity", [("CRASHED", "critical"), ("FAILED", "warning")])
async def test_confirmed_railway_crash_alerts_on_first_collection(tmp_path, monkeypatch, status, severity):
    import opswatch.connectors as module

    async def cli(argv, **kwargs):
        return json.dumps([{"id": "deployment-1", "status": status}])

    monkeypatch.setattr(module, "run", cli)
    store = Store(str(tmp_path))
    target = Target(id="api", name="API", kind="railway", project="project", service="service")
    cfg = Config(targets=[target], failures_before_alert=3)
    async with httpx.AsyncClient() as client:
        await Service(cfg, store, client).check_target(target)
    assert store.incidents()[0]["severity"] == severity
    assert len(store.pending()) == 1
    store.close()


async def test_health_endpoint_5xx_alerts_on_first_observation(tmp_path):
    store = Store(str(tmp_path))
    target = Target(id="web", name="Web", kind="http", url="https://web.example.com")
    cfg = Config(targets=[target], failures_before_alert=3)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(503))
    ) as client:
        await Service(cfg, store, client).check_target(target)
    assert store.incidents()[0]["severity"] == "critical"
    store.close()


async def test_database_connection_failure_alerts_on_first_collection(tmp_path, monkeypatch):
    import opswatch.connectors as module

    async def connect(*args, **kwargs):
        raise module.psycopg.OperationalError("Connection refused")

    monkeypatch.setenv("TEST_DSN", "postgresql://monitor@example.com/db")
    monkeypatch.setattr(module.psycopg.AsyncConnection, "connect", connect)
    store = Store(str(tmp_path))
    target = Target(id="db", name="DB", kind="postgres", dsn_env="TEST_DSN")
    async with httpx.AsyncClient() as client:
        await Service(Config(targets=[target]), store, client).check_target(target)
    assert store.incidents()[0]["title"] == "Database connection failed"
    store.close()


def test_log_is_deduplicated_only_after_its_alert_is_durable(tmp_path):
    store = Store(str(tmp_path))
    target = Target(id="api", name="API", kind="http", url="https://api.example.com")
    record = {"timestamp": time.time(), "level": "error", "message": "Database connection failed"}
    first, _ = parse_logs(target, [record], store)
    retried, _ = parse_logs(target, [record], store)
    assert len(first) == len(retried) == 1
    store.observe(retried[0], Config())
    assert len(store.pending()) == 1
    assert parse_logs(target, [record], store)[0] == []
    store.close()


def test_error_without_a_timestamp_still_alerts(tmp_path):
    store = Store(str(tmp_path))
    target = Target(id="api", name="API", kind="http", url="https://api.example.com")
    items, _ = parse_logs(target, [{"level": "error", "message": "Unhandled exception"}], store)
    assert len(items) == 1
    assert items[0].evidence["timestamp_estimated"] is True
    store.close()


async def test_vercel_log_cap_reports_a_monitoring_gap(tmp_path, monkeypatch):
    import opswatch.connectors as module

    async def cli(argv, **kwargs):
        if argv[1] == "list":
            return json.dumps({"deployments": [{"state": "READY"}]})
        limit = int(argv[argv.index("--limit") + 1])
        return json.dumps([{"id": str(i), "timestamp": time.time(), "level": "info"} for i in range(limit)])

    monkeypatch.setattr(module, "run", cli)
    target = Target(id="web", name="Web", kind="vercel", project="web", logs=True)
    store = Store(str(tmp_path))
    async with httpx.AsyncClient() as client:
        items, _ = await Collector(Config(), store, client).vercel(target)
    gap = next(o for o in items if o.key == "runtime-logs")
    assert gap.healthy is False
    assert gap.immediate
    assert not gap.checkpoint
    store.close()


def test_message_less_5xx_errors_are_grouped_by_endpoint(tmp_path):
    store = Store(str(tmp_path))
    target = Target(id="api", name="API", kind="http", url="https://api.example.com")
    records = [
        {"timestamp": time.time(), "responseStatusCode": 500, "requestPath": path}
        for path in ["/api/invoices", "/api/students"]
    ]
    items, _ = parse_logs(target, records, store)
    assert len({item.key for item in items}) == 2
    store.close()


async def test_sentry_includes_recent_errors_even_when_already_resolved(tmp_path, monkeypatch):
    import opswatch.connectors as module

    async def cli(argv, **kwargs):
        query = argv[argv.index("--query") + 1]
        assert "is:unresolved" not in query
        return json.dumps(
            {
                "data": [
                    {
                        "id": "123",
                        "title": "Render failed",
                        "status": "resolved",
                        "level": "error",
                        "lastSeen": time.time(),
                        "count": "1",
                    }
                ]
            }
        )

    monkeypatch.setattr(module, "run", cli)
    store = Store(str(tmp_path))
    target = Target(id="sentry", name="SMS", kind="sentry", organization="org", project="sms")
    cfg = Config(targets=[target])
    async with httpx.AsyncClient() as client:
        await Service(cfg, store, client).check_target(target)
        assert len(store.pending()) == 1
    store.close()


async def test_failed_log_collection_does_not_mark_errors_quiet(tmp_path, monkeypatch):
    store = Store(str(tmp_path))
    target = Target(id="web", name="Web", kind="vercel", project="web")
    cfg = Config(targets=[target], recoveries_before_alert=1)
    store.observe(
        Observation("web", "log:1", "application error", False, "Database failed", immediate=True),
        cfg,
        now=time.time() - 1000,
    )

    async def collect(t):
        return [
            Observation(t.id, "runtime-logs", "monitoring", False, "Log collection failed"),
            Observation(t.id, "collector", "monitoring", True, "Collector connected"),
        ], []

    async with httpx.AsyncClient() as client:
        service = Service(cfg, store, client)
        monkeypatch.setattr(service.collector, "collect", collect)
        await service.check_target(target)
    assert next(i for i in store.incidents() if i["key"] == "log:1")["status"] == "open"
    store.close()


async def test_alert_delivery_does_not_wait_for_a_slow_unrelated_collector(tmp_path, monkeypatch):
    import asyncio

    store = Store(str(tmp_path))
    fast = Target(id="fast", name="Fast", kind="http", url="https://fast.example.com")
    slow = Target(id="slow", name="Slow", kind="http", url="https://slow.example.com")
    cfg = Config(targets=[fast, slow], failures_before_alert=1)
    delivered = asyncio.Event()

    async def collect(t):
        if t.id == "slow":
            await asyncio.wait_for(delivered.wait(), 0.5)
            return [Observation(t.id, "collector", "monitoring", True, "Connected")], []
        return [Observation(t.id, "http", "availability", False, "Down", immediate=True)], []

    class Bot:
        async def deliver(self):
            if store.pending():
                delivered.set()

    async with httpx.AsyncClient() as client:
        service = Service(cfg, store, client)
        service.telegram = Bot()
        monkeypatch.setattr(service.collector, "collect", collect)
        await service.cycle(all_targets=True)
    assert next(r for r in store.checks() if r["target"] == "slow")["healthy"] == 1
    store.close()


async def test_unexpected_processing_failure_creates_a_monitor_alert(tmp_path, monkeypatch):
    store = Store(str(tmp_path))
    target = Target(id="api", name="API", kind="http", url="https://api.example.com")

    async def collect(t):
        raise RuntimeError("Malformed event")

    async with httpx.AsyncClient() as client:
        service = Service(Config(targets=[target]), store, client)
        monkeypatch.setattr(service.collector, "collect", collect)
        await service.cycle(all_targets=True)
    assert store.incidents()[0]["kind"] == "monitoring"
    assert len(store.pending()) == 1
    store.close()


def test_retried_outage_stays_before_its_recovery_but_other_incidents_can_deliver(tmp_path):
    store = Store(str(tmp_path))
    cfg = Config(failures_before_alert=1, recoveries_before_alert=1)
    store.observe(Observation("a", "http", "availability", False, "A down"), cfg)
    first = store.pending()[0]
    store.retry(first["id"], "Network unavailable")
    store.observe(Observation("a", "http", "availability", True, "A recovered"), cfg)
    store.observe(Observation("b", "http", "availability", False, "B down"), cfg)
    pending = [json.loads(r["payload"]) for r in store.pending()]
    assert [p["target"] for p in pending] == ["b"]
    store.close()
