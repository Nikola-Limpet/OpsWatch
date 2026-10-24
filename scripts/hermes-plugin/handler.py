"""Hermes plugin: route registered commands to Opswatch's private owner chat."""

import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = {
    "opsstatus": ("Application and monitor status", ""),
    "incidents": ("Open Opswatch incidents", ""),
    "incident": ("Incident evidence", "OW-0001"),
    "diagnose": ("Agent fix suggestion", "OW-0001"),
    "queries": ("Measured slow database queries", ""),
    "opsmodel": ("Show or change diagnosis model", "MODEL_ID"),
    "opshelp": ("Opswatch command reference", ""),
    "opsask": ("Ask about monitor evidence", "QUESTION"),
    "fix": ("Repair an incident and prepare a draft PR", "OW-0001"),
    "repairs": ("Repair job progress and PRs", ""),
    "repair": ("Repair job details", "RF-0001"),
    "cancelfix": ("Cancel a queued or running repair", "RF-0001"),
    "repos": ("Repositories available for repairs", ""),
}


async def handle(event_type, context):
    command = context.get("command", "")
    if command not in COMMANDS:
        return None
    if context.get("platform") != "telegram":
        return {"decision": "deny", "message": "Use Opswatch commands in the owner's Telegram bot."}
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            str(ROOT / ".venv/bin/python3"),
            "-m",
            "opswatch.hermes_commands",
            "--config",
            str(ROOT / "config.local.yaml"),
            command,
            str(context.get("raw_args", "")),
            str(context.get("user_id", "")),
            cwd=str(ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(process.communicate(), 960)
        if process.returncode:
            return {
                "decision": "deny",
                "message": "Opswatch could not handle that command. "
                "Check the owner allowlist and monitor service.",
            }
        return {"decision": "handled"}
    except Exception:
        return {
            "decision": "deny",
            "message": "Opswatch command failed. The monitor continues independently.",
        }
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()


# Installed by the plugin factory before Telegram connects and binds its handlers.
# Keep this extension here instead of modifying Hermes's upstream source files.
_CALLBACK_TASKS = set()


def install_callbacks(adapter_class):
    original = adapter_class._handle_callback_query
    if getattr(original, "_opswatch_buttons", False) is True:
        return

    async def route(self, update, context):
        query = update.callback_query
        if query and str(query.data or "").startswith("ow:"):
            task = asyncio.create_task(dispatch_callback(query))
            _CALLBACK_TASKS.add(task)
            task.add_done_callback(_CALLBACK_TASKS.discard)
            return
        return await original(self, update, context)

    route._opswatch_buttons = True
    adapter_class._handle_callback_query = route


async def dispatch_callback(query):
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            str(ROOT / ".venv/bin/python3"),
            "-m",
            "opswatch.hermes_commands",
            "--config",
            str(ROOT / "config.local.yaml"),
            "--chat-id",
            str(getattr(query.message, "chat_id", "")),
            "--callback-id",
            str(query.id),
            "callback",
            str(query.data),
            str(getattr(query.from_user, "id", "")),
            cwd=str(ROOT),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(process.wait(), 960)
        if process.returncode:
            raise RuntimeError("Callback bridge failed")
    except Exception:
        try:
            await query.answer("Action unavailable. Try /opshelp.", show_alert=True)
        except Exception:
            pass
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
