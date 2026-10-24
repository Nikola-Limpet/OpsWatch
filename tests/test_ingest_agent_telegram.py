import hashlib
import hmac
import json
import time

import httpx

from opswatch.agent import Agent
from opswatch.config import Config, Target
from opswatch.ingest import application
from opswatch.service import Service
from opswatch.storage import Store
from opswatch.telegram import Telegram, chunks
from opswatch.types import Observation


async def test_signed_ingest_auth_and_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_SECRET", "some-long-test-secret")
    cfg = Config(
        data_dir=str(tmp_path),
        targets=[
            Target(
                id="web", name="Web", kind="http", url="https://example.com", ingest_secret_env="EVENT_SECRET"
            )
        ],
    )
    store = Store(str(tmp_path))
    async with httpx.AsyncClient() as backend:
        service = Service(cfg, store, backend)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application(service)), base_url="http://test"
        ) as client:
            data = json.dumps([{"timestamp": time.time(), "message": "error", "level": "error"}]).encode()
            stamp = str(int(time.time()))
            headers = {
                "X-Opswatch-Timestamp": stamp,
                "X-Opswatch-Signature": hmac.new(
                    b"some-long-test-secret", stamp.encode() + b"." + data, hashlib.sha256
                ).hexdigest(),
            }
            assert (await client.post("/events/web", content=data)).status_code == 401
            assert (await client.post("/events/web", content=data, headers=headers)).status_code == 200
            assert (await client.post("/events/web", content=data, headers=headers)).status_code == 200
            assert len(store.incidents()) == 1
            assert len(store.pending()) == 1
            assert (await client.get("/healthz")).status_code == 503
            store.set("heartbeat", time.time())
            assert (await client.get("/healthz")).status_code == 200
    store.close()


async def test_agent_budget_model_and_redaction(tmp_path, monkeypatch):
    cfg, store = Config(data_dir=str(tmp_path)), Store(str(tmp_path))
    cfg.agent.max_per_hour = 1
    monkeypatch.setenv("TEST_API_TOKEN", "sensitive-token-value")
    agent = Agent(cfg, store)
    agent.set_model("gpt-6.1-sol")
    prompts = []

    async def invoke(prompt):
        prompts.append(prompt)
        return "Suggested fix: retry the failed connection."

    monkeypatch.setattr(agent, "invoke", invoke)
    reply = await agent.diagnose("Find the cause", {"message": "sensitive-token-value"})
    assert "Suggested fix" in reply
    assert "sensitive-token-value" not in prompts[0]
    assert "UNTRUSTED DATA" in prompts[0]
    assert "limit" in await agent.diagnose("again", {})
    assert Agent(cfg, store).model() == "gpt-6.1-sol"
    store.close()


def test_html_chunks_preserve_escaped_evidence():
    text = "<pre>" + "&lt;script&gt;" * 1000 + "</pre>"
    parts = chunks(text)
    assert len(parts) > 1
    assert all(len(part.encode("utf-16-le")) // 2 < 4096 * 5 for part in parts)
    assert all("<script>" not in part for part in parts)


async def test_telegram_commands_require_both_owner_and_chat(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "100")
    monkeypatch.setenv("TELEGRAM_USER_ID", "1")
    cfg, store = Config(data_dir=str(tmp_path)), Store(str(tmp_path))
    stopped, handled = asyncio.Event(), []
    async with httpx.AsyncClient() as client:
        bot = Telegram(cfg, store, client, Agent(cfg, store))

        async def api(method, payload, **kwargs):
            if method != "getUpdates":
                return {}
            stopped.set()
            return [
                {
                    "update_id": i,
                    "message": {
                        "chat": {"id": chat},
                        "from": {"id": user},
                        "date": int(time.time()),
                        "text": "/model gpt-6.1-sol",
                    },
                }
                for i, (chat, user) in enumerate([(100, 2), (200, 1), (100, 1)], start=1)
            ]

        async def command(text, user):
            handled.append(user)

        monkeypatch.setattr(bot, "api", api)
        monkeypatch.setattr(bot, "command", command)
        await bot.poll(stopped)
        assert handled == ["1"]
        assert store.get("telegram_offset") == 4
    store.close()


async def test_auto_diagnosis_coalesces_simultaneous_alerts(tmp_path, monkeypatch):
    import asyncio

    cfg, store = Config(data_dir=str(tmp_path)), Store(str(tmp_path))
    first = store.observe(
        Observation("web", "http", "availability", False, "HTTP failed", immediate=True), cfg
    )
    second = store.observe(
        Observation("web", "deploy", "deployment", False, "Deploy failed", immediate=True), cfg
    )
    sent, evidence = [], []
    async with httpx.AsyncClient() as client:
        service = Service(cfg, store, client)

        class Bot:
            async def send(self, message):
                sent.append(message)

        async def diagnose(question, data):
            evidence.append(data)
            await asyncio.sleep(0.01)
            return "Finding: failed deployment and unavailable endpoint."

        service.telegram = Bot()
        monkeypatch.setattr(service.agent, "diagnose", diagnose)
        await asyncio.gather(service.auto_diagnose(first), service.auto_diagnose(second))
        assert len(sent) == 1
        assert len(evidence[0]["related_incidents"]) == 2
    store.close()
