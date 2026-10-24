import os
import time
from uuid import uuid4

import httpx
import psycopg
import pytest
import yaml
from psycopg import sql
from psycopg.conninfo import make_conninfo

from opswatch.cloud_bridge import CloudBridge
from opswatch.cloud_relay import application
from opswatch.cloud_store import PostgresRelay
from opswatch.config import load_config
from opswatch.storage import Store


@pytest.mark.skipif(not os.getenv("OPSWATCH_TEST_DATABASE_URL"), reason="Requires isolated Postgres")
async def test_cloud_control_is_processed_locally_and_published_before_completion(tmp_path):
    dsn = os.environ["OPSWATCH_TEST_DATABASE_URL"]
    schema = "bridge_" + uuid4().hex
    with psycopg.connect(dsn) as db:
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        relay = PostgresRelay(make_conninfo(dsn, options=f"-c search_path={schema}"))
        relay.initialize()
        path = tmp_path / "config.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "data_dir": "data",
                    "targets": [
                        {"id": "web", "name": "Web", "kind": "http", "url": "https://example.invalid"},
                    ],
                }
            )
        )
        config = load_config(path)
        store = Store(config.data_dir)
        store.set("heartbeat", time.time())
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application(relay, "bridge-token", "csrf-token")),
                base_url="https://relay.example.test",
            ) as client:
                bridge = CloudBridge(
                    config,
                    store,
                    client=client,
                    url="https://relay.example.test",
                    token="bridge-token",
                    publish_icons=False,
                )
                assert await bridge.sync_once()
                identity = str(uuid4())
                headers = {
                    "Origin": "https://relay.example.test",
                    "X-OpsWatch-CSRF": "csrf-token",
                    "X-OpsWatch-Request-ID": identity,
                }
                queued = await client.post(
                    "/api/services/web/monitoring", json={"enabled": False}, headers=headers
                )
                assert queued.status_code == 202
                assert await bridge.sync_once()
                completed = (await client.get(f"/api/commands/{identity}")).json()
                assert completed["state"] == "done"
                assert completed["result"] == {"enabled": False, "targets": ["web"]}
                current = (await client.get("/api/snapshot")).json()
                assert current["summary"]["total"] == 0
                assert current["projects"][0]["services"][0]["enabled"] is False
                assert load_config(path).targets[0].enabled is False
                retry = await client.post(
                    "/api/services/web/monitoring", json={"enabled": False}, headers=headers
                )
                assert retry.json()["command_id"] == identity
                assert store.db.execute("SELECT count(*) FROM cloud_bridge_commands").fetchone()[0] == 1
        finally:
            store.close()
    finally:
        with psycopg.connect(dsn) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
