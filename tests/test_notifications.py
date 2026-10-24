import time

import httpx
import pytest

from opswatch.agent import Agent
from opswatch.config import Config, Target
from opswatch.storage import Store
from opswatch.telegram import Telegram
from opswatch.types import Observation


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    for key, value in {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_CHAT_ID": "1",
        "TELEGRAM_USER_ID": "1",
    }.items():
        monkeypatch.setenv(key, value)
    cfg = Config.model_validate(
        {
            "data_dir": str(tmp_path),
            "failures_before_alert": 1,
            "recoveries_before_alert": 1,
            "telegram": {"enabled": True, "notifications": "important"},
            "targets": [Target(id="web", name="Web", kind="http", url="https://example.com").model_dump()],
        }
    )
    store = Store(cfg.data_dir)
    yield cfg, store
    store.close()


def observation(key="http", healthy=False, severity="warning", kind="performance", evidence=None):
    return Observation("web", key, kind, healthy, f"Problem {key}", evidence or {}, severity, True)


async def deliver(cfg, store, monkeypatch):
    sent = []
    async with httpx.AsyncClient() as client:
        bot = Telegram(cfg, store, client, Agent(cfg, store))

        async def send(message, markup=None):
            sent.append(message)

        monkeypatch.setattr(bot, "send", send)
        await bot.deliver()
    return sent


async def test_brief_warning_does_not_block_immediate_critical(monitor, monkeypatch):
    cfg, store = monitor
    store.observe(observation(), cfg)
    store.observe(observation("fatal", severity="critical", kind="availability"), cfg)
    sent = await deliver(cfg, store, monkeypatch)
    assert len(sent) == 1 and "CRITICAL" in sent[0]
    assert len(store.incidents()) == 2


async def test_cap_warning_remains_visible_without_telegram_or_agent_noise(monitor, monkeypatch):
    from opswatch.service import Service

    cfg, store = monitor
    now = time.time()
    cap = observation("runtime-logs", kind="monitoring", evidence={"records": 1000, "limit": 1000})
    store.observe(cap, cfg, now - 1000)
    store.observe(cap, cfg, now)
    assert len(store.incidents()) == 1
    assert await deliver(cfg, store, monkeypatch) == []
    cfg.agent.auto_diagnose = True
    async with httpx.AsyncClient() as client:
        service = Service(cfg, store, client)

        calls = []

        async def forbidden(*args, **kwargs):
            calls.append(args)
            return "Unnecessary diagnosis"

        monkeypatch.setattr(service.agent, "diagnose", forbidden)
        await service.auto_diagnose(store.incidents()[0])
        assert calls == []


async def test_persistent_warning_is_delivered_once_and_reminder_state_survives_restart(monitor, monkeypatch):
    cfg, store = monitor
    now = time.time()
    store.observe(observation(), cfg, now - 601)
    assert not store.pending()
    store.observe(observation(), cfg, now)
    assert len(await deliver(cfg, store, monkeypatch)) == 1
    other = Store(cfg.data_dir)
    try:
        other.observe(observation(), cfg)
        assert not other.pending()
        assert await deliver(cfg, other, monkeypatch) == []
    finally:
        other.close()


async def test_recovery_for_unsent_or_short_warning_is_silent(monitor, monkeypatch):
    cfg, store = monitor
    store.observe(observation(), cfg)
    store.observe(observation(healthy=True), cfg)
    assert await deliver(cfg, store, monkeypatch) == []
    assert store.incidents(status="resolved")


async def test_announced_long_outage_recovers_once_but_quiet_log_events_do_not(monitor, monkeypatch):
    cfg, store = monitor
    for key, kind in [("http", "availability"), ("log", "application error")]:
        store.observe(observation(key, severity="critical", kind=kind), cfg, time.time() - 700)
    assert len(await deliver(cfg, store, monkeypatch)) == 2
    for key, kind in [("http", "availability"), ("log", "application error")]:
        store.observe(observation(key, healthy=True, kind=kind), cfg)
    sent = await deliver(cfg, store, monkeypatch)
    assert len(sent) == 1 and "RECOVERED" in sent[0]


async def test_critical_escalation_bypasses_warning_reminder_cooldown(monitor, monkeypatch):
    cfg, store = monitor
    store.observe(observation(), cfg, time.time() - 700)
    store.observe(observation(), cfg)
    assert len(await deliver(cfg, store, monkeypatch)) == 1
    store.observe(observation(severity="critical"), cfg)
    sent = await deliver(cfg, store, monkeypatch)
    assert len(sent) == 1 and "CRITICAL" in sent[0]


async def test_old_queued_outage_is_dropped_after_resolution(monitor, monkeypatch):
    cfg, store = monitor
    store.observe(observation(severity="critical"), cfg, time.time() - 700)
    store.observe(observation(healthy=True), cfg)
    assert await deliver(cfg, store, monkeypatch) == []
    assert not store.pending()
