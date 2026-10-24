from __future__ import annotations

import asyncio
import fcntl
import logging
import signal
import time
from pathlib import Path

import httpx

from .agent import Agent
from .config import Config
from .connectors import Collector
from .redact import redact_text
from .security import Security
from .selection import refresh_selection
from .storage import Store
from .telegram import Telegram
from .types import Observation

log = logging.getLogger("opswatch")


class Service:
    def __init__(self, config: Config, store: Store, client: httpx.AsyncClient):
        self.config, self.store, self.client = config, store, client
        self.collector = Collector(config, store, client)
        self.security = Security(config, store, client)
        self.agent = Agent(config, store)
        self.telegram = Telegram(config, store, client, self.agent) if config.telegram.enabled else None
        self.stopped = asyncio.Event()
        self.semaphore = asyncio.Semaphore(config.concurrency)
        self.auto_lock = asyncio.Lock()
        self.delivery_lock = asyncio.Lock()
        self.next_check = {}
        self.enabled_targets = {t.id for t in config.targets if t.enabled}
        self.background: set[asyncio.Task] = set()

    async def check_target(self, target):
        async with self.semaphore:
            refresh_selection(self.config)
            if not target.enabled:
                return
            items, events = await self.collector.collect(target)
            refresh_selection(self.config)
            if not target.enabled:
                return
            for event in events:
                refresh_selection(self.config)
                if not target.enabled:
                    return
                item = await self.security.inspect(target, event)
                if item:
                    items.append(item)
            for item in items:
                refresh_selection(self.config)
                if not target.enabled:
                    return
                notification = self.store.observe(item, self.config)
                if notification:
                    log.info("%s %s %s", notification["ref"], notification["event"], notification["title"])
                    if (
                        self.config.agent.auto_diagnose
                        and notification["event"] == "opened"
                        and self.telegram
                    ):
                        task = asyncio.create_task(self.auto_diagnose(notification))
                        self.background.add(task)
                        task.add_done_callback(self.background.discard)
            self.store.observe(
                Observation(target.id, "processing", "monitoring", True, "Alert processing connected"),
                self.config,
            )
            # Log and scan incidents stop after a quiet period. This says observation stopped,
            # rather than asserting that a vulnerability or bug was fixed.
            now = time.time()
            for incident in self.store.incidents(200):
                if (
                    incident["target"] == target.id
                    and incident["kind"] in {"application error", "security"}
                    and now - incident["updated"] > 900
                    and any(i.key == "collector" and i.healthy for i in items)
                    and not any(
                        i.healthy is False
                        and i.key
                        in {"collector", "runtime-logs", "http-logs", "issue-coverage", "log-coverage"}
                        for i in items
                    )
                ):
                    self.store.observe(
                        Observation(
                            target.id,
                            incident["key"],
                            incident["kind"],
                            True,
                            "No matching events in the last 15 minutes",
                            {"quiet_seconds": 900},
                        ),
                        self.config,
                    )
            self.next_check[target.id] = time.monotonic() + target.interval_seconds
        if self.telegram:
            async with self.delivery_lock:
                await self.telegram.deliver()

    async def auto_diagnose(self, incident):
        from .notifications import Notifications

        refresh_selection(self.config)
        target = next((t for t in self.config.targets if t.id == incident["target"]), None)
        if (target and not target.enabled) or not Notifications(self.config, self.store).diagnoses(incident):
            return
        async with self.auto_lock:
            refresh_selection(self.config)
            if target and not target.enabled:
                return
            recent = self.store.get("auto_diagnosed:" + incident["target"], 0)
            if time.time() - recent < 1200:
                return
            used = self.store.db.execute(
                "SELECT count(*) FROM agent_runs WHERE created>?", (time.time() - 3600,)
            ).fetchone()[0]
            if used >= self.config.agent.max_per_hour:
                return
            try:
                related = [row for row in self.store.incidents(50) if row["target"] == incident["target"]]
                reply = await self.agent.diagnose(
                    "Suggest a fix for this incident and its related alerts.",
                    {"incident": incident, "related_incidents": related},
                )
                refresh_selection(self.config)
                if target and not target.enabled:
                    return
                from .formatting import text_message

                await self.telegram.send(text_message(f"Agent suggestion for {incident['ref']}\n{reply}"))
                self.store.set("auto_diagnosed:" + incident["target"], time.time())
            except Exception as error:
                log.warning("Automatic diagnosis: %s", redact_text(str(error), 300))

    async def cycle(self, all_targets: bool = False):
        refresh_selection(self.config)
        enabled = {t.id for t in self.config.targets if t.enabled}
        for identity in enabled - self.enabled_targets:
            self.next_check.pop(identity, None)
        self.enabled_targets = enabled
        targets = [
            t
            for t in self.config.targets
            if t.enabled and (all_targets or self.next_check.get(t.id, 0) <= time.monotonic())
        ]
        if targets:
            results = await asyncio.gather(*(self.check_target(t) for t in targets), return_exceptions=True)
            for target, result in zip(targets, results, strict=True):
                refresh_selection(self.config)
                if not target.enabled:
                    continue
                if isinstance(result, Exception):
                    log.error("%s collection failed: %s", target.id, redact_text(str(result), 300))
                    self.store.observe(
                        Observation(
                            target.id,
                            "processing",
                            "monitoring",
                            False,
                            "Monitor could not process this target",
                            {"error": redact_text(str(result), 600)},
                            immediate=True,
                            next_step="Check monitoring logs and collection permissions. "
                            "Application state is unknown.",
                        ),
                        self.config,
                    )
        self.store.set("heartbeat", time.time())
        if self.telegram:
            await self.telegram.deliver()

    async def run(self):
        # One local poller and monitor per data directory; MCP readers may coexist.
        lock_file = (Path(self.config.data_dir) / "monitor.lock").open("w")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            raise RuntimeError("Opswatch is already running for this data directory") from None
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.stopped.set)
        tasks = []
        try:
            if self.config.ingest.enabled:
                import uvicorn

                from .ingest import application

                server = uvicorn.Server(
                    uvicorn.Config(
                        application(self),
                        host=self.config.ingest.host,
                        port=self.config.ingest.port,
                        log_level="warning",
                        access_log=False,
                    )
                )
                tasks.append(asyncio.create_task(server.serve()))
            if self.telegram and self.config.telegram.mode == "standalone":
                tasks.append(asyncio.create_task(self.telegram.poll(self.stopped)))
            log.info(
                "Monitoring %s targets; policy dry_run=%s",
                sum(t.enabled for t in self.config.targets),
                self.config.policy.dry_run,
            )
            previous = time.monotonic()
            while not self.stopped.is_set():
                for task in tasks:
                    if task.done():
                        task.result()
                now = time.monotonic()
                # Resume a sleeping laptop with immediate checks, and record the monitoring gap.
                if now - previous > 180:
                    self.next_check.clear()
                    self.store.audit("monitor", "monitoring_gap", {"seconds": round(now - previous)})
                previous = now
                await self.cycle()
                if self.config.heartbeat_url_env:
                    from .config import secret

                    if self.store.get("last_external_heartbeat", 0) < time.time() - 60:
                        try:
                            response = await self.client.get(
                                secret(self.config.heartbeat_url_env), timeout=10
                            )
                            response.raise_for_status()
                            self.store.set("last_external_heartbeat", time.time())
                        except Exception as error:
                            log.warning("External heartbeat failed: %s", type(error).__name__)
                if self.store.get("last_prune", 0) < time.time() - 3600:
                    self.store.prune(self.config.retention_days)
                    self.store.set("last_prune", time.time())
                try:
                    await asyncio.wait_for(self.stopped.wait(), 2)
                except TimeoutError:
                    pass
        finally:
            for task in [*tasks, *self.background]:
                task.cancel()
            await asyncio.gather(*tasks, *self.background, return_exceptions=True)
            lock_file.close()
