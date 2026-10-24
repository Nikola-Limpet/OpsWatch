import json
import time

import httpx
import pytest

from opswatch.config import Account, Config, Target
from opswatch.connectors import Collector, account_env, discover, epoch, parse_logs
from opswatch.process import parse_json, parse_json_lines, run
from opswatch.storage import Store


def test_account_tokens_are_isolated(monkeypatch):
    monkeypatch.setenv("RAILWAY_API_TOKEN", "unrelated-default")
    monkeypatch.setenv("RAILWAY_TOKEN", "unrelated-project")
    monkeypatch.setenv("SECOND_TOKEN", "second-account")
    env = account_env("railway", Account(name="second", token_env="SECOND_TOKEN"))
    assert env["RAILWAY_API_TOKEN"] == "second-account"
    assert "RAILWAY_TOKEN" not in env
    with pytest.raises(ValueError, match="missing"):
        account_env("railway", Account(name="third", token_env="ABSENT_TOKEN"))


def test_cli_json_formats():
    assert parse_json('Fetching projects\n{"projects": []}') == {"projects": []}
    assert parse_json_lines('{"message":"a"}\n{"message":"b"}') == [{"message": "a"}, {"message": "b"}]
    with pytest.raises(ValueError):
        parse_json_lines("Permission denied")


def test_journal_microsecond_timestamps():
    assert epoch("1791803484717189") == pytest.approx(1791803484.717189)
    assert epoch("2026-10-12T11:11:24Z") > 0


async def test_http_failure_and_latency(tmp_path):
    cfg, store = Config(data_dir=str(tmp_path)), Store(str(tmp_path))
    target = Target(id="web", name="Web", kind="http", url="https://example.com/health")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
        items, _ = await Collector(cfg, store, client).collect(target)
        assert items[0].healthy is False
        assert items[1].healthy is None
        assert items[0].evidence["http_status"] == 503
    store.close()


async def test_vercel_discovery_uses_verified_production_domains_across_pages(monkeypatch):
    import opswatch.connectors as module

    async def cli(argv, **kwargs):
        assert argv[-2:] == ["--scope", "example-team"]
        if argv[1:3] == ["project", "ls"]:
            return json.dumps(
                {"projects": [{"name": "sms", "latestProductionUrl": "https://sms.vercel.app"}]}
            )
        assert argv[1] == "api"
        if "until=123" in argv[2]:
            return json.dumps({"domains": [{"name": "sms.example.com", "verified": True}], "pagination": {}})
        return json.dumps(
            {
                "domains": [
                    {"name": "sms.vercel.app", "verified": True},
                    {"name": "staging.example.com", "verified": True, "gitBranch": "staging"},
                    {"name": "preview.example.com", "verified": True, "customEnvironmentId": "env_1"},
                    {"name": "unverified.example.com", "verified": False},
                    {"name": "*.example.com", "verified": True},
                    {"name": "www.example.com", "verified": True, "redirect": "sms.example.com"},
                ],
                "pagination": {"next": 123},
            }
        )

    monkeypatch.setattr(module, "run", cli)
    cfg = Config(vercel_accounts=[Account(name="example-team", scope="example-team")])
    targets, inventory = await discover(cfg, ["vercel"])
    assert targets[0].url == "https://sms.example.com"
    assert targets[0].enabled
    assert not inventory["errors"]


async def test_vercel_discovery_does_not_fallback_when_custom_domain_is_missing(monkeypatch):
    import opswatch.connectors as module

    async def cli(argv, **kwargs):
        if argv[1:3] == ["project", "ls"]:
            return json.dumps(
                {"projects": [{"name": "exam", "latestProductionUrl": "https://exam.vercel.app"}]}
            )
        return json.dumps({"domains": [{"name": "exam.vercel.app", "verified": True}]})

    monkeypatch.setattr(module, "run", cli)
    targets, _ = await discover(Config(), ["vercel"])
    assert targets[0].url is None
    assert not targets[0].enabled


def test_enabled_vercel_health_checks_reject_provider_aliases():
    with pytest.raises(ValueError, match="custom production domain"):
        Target(id="sms", name="SMS", kind="vercel", project="sms", url="https://sms.vercel.app")


async def test_missing_production_url_is_reported_without_probing_a_deployment(tmp_path, monkeypatch):
    import opswatch.connectors as module

    async def cli(argv, **kwargs):
        return json.dumps({"deployments": [{"state": "READY", "url": "exam-123.vercel.app"}]})

    monkeypatch.setattr(module, "run", cli)
    store = Store(str(tmp_path))
    target = Target(id="exam", name="Exam", kind="vercel", project="exam")
    async with httpx.AsyncClient() as client:
        items, _ = await Collector(Config(), store, client).collect(target)
    assert next(o for o in items if o.key == "deployment").healthy is True
    warning = next(o for o in items if o.key == "health-url")
    assert warning.healthy is False
    assert warning.kind == "monitoring"
    assert not any(o.key == "http" for o in items)
    store.close()


