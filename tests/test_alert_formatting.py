import json
import time

import httpx

from opswatch import formatting
from opswatch.agent import Agent
from opswatch.config import Config, Target
from opswatch.storage import Store
from opswatch.telegram import PlainText, Telegram
from opswatch.types import Observation


def sample(cfg, store, **values):
    item = store.observe(
        Observation(
            "web",
            "error",
            "application error",
            False,
            "Application error",
            {"message": "Database failed", "path": "/api/orders", "status": 500},
            immediate=True,
        ),
        cfg,
    )
    return {**item, **values}


def test_error_alert_has_readable_evidence_and_keeps_payloads_escaped(tmp_path):
    cfg = Config(
        targets=[Target(id="web", name="SMS", kind="vercel", project="sms", url="https://sms.example.com")]
    )
    store = Store(str(tmp_path))
    item = sample(cfg, store)
    item["evidence"]["message"] = "<script>token=secret-value</script>"
    message = formatting.incident_message(item, cfg)
    assert "HTTP 500" in message
    assert "/api/orders" in message
    assert "<script>" not in message
    assert "secret-value" not in message
    assert '"message"' not in message
    store.close()


def test_alert_button_uses_configured_production_app_not_deployment_alias(tmp_path):
    cfg = Config(
        targets=[Target(id="web", name="SMS", kind="vercel", project="sms", url="https://sms.example.com")]
    )
    store = Store(str(tmp_path))
    item = sample(cfg, store)
    item["evidence"]["deployment_url"] = "sms-preview.vercel.app"
    buttons = formatting.incident_markup(item, cfg)["inline_keyboard"][0]
    assert next(b["url"] for b in buttons if b["text"] == "Open app") == "https://sms.example.com"
    assert all("vercel.app" not in b["url"] for b in buttons)
    store.close()


def test_untrusted_issue_link_cannot_create_a_telegram_button(tmp_path):
    cfg = Config()
    store = Store(str(tmp_path))
    item = sample(cfg, store)
    item["evidence"]["permalink"] = "https://phishing.example.com/token=secret"
    assert "url" not in str(formatting.incident_markup(item, cfg))
    store.close()


def test_large_unicode_error_fits_one_telegram_message(tmp_path):
    cfg = Config()
    store = Store(str(tmp_path))
    item = sample(cfg, store)
    item["title"] = "Failure " + "💥" * 3000
    item["evidence"]["message"] = "💥" * 3000
    item["next_step"] = "💥" * 3000
    item["action"] = "💥" * 3000
    parser = PlainText()
    parser.feed(formatting.incident_message(item, cfg))
    assert len("".join(parser.parts).encode("utf-16-le")) // 2 <= 3800
    store.close()


async def test_bot_honors_rate_limit_and_keeps_outage_before_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "100")
    monkeypatch.setenv("TELEGRAM_USER_ID", "1")
    cfg = Config(failures_before_alert=1, recoveries_before_alert=1)
    store = Store(str(tmp_path))
    store.observe(Observation("web", "http", "availability", False, "Down"), cfg)
    store.observe(Observation("web", "http", "availability", True, "Recovered"), cfg)

    def rate_limit(request):
        return httpx.Response(
            429, json={"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 120}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(rate_limit)) as client:
        bot = Telegram(cfg, store, client, Agent(cfg, store))
        await bot.deliver()
    rows = store.db.execute("SELECT id,retry_at FROM outbox ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0]["retry_at"] >= time.time() + 119
    assert store.pending() == []
    store.close()


async def test_invalid_html_falls_back_to_readable_plain_text(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "100")
    monkeypatch.setenv("TELEGRAM_USER_ID", "1")
    cfg, store = Config(), Store(str(tmp_path))
    payloads = []

    def send(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) == 1:
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: can't parse entities"})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
        bot = Telegram(cfg, store, client, Agent(cfg, store))
        await bot.send("<b>Critical</b>\nDatabase unavailable")
    assert payloads[1]["text"] == "Critical\nDatabase unavailable"
    assert "parse_mode" not in payloads[1]
    store.close()
