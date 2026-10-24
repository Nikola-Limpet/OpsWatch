import sqlite3

from opswatch.config import Config
from opswatch.storage import Store
from opswatch.types import Observation


def item(healthy=False):
    return Observation("api", "http", "availability", healthy, "API unhealthy", {"status": 503}, "critical")


def test_failures_and_recovery_survive_restart(tmp_path):
    cfg = Config(data_dir=str(tmp_path), failures_before_alert=3, recoveries_before_alert=2)
    store = Store(str(tmp_path))
    assert store.observe(item(), cfg, now=100) is None
    assert store.observe(item(), cfg, now=101) is None
    store.close()
    store = Store(str(tmp_path))
    opened = store.observe(item(), cfg, now=102)
    assert opened["event"] == "opened"
    assert store.observe(item(), cfg, now=103) is None
    assert len(store.pending()) == 1
    assert store.observe(item(None), cfg, now=104) is None
    assert store.incidents()[0]["status"] == "open"
    assert store.observe(item(True), cfg, now=105) is None
    recovered = store.observe(item(True), cfg, now=106)
    assert recovered["event"] == "resolved"
    assert store.incidents() == []
    assert store.db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 2
    outage = store.pending()[0]
    store.delivered(outage["id"])
    assert len(store.pending()) == 1
    assert '"event": "resolved"' in store.pending()[0]["payload"]
    store.close()


def test_redaction_applies_before_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_API_TOKEN", "a-token-that-must-never-leak")
    cfg = Config(data_dir=str(tmp_path))
    store = Store(str(tmp_path))
    observation = Observation(
        "api",
        "error",
        "application error",
        False,
        "token=a-token-that-must-never-leak",
        {"authorization": "Bearer secret", "message": "password=hunter2"},
        immediate=True,
        action="use a-token-that-must-never-leak",
    )
    alert = store.observe(observation, cfg)
    assert "a-token-that-must-never-leak" not in str(alert)
    assert "hunter2" not in str(alert)
    assert "Bearer secret" not in str(alert)
    dump = " ".join(store.db.iterdump())
    assert "a-token-that-must-never-leak" not in dump
    store.close()


def test_reminder_is_durable_and_limited(tmp_path):
    cfg = Config(data_dir=str(tmp_path), failures_before_alert=1, reminder_seconds=60)
    store = Store(str(tmp_path))
    opened = store.observe(item(), cfg, now=100)
    assert store.observe(item(), cfg, now=159) is None
    reminder = store.observe(item(), cfg, now=160)
    assert reminder["ref"] == opened["ref"]
    assert reminder["event"] == "reminder"
    pending = store.pending()
    store.retry(pending[0]["id"], "token=do-not-leak")
    error = store.db.execute("SELECT last_error FROM outbox WHERE id=?", (pending[0]["id"],)).fetchone()[0]
    assert "do-not-leak" not in error
    store.close()


def test_only_one_open_incident_per_condition(tmp_path):
    store = Store(str(tmp_path))
    cfg = Config(failures_before_alert=1)
    store.observe(item(), cfg)
    other = sqlite3.connect(tmp_path / "opswatch.sqlite3")
    assert other.execute("SELECT count(*) FROM incidents WHERE status='open'").fetchone()[0] == 1
    other.close()
    store.close()


def test_quiet_event_preserves_the_error_that_needs_investigating(tmp_path):
    cfg = Config(data_dir=str(tmp_path), recoveries_before_alert=1)
    store = Store(str(tmp_path))
    opened = store.observe(
        Observation(
            "api",
            "exception",
            "application error",
            False,
            "Unhandled error",
            {"message": "TypeError at order.ts:12", "password": "private-value"},
            immediate=True,
        ),
        cfg,
        now=100,
    )
    store.observe(
        Observation(
            "api",
            "exception",
            "application error",
            True,
            "No matching events",
            {"quiet_seconds": 900},
        ),
        cfg,
        now=1000,
    )
    resolved = store.incident(opened["ref"])
    assert resolved["status"] == "resolved"
    assert resolved["evidence"]["message"] == "TypeError at order.ts:12"
    assert resolved["evidence"]["recovery"] == {"quiet_seconds": 900}
    assert "private-value" not in str(resolved)
    store.close()
