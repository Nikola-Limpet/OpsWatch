from __future__ import annotations

import asyncio
import json
import time
from html import escape
from html.parser import HTMLParser

import httpx

from .agent import Agent
from .config import Config, secret
from .formatting import incident_markup, incident_message, repair_markup, status_message, text_message
from .notifications import Notifications
from .redact import redact_text
from .selection import refresh_selection
from .storage import Store


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


class TelegramError(RuntimeError):
    def __init__(self, message: str, code: int = 0, retry_after: float = 0):
        super().__init__(message)
        self.code, self.retry_after = code, retry_after


def chunks(message: str) -> list[str]:
    parser = PlainText()
    parser.feed(message)
    plain = "".join(parser.parts)
    if len(plain.encode("utf-16-le")) // 2 <= 3800:
        return [message]
    # Long replies fall back to escaped text so tags/entities never break mid-chunk.
    return [escape(plain[i : i + 1800]) for i in range(0, len(plain), 1800)]


class Telegram:
    def __init__(self, config: Config, store: Store, client: httpx.AsyncClient, agent: Agent):
        self.config, self.store, self.client, self.agent = config, store, client, agent
        self.token = secret(config.telegram.token_env)
        self.chat = secret(config.telegram.chat_id_env)
        self.users = {int(value.strip()) for value in secret(config.telegram.allowed_user_ids_env).split(",")}
        self.delivery_lock = asyncio.Lock()
        if not self.users or not self.chat.lstrip("-").isdigit():
            raise ValueError("Telegram requires a numeric chat ID and a numeric user allowlist")

    async def api(self, method: str, payload: dict, timeout: int = 40):
        # Never include the request URL in exceptions: Telegram tokens appear in its path.
        try:
            response = await self.client.post(
                f"https://api.telegram.org/bot{self.token}/{method}", json=payload, timeout=timeout
            )
            result = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise RuntimeError(f"Telegram {method} transport failed: {type(error).__name__}") from None
        if not result.get("ok"):
            raise TelegramError(
                "Telegram rejected " + method + ": " + redact_text(result.get("description", "error")),
                code=result.get("error_code", response.status_code),
                retry_after=result.get("parameters", {}).get("retry_after", 0),
            )
        return result["result"]

    async def send(self, message: str, reply_markup: dict | None = None):
        for part in chunks(message):
            payload = {
                "chat_id": self.chat,
                "text": part,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup
            try:
                await self.api("sendMessage", payload)
            except TelegramError as error:
                if error.code != 400 or "parse entities" not in str(error):
                    raise
                parser = PlainText()
                parser.feed(part)
                payload["text"] = "".join(parser.parts)
                payload.pop("parse_mode", None)
                await self.api("sendMessage", payload)

    async def deliver(self):
        async with self.delivery_lock:
            if self.store.get("telegram_retry_at", 0) > time.time():
                return
            for row in self.store.pending():
                try:
                    item = json.loads(row["payload"])
                    refresh_selection(self.config)
                    target = next((t for t in self.config.targets if t.id == item["target"]), None)
                    if target and not target.enabled:
                        self.store.delivered(row["id"])
                        continue
                    if self.config.telegram.notifications == "important":
                        current = self.store.incident(item["ref"])
                        if not current or (
                            item.get("event") != "resolved" and current["status"] == "resolved"
                        ):
                            self.store.delivered(row["id"])
                            continue
                        item = {**current, "event": item.get("event")}
                        if not Notifications(self.config, self.store).eligible(item):
                            self.store.delivered(row["id"])
                            continue
                    await self.send(incident_message(item, self.config), incident_markup(item, self.config))
                except Exception as error:
                    delay = getattr(error, "retry_after", 0)
                    self.store.retry(row["id"], str(error), delay)
                    self.store.set(
                        "telegram_delivery_error",
                        {"time": time.time(), "error": redact_text(str(error), 500)},
                    )
                    if delay:
                        self.store.set("telegram_retry_at", time.time() + delay)
                        break
                    continue
                self.store.delivered(row["id"], item)
                self.store.set("telegram_last_delivery", time.time())

    async def poll(self, stopped: asyncio.Event):
        info = await self.api("getWebhookInfo", {})
        if info.get("url"):
            raise RuntimeError("This bot has an active webhook. Use a dedicated bot or telegram.mode=hermes.")
        await self.api(
            "setMyCommands",
            {
                "commands": [
                    {"command": "status", "description": "Application and monitor status"},
                    {"command": "incidents", "description": "Open incidents"},
                    {"command": "incident", "description": "Evidence for an incident ID"},
                    {"command": "diagnose", "description": "Agent fix suggestion for an incident"},
                    {"command": "fix", "description": "Repair in a worktree and prepare a draft PR"},
                    {"command": "repairs", "description": "Repair jobs and PRs"},
                    {"command": "repair", "description": "Details for a repair job"},
                    {"command": "cancelfix", "description": "Cancel a repair before publishing"},
                    {"command": "repos", "description": "Repositories available for repair"},
                    {"command": "queries", "description": "Slow query evidence"},
                    {"command": "ask", "description": "Ask Hermes about monitored applications"},
                    {"command": "model", "description": "Show or change the diagnosis model"},
                    {"command": "help", "description": "Command reference"},
                ]
            },
        )
        while not stopped.is_set():
            try:
                updates = await self.api(
                    "getUpdates",
                    {
                        "offset": self.store.get("telegram_offset", 0),
                        "timeout": 25,
                        "allowed_updates": ["message"],
                    },
                )
                for update in updates:
                    message = update.get("message", {})
                    allowed = (
                        str(message.get("chat", {}).get("id")) == self.chat
                        and message.get("from", {}).get("id") in self.users
                    )
                    if allowed and time.time() - message.get("date", 0) < 300:
                        await self.command(message.get("text", ""), str(message["from"]["id"]))
                    self.store.set("telegram_offset", update["update_id"] + 1)
            except Exception as error:
                self.store.set("telegram_error", {"time": time.time(), "error": redact_text(str(error), 500)})
                try:
                    await asyncio.wait_for(stopped.wait(), 5)
                except TimeoutError:
                    pass

    async def command(self, text: str, user: str):
        refresh_selection(self.config)
        command, _, argument = text.strip().partition(" ")
        command = command.split("@")[0].lower()
        if command in {"/fix", "/repairs", "/repair", "/cancelfix", "/repos"}:
            from .repairs import Repairs, repair_message

            jobs = Repairs(self.config, self.store)
            markup = None
            try:
                if command == "/fix":
                    parts = argument.split()
                    if not 1 <= len(parts) <= 2:
                        raise ValueError("Use /fix OW-0001 or /fix OW-0001 REPOSITORY_ID. See /repos.")
                    job = jobs.enqueue(parts[0], user, parts[1] if len(parts) == 2 else None)
                    reply = repair_message(job)
                    markup = repair_markup(job)
                    if time.time() - self.store.get("repair_heartbeat", 0) > 30:
                        reply += "\n\nRepair worker is offline; this request is saved and waiting."
                elif command == "/repos":
                    reply = "\n".join(f"{r.id} · {r.github}" for r in self.config.repair.repositories)
                    reply = reply or "No repair repositories configured."
                elif command == "/repairs":
                    entries = jobs.list()
                    markup = (
                        {
                            "inline_keyboard": [
                                [
                                    {
                                        "text": f"{j['id']} · {j['status']}",
                                        "callback_data": f"ow:repair:{j['id']}",
                                    }
                                ]
                                for j in entries[:20]
                            ]
                        }
                        if entries
                        else None
                    )
                    reply = "\n".join(f"{j['id']} · {j['incident']} · {j['status']}" for j in entries)
                    reply = reply or "No repair jobs yet. Start with /fix OW-0001."
                elif command == "/cancelfix":
                    job = jobs.cancel(argument.strip())
                    reply = repair_message(job)
                    markup = repair_markup(job)
                else:
                    job = jobs.get(argument.strip())
                    markup = repair_markup(job) if job else None
                    reply = repair_message(job) if job else "Use /repair RF-0001; find IDs with /repairs."
            except ValueError as error:
                reply = str(error)
            await self.send(text_message(reply), markup)
        elif command == "/status":
            await self.send(status_message(self.store.checks(), self.config, self.store.get("heartbeat")))
        elif command == "/incidents":
            rows = self.store.incidents()
            lines = [f"{r['ref']} · {r['target']} · {r['title']}" for r in rows]
            await self.send(
                text_message("Open incidents\n" + ("\n".join(lines) or "No open incidents.")),
                {
                    "inline_keyboard": [
                        [{"text": f"{r['ref']} · {r['target']}", "callback_data": f"ow:incident:{r['ref']}"}]
                        for r in rows[:30]
                    ]
                }
                if rows
                else None,
            )
        elif command in {"/incident", "/diagnose"}:
            item = self.store.incident(argument.strip())
            if not item:
                await self.send("Use an incident ID, for example <code>/incident OW-0001</code>.")
            elif command == "/incident":
                await self.send(incident_message(item, self.config), incident_markup(item, self.config))
            else:
                await self.send(f"Reviewing <code>{item['ref']}</code> with {escape(self.agent.model())}.")
                reply = await self.agent.diagnose("Suggest a fix for this incident.", item, user)
                await self.send(text_message(reply), incident_markup(item, self.config))
        elif command == "/queries":
            rows = [r for r in self.store.checks() if r["kind"] == "query performance" and r["healthy"] == 0]
            await self.send(
                text_message(
                    json.dumps(rows[:8], indent=2)
                    if rows
                    else "No measured slow queries. Enable a Postgres target; "
                    "the first poll establishes a baseline."
                )
            )
        elif command == "/model":
            try:
                reply = (
                    self.agent.set_model(argument.strip())
                    if argument.strip()
                    else f"Backend: {self.config.agent.backend}\nProvider: {self.config.agent.provider}\n"
                    f"Model: {self.agent.model()}\n"
                    "Change with /model MODEL_ID. No automatic fallback to an older model."
                )
            except ValueError as error:
                reply = str(error)
            await self.send(text_message(reply))
        elif command == "/ask" or (text and not text.startswith("/")):
            question = argument if command == "/ask" else text
            await self.send("Reviewing the recorded monitor evidence.")
            reply = await self.agent.diagnose(
                question, {"incidents": self.store.incidents(8), "checks": self.store.checks()[:30]}, user
            )
            await self.send(text_message(reply))
        else:
            await self.send(
                "<b>Opswatch</b>\n/status · /incidents · /queries\n"
                "/incident OW-0001\n/diagnose OW-0001\n/ask Why is my app slow?\n/model MODEL_ID\n\n"
                "Fixes are suggestions. Scans are blocked only when your policy and firewall allow it."
            )
