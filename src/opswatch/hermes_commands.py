"""Owner-only command bridge for the existing Hermes Telegram gateway."""

from __future__ import annotations

import argparse
import asyncio
import os
import re

import httpx

from .agent import Agent
from .config import Config, load_config
from .storage import Store
from .telegram import Telegram

COMMANDS = {
    "opsstatus": "status",
    "incidents": "incidents",
    "incident": "incident",
    "diagnose": "diagnose",
    "queries": "queries",
    "opsmodel": "model",
    "opshelp": "help",
    "opsask": "ask",
    "fix": "fix",
    "repairs": "repairs",
    "repair": "repair",
    "cancelfix": "cancelfix",
    "repos": "repos",
}


async def handle(
    config: Config,
    store: Store,
    client: httpx.AsyncClient,
    command: str,
    argument: str,
    user: str,
) -> bool:
    if not config.telegram.enabled or config.telegram.mode != "hermes" or command not in COMMANDS:
        return False
    telegram = Telegram(config, store, client, Agent(config, store))
    if not user.isdigit() or int(user) not in telegram.users:
        return False
    if command == "opshelp":
        await telegram.send(
            "<b>Opswatch commands</b>\n"
            "/opsstatus · /incidents · /queries\n"
            "/incident OW-0001 — show evidence\n"
            "/diagnose OW-0001 — suggest a fix\n"
            "/fix OW-0001 — repair in a worktree and prepare a draft PR\n"
            "/repairs · /repair RF-0001 · /cancelfix RF-0001\n"
            "/repos — configured GitHub repositories\n"
            "/opsask Why is my app slow?\n"
            "/opsmodel — diagnosis model\n\n"
            "Use /incidents to find an incident ID. Hermes /status and /model describe the gateway."
        )
    elif command in {"incident", "diagnose"} and not store.incident(argument.strip()):
        await telegram.send(
            f"Use an incident ID: <code>/{command} OW-0001</code>.\nSend /incidents to see the available IDs."
        )
    else:
        await telegram.command(f"/{COMMANDS[command]} {argument}".strip(), user)
    return True


async def callback(config, store, client, data, user, chat, query_id):
    """Only fixed actions from the configured owner chat can invoke commands."""
    telegram = Telegram(config, store, client, Agent(config, store))
    match = re.fullmatch(
        r"ow:(incident|diagnose|fix):(OW-[0-9]{4,10})|ow:(repair|cancelfix):(RF-[0-9]{4,10})", data
    )
    allowed = (
        config.telegram.enabled
        and config.telegram.mode == "hermes"
        and user.isdigit()
        and int(user) in telegram.users
        and chat == telegram.chat
        and match is not None
    )
    await telegram.api(
        "answerCallbackQuery",
        {
            "callback_query_id": query_id,
            "text": "Working on it…" if allowed else "This button is unavailable in this chat.",
            "show_alert": not allowed,
        },
    )
    if not allowed:
        return False
    command, ref = (match[1], match[2]) if match[1] else (match[3], match[4])
    try:
        await telegram.command(f"/{command} {ref}", user)
    except Exception:
        await telegram.send("That action failed. Use /opshelp or try again shortly.")
        raise
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--callback-id", default="")
    parser.add_argument("command", choices=[*COMMANDS, "callback"])
    parser.add_argument("argument")
    parser.add_argument("user")
    args = parser.parse_args()
    os.umask(0o077)
    config = load_config(args.config)
    store = Store(config.data_dir)

    async def execute():
        async with httpx.AsyncClient() as client:
            if args.command == "callback":
                return await callback(
                    config, store, client, args.argument, args.user, args.chat_id, args.callback_id
                )
            return await handle(config, store, client, args.command, args.argument, args.user)

    try:
        if not asyncio.run(execute()):
            raise SystemExit("Opswatch command denied: owner-only Telegram commands.")
    finally:
        store.close()


if __name__ == "__main__":
    main()
