"""Outbound dashboard synchronization; monitoring and repair execution stay local."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import fcntl
import gzip
import hashlib
import json
import logging
import math
import os
import re
import signal
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from .cloud_store import READ_RESULT_RETENTION
from .config import Config, load_config, secret
from .dashboard import public_repair, snapshot
from .favicons import Favicons
from .repairs import Repairs
from .selection import set_selection
from .storage import Store
from .studio_protocol import READ_ACTIONS, validate_action

log = logging.getLogger(__name__)
REQUEST_LIMIT = 4 * 1024 * 1024


def encoded(payload: dict) -> bytes:
    return json.dumps(payload, allow_nan=False).encode()


def relay_snapshot(config: Config) -> dict | None:
    value = snapshot(config)
    if config is not None:
        from .studio import studio_snapshot

        value["studio"] = studio_snapshot(config)
    if len(encoded(value)) <= REQUEST_LIMIT // 2:
        return value

    def compact(item):
        if isinstance(item, dict):
            return {
                key: (
                    {"notice": "Large evidence omitted from cloud view; inspect locally."}
                    if key == "evidence" and len(encoded({key: entry})) > 2048
                    else compact(entry)
                )
                for key, entry in item.items()
            }
        if isinstance(item, list):
            return [compact(entry) for entry in item]
        return item[:2000] + "…" if isinstance(item, str) and len(item) > 2000 else item

    value = compact(value)
    while len(encoded(value)) > REQUEST_LIMIT // 2:
        protected = {
            "incidents": lambda item: item.get("status") == "open",
            "repairs": lambda item: item.get("status") not in {"pr_open", "blocked", "cancelled"},
            "queries": lambda item: False,
        }
        changed = False
        for key, keep in protected.items():
            records = value.get(key, [])
            history = [item for item in records if not keep(item)]
            if history:
                retained = {id(item) for item in history[: len(history) // 2]}
                value[key] = [item for item in records if keep(item) or id(item) in retained]
                changed = True
        if not changed:
            # Keep the last cloud snapshot stale rather than claim an incomplete inventory is fresh.
            log.warning("Cloud snapshot exceeds its size budget; inspect local dashboard")
            return None
    return value


def command_id(value) -> str | None:
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        return value if str(UUID(value)) == value else None
    except ValueError:
        return None


class CloudBridge:
    def __init__(
        self,
        config: Config,
        store: Store,
        *,
        client: httpx.AsyncClient | None = None,
        clock=time.time,
        url: str | None = None,
        token: str | None = None,
        bypass_token: str | None = None,
        publish_icons: bool = True,
    ):
        self.config, self.store, self.client, self.clock = config, store, client, clock
        relay = (url if url is not None else secret("OPSWATCH_RELAY_URL")).rstrip("/")
        try:
            parsed = urlsplit(relay)
            valid = (
                parsed.hostname
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
                and parsed.path in {"", "/"}
                and (
                    parsed.scheme == "https"
                    or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
                )
            )
            _ = parsed.port  # Reject malformed ports without including the URL in errors.
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Relay URL must be an HTTPS origin without credentials or query parameters")
        self.url = relay + "/api/bridge/sync"
        bridge_token = token if token is not None else secret("OPSWATCH_RELAY_BRIDGE_TOKEN")
        self.headers = {
            "Authorization": "Bearer " + bridge_token,
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
        }
        bypass = bypass_token if bypass_token is not None else os.environ.get("OPSWATCH_RELAY_BYPASS_TOKEN")
        if bypass:
            self.headers["x-vercel-protection-bypass"] = bypass
        self.publish_icons = publish_icons
        self.favicons = Favicons(
            config.model_copy(update={"targets": [t for t in config.targets if t.enabled]})
        )
        self.icon_task: asyncio.Task | None = None
        self.next_icons = 0.0
        self.icons: dict[str, dict] = {}
        self.icon_hashes: dict[str, str] = {}
        self.next_snapshot = 0.0
        self.stop = asyncio.Event()
        with self.store.db:
            self.store.db.execute("""CREATE TABLE IF NOT EXISTS cloud_bridge_commands (
                id TEXT PRIMARY KEY, result TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0,
                discard_after REAL
            )""")
            columns = {row[1] for row in self.store.db.execute("PRAGMA table_info(cloud_bridge_commands)")}
            if "discard_after" not in columns:
                self.store.db.execute("ALTER TABLE cloud_bridge_commands ADD COLUMN discard_after REAL")

    def current_config(self) -> Config:
        if self.config._source_path:
            latest = load_config(self.config._source_path)
            if Path(latest.data_dir).resolve() != Path(self.config.data_dir).resolve():
                raise ValueError("Local data directory changed; restart the cloud bridge")
            self.config = latest
        return self.config

    def pending_results(self) -> list[dict]:
        self.prune_read_results()
        return [
            json.loads(row[0])
            for row in self.store.db.execute(
                "SELECT result FROM cloud_bridge_commands WHERE acknowledged=0 ORDER BY rowid LIMIT 50"
            )
        ]

    def prune_read_results(self):
        with self.store.db:
            self.store.db.execute("DELETE FROM cloud_bridge_commands WHERE discard_after<=?", (self.clock(),))

    def save_result(self, result: dict, discard_after=None):
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO cloud_bridge_commands(id,result,acknowledged,discard_after) VALUES (?,?,0,?) "
                "ON CONFLICT(id) DO UPDATE SET result=excluded.result,acknowledged=0,"
                "discard_after=COALESCE(cloud_bridge_commands.discard_after,excluded.discard_after)",
                (result["id"], json.dumps(result, allow_nan=False), discard_after),
            )

    def process_command(self, command) -> tuple[bool, bool]:
        if not isinstance(command, dict) or not (identity := command_id(command.get("id"))):
            return False, False
        self.prune_read_results()
        row = self.store.db.execute(
            "SELECT result FROM cloud_bridge_commands WHERE id=?", (identity,)
        ).fetchone()
        if row:
            # A lost acknowledgment may cause an already completed command to be leased again.
            self.save_result(json.loads(row[0]))
            return True, False
        result = {"id": identity, "result": None, "error": "Invalid or expired command."}
        valid = False
        discard_after = None
        try:
            valid = self.valid_command(command)
        except (TypeError, ValueError, OverflowError):
            pass
        if valid:
            if command["kind"] == "studio" and command["payload"]["action"] in READ_ACTIONS:
                discard_after = command["expires_at"] + READ_RESULT_RETENTION
            # Persist before side effects. A crash in the narrow execution window must never replay
            # a mutation; the dashboard can report the uncertain result and let the owner review.
            result["error"] = "Local execution was interrupted; review local state before retrying."
            self.save_result(result, discard_after)
            try:
                config = self.current_config()
                if command["kind"] == "fix":
                    result["result"] = public_repair(
                        Repairs(config, self.store).enqueue(command["target"], "cloud-dashboard")
                    )
                elif command["kind"] == "studio":
                    from .studio import Studio

                    action = command["payload"]
                    result["result"] = Studio(config, self.store).dispatch(
                        action["action"], action["payload"], identity
                    )
                    if len(encoded(result["result"])) > 60000:
                        result["result"] = None
                        raise ValueError("The agent result exceeded the cloud page size. Use a smaller page.")
                else:
                    enabled = command["payload"]["enabled"]
                    targets = set_selection(config, command["kind"], command["target"], enabled)
                    result["result"] = {"enabled": enabled, "targets": targets}
                result["error"] = None
            except Exception:
                # Exception text can contain provider secrets, filesystem paths or remote bodies.
                result["error"] = (
                    "Command could not be completed locally. Review local configuration and state."
                )
        self.save_result(result, discard_after)
        return True, result["error"] is None

    def valid_command(self, command: dict) -> bool:
        if set(command) != {"id", "kind", "target", "payload", "expires_at"}:
            return False
        expiry = command["expires_at"]
        if type(expiry) not in {int, float} or not math.isfinite(expiry):
            return False
        if not self.clock() < expiry <= self.clock() + 120:
            return False
        target, payload, kind = command["target"], command["payload"], command["kind"]
        if not isinstance(target, str) or not 1 <= len(target) <= 200 or not isinstance(payload, dict):
            return False
        if kind == "fix":
            return not payload and bool(re.fullmatch(r"OW-[0-9]{1,17}", target))
        if kind == "studio":
            if target != "workspace" or set(payload) != {"action", "payload"}:
                return False
            try:
                validate_action(payload["action"], payload["payload"])
            except (TypeError, ValueError):
                return False
            return True
        return (
            kind in {"services", "projects"}
            and set(payload) == {"enabled"}
            and type(payload["enabled"]) is bool
        )

    async def collect_icons(self):
        """Use the existing daily cache without delaying command polling."""
        try:
            enabled = [target.id for target in self.config.targets if target.enabled][:100]
            # Preserve Favicons' daily origin cache across selection/config refreshes.
            updated = Favicons(
                self.config.model_copy(
                    update={
                        "targets": [t for t in self.config.targets if t.enabled],
                    }
                )
            )
            self.favicons.origins = updated.origins
            for target in enabled:
                if self.stop.is_set():
                    break
                icon = await asyncio.wait_for(self.favicons.get(target), timeout=15)
                if icon and len(icon.data) <= 262144:
                    digest = hashlib.sha256(icon.data).hexdigest()
                    if self.icon_hashes.get(target) != digest:
                        self.icons[target] = {
                            "target": target,
                            "content_type": icon.content_type,
                            "data_base64": base64.b64encode(icon.data).decode("ascii"),
                        }
                        self.icon_hashes[target] = digest
        except (Exception, asyncio.CancelledError):
            pass

    async def exchange(self, payload: dict) -> dict:
        async def request(client):
            async with client.stream(
                "POST",
                self.url,
                content=gzip.compress(encoded(payload)),
                headers=self.headers,
                timeout=20,
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                data = bytearray()
                async for part in response.aiter_bytes():
                    data.extend(part)
                    if len(data) > 262144:
                        raise ValueError("Relay response exceeded its limit")
                result = json.loads(data)
                if not isinstance(result, dict) or not isinstance(result.get("acknowledged"), list):
                    raise ValueError("Invalid relay response")
                return result

        if self.client:
            return await request(self.client)
        async with httpx.AsyncClient(trust_env=False) as client:
            return await request(client)

    async def sync_once(self) -> bool:
        try:
            self.current_config()
            payload = {}
            if self.clock() >= self.next_snapshot:
                if (current := relay_snapshot(self.config)) is not None:
                    payload["snapshot"] = current
            if (
                self.publish_icons
                and self.clock() >= self.next_icons
                and (self.icon_task is None or self.icon_task.done())
            ):
                self.icon_task = asyncio.create_task(self.collect_icons())
                self.next_icons = self.clock() + 3600
            for _ in range(8):
                pending = self.pending_results()
                if pending:
                    payload["results"] = pending
                enabled = {t.id for t in self.config.targets if t.enabled}
                icons = []
                for key, icon in self.icons.items():
                    if key not in enabled:
                        continue
                    candidate = icons + [icon]
                    if len(encoded({**payload, "icons": candidate})) > REQUEST_LIMIT:
                        break
                    icons = candidate
                    if len(icons) == 8:
                        break
                if icons:
                    payload["icons"] = icons
                response = await self.exchange(payload)
                sent_ids = {result["id"] for result in pending}
                with self.store.db:
                    for identity in response["acknowledged"]:
                        if command_id(identity) in sent_ids:
                            self.store.db.execute(
                                "UPDATE cloud_bridge_commands SET acknowledged=1 WHERE id=?", (identity,)
                            )
                if "snapshot" in payload:
                    self.next_snapshot = self.clock() + 30
                for icon in icons:
                    if self.icons.get(icon["target"]) == icon:
                        del self.icons[icon["target"]]
                received, mutated = self.process_command(response.get("command"))
                if not received:
                    return True
                current = relay_snapshot(self.config) if mutated else None
                payload = {"snapshot": current} if current is not None else {}
            return True
        except Exception:
            log.warning("Cloud dashboard synchronization failed; retrying on the next poll")
            return False

    async def serve(self):
        lock_path = Path(self.config.data_dir) / "cloud-bridge.lock"
        with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600), "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("The cloud bridge is already running") from None
            loop = asyncio.get_running_loop()
            for signum in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(signum, self.stop.set)
            from .studio import StudioWorker

            studio_task = asyncio.create_task(StudioWorker(self.config).serve(self.stop))
            try:
                while not self.stop.is_set():
                    await self.sync_once()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self.stop.wait(), timeout=10)
            finally:
                self.stop.set()
                studio_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await studio_task
                if self.icon_task:
                    self.icon_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self.icon_task
                for signum in (signal.SIGTERM, signal.SIGINT):
                    loop.remove_signal_handler(signum)
