import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from test_alert_formatting import sample
from test_hermes_commands import setup

from opswatch import formatting
from opswatch.config import Config, Repair, RepairRepository, Target


def test_incident_actions_carry_id_and_only_offer_configured_manual_repairs(tmp_path):
    from opswatch.storage import Store

    cfg = Config(
        repair=Repair(
            enabled=True, repositories=[RepairRepository(id="web", github="a/b", checks=[["true"]])]
        ),
        targets=[Target(id="web", name="Web", kind="http", url="https://example.com", repair_repo="web")],
    )
    store = Store(str(tmp_path))
    item = sample(cfg, store)
    buttons = [b for row in formatting.incident_markup(item, cfg)["inline_keyboard"] for b in row]
    assert {b.get("callback_data") for b in buttons} >= {
        f"ow:diagnose:{item['ref']}",
        f"ow:fix:{item['ref']}",
    }
    item["kind"] = "security"
    assert not any(
        b.get("callback_data", "").startswith("ow:fix:")
        for row in formatting.incident_markup(item, cfg)["inline_keyboard"]
        for b in row
    )
    store.close()


def test_repair_actions_respect_terminal_states():
    from opswatch.formatting import repair_markup

    job = {"id": "RF-0001", "incident": "OW-0002", "status": "coding"}
    assert "ow:cancelfix:RF-0001" in str(repair_markup(job))
    job.update(status="pr_open", pr_url="https://github.com/a/b/pull/1")
    assert "cancelfix" not in str(repair_markup(job))
    assert job["pr_url"] in str(repair_markup(job))


@pytest.mark.parametrize(
    "user,chat,data,allowed",
    [
        ("1", "100", "ow:incident:OW-0001", True),
        ("2", "100", "ow:fix:OW-0001", False),
        ("1", "999", "ow:fix:OW-0001", False),
        ("1", "100", "ow:fix:OW-0001;ls", False),
        ("1", "100", "ow:opsmodel:evil", False),
    ],
)
async def test_callback_validates_owner_chat_and_fixed_payload(
    monkeypatch, tmp_path, user, chat, data, allowed
):
    from opswatch.hermes_commands import callback
    from opswatch.telegram import Telegram

    cfg, store = setup(monkeypatch, tmp_path)
    command = AsyncMock()
    monkeypatch.setattr(Telegram, "command", command)
    monkeypatch.setattr(Telegram, "api", AsyncMock())
    async with httpx.AsyncClient() as client:
        assert await callback(cfg, store, client, data, user, chat, "query-id") is allowed
    assert command.await_count == int(allowed)
    if allowed:
        command.assert_awaited_once_with("/incident OW-0001", "1")
    store.close()


async def test_adapter_extension_preserves_hermes_callbacks_and_dispatches_ours(monkeypatch):
    path = Path(__file__).parents[1] / "scripts/hermes-plugin/handler.py"
    spec = importlib.util.spec_from_file_location("opswatch_hook_test", path)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    original = AsyncMock()
    adapter = type("Adapter", (), {"_handle_callback_query": original})
    bridge = AsyncMock()
    monkeypatch.setattr(hook, "dispatch_callback", bridge)
    hook.install_callbacks(adapter)
    hook.install_callbacks(adapter)
    update = SimpleNamespace(callback_query=SimpleNamespace(data="ow:incident:OW-0001"))
    await adapter()._handle_callback_query(update, None)
    await __import__("asyncio").sleep(0)
    bridge.assert_awaited_once()
    update.callback_query.data = "mp:existing"
    await adapter()._handle_callback_query(update, None)
    original.assert_awaited_once()
