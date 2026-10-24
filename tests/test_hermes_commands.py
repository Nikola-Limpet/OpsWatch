import importlib
import json

import httpx
import pytest

from opswatch.agent import Agent
from opswatch.config import Config
from opswatch.config import Telegram as TelegramConfig
from opswatch.storage import Store
from opswatch.types import Observation


def route():
    try:
        return importlib.import_module("opswatch.hermes_commands").handle
    except ModuleNotFoundError:
        pytest.fail("Hermes has no Opswatch command route")


def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "100")
    monkeypatch.setenv("TELEGRAM_USER_ID", "1")
    config = Config(telegram=TelegramConfig(enabled=True, mode="hermes"))
    return config, Store(str(tmp_path))


async def test_bare_diagnose_gives_usage_instead_of_unknown_command(monkeypatch, tmp_path):
    cfg, store = setup(monkeypatch, tmp_path)
    sent = []

    def receive(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
        assert await route()(cfg, store, client, "diagnose", "", "1")
    assert "/diagnose OW-0001" in sent[0]["text"]
    assert "Unknown command" not in sent[0]["text"]
    store.close()


async def test_hermes_diagnosis_routes_recorded_incident_to_agent(monkeypatch, tmp_path):
    cfg, store = setup(monkeypatch, tmp_path)
    item = store.observe(
        Observation("web", "error", "application error", False, "Connection refused", {}, immediate=True), cfg
    )

    async def invoke(self, prompt):
        assert "Connection refused" in prompt
        return "Finding: connection failed. Suggested fix: check the database service."

    monkeypatch.setattr(Agent, "invoke", invoke)
    sent = []

    def receive(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
        assert await route()(cfg, store, client, "diagnose", item["ref"], "1")
    assert len(sent) == 2
    assert "Suggested fix" in sent[-1]["text"]
    assert store.db.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 1
    store.close()


async def test_hermes_command_rejects_unapproved_sender_before_read_or_send(monkeypatch, tmp_path):
    cfg, store = setup(monkeypatch, tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("sent to API"))
    ) as client:
        assert not await route()(cfg, store, client, "opsstatus", "", "999")
        assert not await route()(cfg, store, client, "opsmodel", "another-model", "999")
        assert not await route()(cfg, store, client, "fix", "OW-0001", "999")
    assert store.get("agent_model") is None
    store.close()


def test_latency_evidence_shows_measurement_alongside_threshold():
    from opswatch.formatting import evidence_text

    assert "Latency: 2400 ms" in evidence_text({"latency_ms": 2400, "threshold_ms": 1500})
