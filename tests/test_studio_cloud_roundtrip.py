import time
from contextlib import closing
from uuid import uuid4

import httpx
from test_cloud_store import MemoryRelay

from opswatch.agent import Agent
from opswatch.cloud_bridge import CloudBridge
from opswatch.cloud_relay import application
from opswatch.config import Config, Target
from opswatch.storage import Store
from opswatch.studio import StudioWorker
from opswatch.types import Observation


async def test_browser_commands_reach_the_durable_local_agent_and_return_saved_history(tmp_path, monkeypatch):
    config = Config(
        data_dir=str(tmp_path / "local"),
        targets=[Target(id="checkout", name="Checkout", kind="http", url="https://checkout.example")],
    )
    relay = MemoryRelay()
    app = application(relay, "bridge-secret", "csrf-secret")
    headers = {"Origin": "https://dashboard.example", "X-OpsWatch-CSRF": "csrf-secret"}
    calls = []

    async def invoke(self, prompt):
        calls.append(prompt)
        return "Checkout returned HTTP 503 in the supplied observation [OW-0001]. Verify the request."

    monkeypatch.setattr(Agent, "invoke", invoke)
    with closing(Store(config.data_dir)) as store:
        store.set("heartbeat", time.time())
        store.observe(
            Observation("checkout", "http", "availability", False, "Checkout unavailable", {"status": 503}),
            config,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://dashboard.example"
        ) as browser:
            bridge = CloudBridge(
                config,
                store,
                client=browser,
                clock=relay.clock,
                url="https://dashboard.example",
                token="bridge-secret",
                publish_icons=False,
            )
            assert await bridge.sync_once()
            assert (await browser.get("/api/studio")).json()["pstack"]["name"] == "pstack"

            async def command(action, payload, *, repeat=False):
                identity = str(uuid4())
                for _ in range(2 if repeat else 1):
                    queued = await browser.post(
                        "/api/studio/actions",
                        json={"action": action, "payload": payload},
                        headers={**headers, "X-OpsWatch-Request-ID": identity},
                    )
                    assert queued.status_code == 202
                    assert queued.json()["command_id"] == identity
                assert await bridge.sync_once()
                completed = (await browser.get(f"/api/commands/{identity}")).json()
                assert completed["state"] == "done", completed
                return completed["result"]

            created = await command(
                "session_create",
                {"scope": {"kind": "service", "id": "checkout"}, "playbook": "bug-fix"},
            )
            session = created["session"]["id"]
            await command("message_send", {"session_id": session, "content": "Investigate 503"}, repeat=True)
            assert await StudioWorker(config).run_once()
            read = await command("session_read", {"session_id": session})
            assert [message["role"] for message in read["messages"]] == ["user", "assistant"]
            assert read["messages"][-1]["status"] == "complete"
            assert "HTTP 503" in read["messages"][-1]["content"]
            assert read["context"]["pstack"]["playbook"] == "bug-fix"
            assert len(calls) == 1
            assert "Checkout unavailable" in calls[0]
            assistant = read["messages"][-1]["id"]
            await command(
                "feedback", {"subject_type": "message", "subject_id": assistant, "rating": "helpful"}
            )
            reloaded = await command("session_read", {"session_id": session})
            assert reloaded["messages"][-1]["feedback"] == "helpful"