async def test_vercel_challenge_does_not_claim_an_application_outage(tmp_path):
    cfg, store = Config(data_dir=str(tmp_path)), Store(str(tmp_path))
    target = Target(id="sms", name="SMS", kind="http", url="https://sms.example.com")

    def response(request):
        return httpx.Response(429, headers={"x-vercel-mitigated": "challenge"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        items, _ = await Collector(cfg, store, client).collect(target)
    assert next(o for o in items if o.key == "http").healthy is None
    warning = next(o for o in items if o.key == "http-probe")
    assert warning.healthy is False
    assert warning.severity == "warning"
    assert warning.evidence["mitigation"] == "challenge"
    store.close()


def test_log_errors_deduplicate_and_do_not_trust_arbitrary_ip_headers(tmp_path):
    store = Store(str(tmp_path))
    target = Target(id="web", name="Web", kind="http", url="https://example.com")
    records = [
        {
            "timestamp": time.time(),
            "message": json.dumps(
                {"level": "error", "client_ip": "8.8.8.8", "path": "/.env", "status": 500, "trusted": True}
            ),
        }
    ]
    items, events = parse_logs(target, records, store)
    assert len(items) == 1
    assert events[0].trusted is False
    store.observe(items[0], Config())
    again, _ = parse_logs(target, records, store)
    assert again == []
    store.close()


def test_postgres_checkpoints_on_stderr_are_not_crash_alerts(tmp_path):
    store = Store(str(tmp_path))
    target = Target(id="db", name="DB", kind="http", url="https://example.com")
    record = {
        "timestamp": time.time(),
        "level": "error",
        "message": "2026-10-12 11:12:18.378 UTC [24] LOG:  checkpoint complete: wrote 592 buffers",
    }
    items, _ = parse_logs(target, [record], store)
    assert not items
    record["message"] = "2026-10-12 11:12:18.378 UTC [24] FATAL:  database system crashed"
    items, _ = parse_logs(target, [record], store)
    assert items[0].severity == "critical"
    store.close()


def test_vercel_request_log_schema_includes_nested_error_evidence(tmp_path):
    store = Store(str(tmp_path))
    target = Target(id="web", name="Web", kind="http", url="https://example.com")
    record = {
        "id": "req-1",
        "timestamp": time.time(),
        "requestPath": "/api/orders",
        "responseStatusCode": 500,
        "level": "error",
        "logs": [{"message": "Database connection failed", "level": "error"}],
    }
    items, events = parse_logs(target, [record], store)
    assert items[0].evidence["message"] == "Database connection failed"
    assert items[0].evidence["status"] == 500
    assert items[0].evidence["path"] == "/api/orders"
    assert events[0].client_ip == ""
    assert not events[0].trusted
    store.close()


async def test_cli_timeout_terminates_process():
    import sys

    with pytest.raises(TimeoutError):
        await run([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.05)


async def test_postgres_uses_current_interval_and_handles_stats_reset(tmp_path, monkeypatch):
    import opswatch.connectors as module

    store = Store(str(tmp_path))
    cfg = Config(data_dir=str(tmp_path))
    target = Target(id="db", name="DB", kind="postgres", dsn_env="TEST_DSN", slow_ms=200)
    monkeypatch.setenv("TEST_DSN", "postgresql://monitor:secret@example.com/db")
    sample = {"reset": "r1", "calls": 100, "time": 100000}

    class Cursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, sql):
            self.sql = sql

        async def fetchone(self):
            return {"exists": True} if "EXISTS" in self.sql else {"stats_reset": sample["reset"]}

        async def fetchall(self):
            return [
                {
                    "userid": "1",
                    "dbid": "1",
                    "queryid": "42",
                    "query": "SELECT * FROM orders WHERE id=$1",
                    "calls": sample["calls"],
                    "total_exec_time": sample["time"],
                }
            ]

    class Connection(Cursor):
        def cursor(self):
            return Cursor()

    async def connect(*args, **kwargs):
        assert "default_transaction_read_only=on" in kwargs["options"]
        return Connection()

    monkeypatch.setattr(module.psycopg.AsyncConnection, "connect", connect)
    async with httpx.AsyncClient() as client:
        collector = Collector(cfg, store, client)
        first, _ = await collector.postgres(target)
        assert not any(o.kind == "query performance" for o in first)  # baseline
        sample.update(calls=110, time=100100)
        second, _ = await collector.postgres(target)
        assert next(o for o in second if o.kind == "query performance").healthy is True
        sample.update(calls=120, time=105100)
        third, _ = await collector.postgres(target)
        slow = next(o for o in third if o.kind == "query performance")
        assert slow.healthy is False
        assert slow.evidence["interval_mean_ms"] == 500
        sample.update(reset="r2", calls=4, time=10)
        reset, _ = await collector.postgres(target)
        assert not any(o.kind == "query performance" for o in reset)
    store.close()
