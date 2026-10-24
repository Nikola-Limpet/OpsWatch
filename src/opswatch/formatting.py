from __future__ import annotations

import html
import os
import re
import time
from datetime import datetime
from urllib.parse import quote, urlencode, urlsplit
from zoneinfo import ZoneInfo

from .config import Config
from .redact import redact_text


def stamp(timestamp: float, timezone: str) -> str:
    return datetime.fromtimestamp(timestamp, ZoneInfo(timezone)).strftime("%d %b %H:%M:%S %Z")


def short(value, limit: int = 200) -> str:
    text = redact_text(str(value), max(limit * 2, 1200))
    data = text.encode("utf-16-le")
    return text if len(data) <= limit * 2 else data[: limit * 2].decode("utf-16-le", errors="ignore") + "…"


def evidence_text(evidence: dict) -> str:
    lines = []
    status = evidence.get("http_status", evidence.get("status"))
    if isinstance(status, (int, float)) and status:
        latency = evidence.get("latency_ms")
        lines.append(f"HTTP {status}" + (f" · {latency:g} ms" if isinstance(latency, (int, float)) else ""))
    elif status:
        lines.append("Deployment: " + short(status))
    if evidence.get("state"):
        lines.append("Deployment: " + short(evidence["state"]))
    if evidence.get("mitigation") == "challenge":
        lines.append("Security challenge · application health unknown")
    latency = evidence.get("latency_ms")
    if not isinstance(status, (int, float)) and isinstance(latency, (int, float)):
        lines.append(f"Latency: {latency:g} ms")
    for key, label, limit in [
        ("path", "Path", 180),
        ("level", "Level", 40),
        ("message", "Error", 700),
        ("error", "Error", 700),
        ("culprit", "Location", 180),
        ("shortId", "Sentry", 80),
        ("count", "Reported events", 40),
        ("lastSeen", "Last seen", 80),
        ("interval_mean_ms", "Query average (ms)", 40),
        ("interval_calls", "Calls in interval", 40),
        ("threshold_ms", "Threshold (ms)", 40),
        ("query", "Query", 360),
        ("client_ip", "Client", 140),
        ("requests", "Probe requests", 40),
    ]:
        if evidence.get(key) is not None and evidence.get(key) != "":
            lines.append(label + ": " + short(evidence[key], limit))
    if evidence.get("distinct_probe_paths"):
        lines.append("Probe paths: " + short(", ".join(evidence["distinct_probe_paths"]), 200))
    if not lines:
        lines = [
            f"{key.replace('_', ' ').capitalize()}: {short(value, 200)}"
            for key, value in list(evidence.items())[:6]
        ]
    return short("\n".join(lines) or "See the incident record for details.", 1500)


def dashboard_link(config: Config, path: str) -> str | None:
    base = config.telegram.dashboard_url or os.environ.get("OPSWATCH_RELAY_URL", "")
    try:
        parsed = urlsplit(base)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or any(char.isspace() for char in base)
        ):
            return None
        return base.rstrip("/") + path
    except ValueError:
        return None


def next_step_text(value: str | None) -> str:
    labels = {
        "diagnose": "Start an agent investigation",
        "fix": "Start a code repair",
        "incident": "Review incident evidence",
    }
    return re.sub(
        r"(?:Use|Run|Send)\s+/(diagnose|fix|incident)\b(?:\s+OW-[0-9]+)?",
        lambda match: labels[match[1].lower()],
        value or "Review the recorded evidence.",
        flags=re.I,
    ).strip()


def incident_markup(item: dict, config: Config) -> dict | None:
    target = next((t for t in config.targets if t.id == item["target"]), None)
    buttons = []
    if target and target.url:
        parsed = urlsplit(target.url)
        if parsed.scheme == "https" and not parsed.username and not parsed.password:
            buttons.append(
                {"text": "Open app", "url": parsed._replace(path="", query="", fragment="").geturl()}
            )
    issue = item["evidence"].get("permalink")
    if issue:
        parsed = urlsplit(str(issue))
        if (
            parsed.scheme == "https"
            and (parsed.hostname or "").endswith(".sentry.io")
            and not parsed.username
            and not parsed.password
        ):
            buttons.append({"text": "Open issue", "url": str(issue)})
    if target and target.kind == "railway":
        url = (
            f"https://railway.com/project/{quote(target.project, safe='')}"
            f"/service/{quote(target.service, safe='')}"
        )
        buttons.append(
            {"text": "Open service", "url": url + "?" + urlencode({"environmentId": target.environment})}
        )
    elif target and target.kind == "vercel":
        scope = config.account_for(target).scope
        if scope:
            buttons.append(
                {
                    "text": "Open project",
                    "url": f"https://vercel.com/{quote(scope, safe='')}/{quote(target.project, safe='')}",
                }
            )
    rows = [buttons[:3]] if buttons else []
    url = dashboard_link(config, "/incidents?" + urlencode({"ref": item["ref"]}))
    if url:
        rows.insert(0, [{"text": "Open incident", "url": url}])
    actions = []
    if config.telegram.interactive_controls:
        actions = [
            {"text": "Evidence", "callback_data": f"ow:incident:{item['ref']}"},
            {"text": "Diagnose", "callback_data": f"ow:diagnose:{item['ref']}"},
        ]
        if (
            config.repair.enabled
            and target
            and target.enabled
            and target.repair_repo
            and item["kind"] not in {"monitoring", "security"}
            and item.get("status") != "resolved"
            and item.get("event") != "resolved"
        ):
            actions.append({"text": "Start fix", "callback_data": f"ow:fix:{item['ref']}"})
    if actions:
        rows.append(actions)
    return {"inline_keyboard": rows} if rows else None


def incident_message(item: dict, config: Config) -> str:
    resolved = item.get("event") == "resolved" or item["status"] == "resolved"
    quiet = resolved and item["kind"] in {"application error", "security"}
    badge = "QUIET" if quiet else "RECOVERED" if resolved else item["severity"].upper()
    icon = "⚪" if quiet else "🟢" if resolved else "🔴" if item["severity"] == "critical" else "🟡"
    names = {t.id: t.name for t in config.targets}
    name = names.get(item["target"], item["target"])
    title = (
        "No matching events in the quiet period"
        if quiet
        else "Check passed again"
        if resolved
        else item["title"]
    )
    evidence = evidence_text(item["evidence"])
    action = (
        "Observation stopped. This does not confirm a code fix."
        if quiet
        else "Recovery confirmed by repeated checks"
        if resolved
        else item["action"]
    )
    target = next((t for t in config.targets if t.id == item["target"]), None)
    environment = (target.environment_name or target.environment) if target else ""
    if len(environment) > 30:
        environment = ""
    context = (environment.capitalize() + " · ") if environment else ""
    context += f"{target.kind.capitalize()} · " if target else ""
    repeat = f" · {item['occurrences']} observations" if item.get("occurrences", 1) > 1 else ""
    action_text = (
        "" if action == "No action taken" else f"<b>Action</b>\n{html.escape(short(action, 220))}\n\n"
    )
    return (
        f"{icon} <b>{badge} · {html.escape(short(name, 100))}</b>\n"
        f"<b>{html.escape(short(title, 180))}</b>\n"
        f"{html.escape(context)}{html.escape(stamp(item['updated'], config.timezone))}\n\n"
        f"<b>Evidence</b>\n<pre>{html.escape(evidence)}</pre>\n"
        f"{action_text}"
        f"<b>Next</b>\n{html.escape(short(next_step_text(item['next_step']), 450))}\n\n"
        f"<code>{item['ref']}</code>{html.escape(repeat)}"
    )


def status_message(checks: list[dict], config: Config, heartbeat: float | None = None) -> str:
    groups: dict[str, list] = {}
    for check in checks:
        groups.setdefault(check["target"], []).append(check)
    lines = ["<b>Opswatch status</b>", html.escape(stamp(time.time(), config.timezone))]
    enabled = [t for t in config.targets if t.enabled]
    for target in enabled:
        rows = groups.get(target.id, [])
        fresh = [r for r in rows if time.time() - r["checked"] <= target.interval_seconds * 3 + 30]
        if not fresh:
            state = "STALE" if rows else "WAITING"
        elif any(r["healthy"] == 0 for r in fresh) or any(
            r["healthy"] == 0 and r["kind"] in {"application error", "security"} for r in rows
        ):
            state = "ATTENTION"
        elif any(r["healthy"] is None for r in fresh):
            state = "UNKNOWN"
        else:
            state = "OK"
        lines.append(f"<b>{state}</b> · {html.escape(target.name)}")
    if not enabled:
        lines.append("No enabled targets. Run opswatch discover.")
    if heartbeat:
        lines.append(f"\nLast monitor cycle {html.escape(stamp(heartbeat, config.timezone))}")
    lines.append(
        "\nAsk Hermes for Opswatch status, incident evidence, or slow queries."
        if config.telegram.mode == "hermes"
        else "\n/incident OW-0001 · /incidents · /queries · /model"
    )
    return "\n".join(lines)


def text_message(text: str) -> str:
    return html.escape(redact_text(text, 3000))


def repair_markup(job: dict, config: Config | None = None) -> dict | None:
    rows = []
    if config is None or config.telegram.interactive_controls:
        actions = [{"text": "Refresh progress", "callback_data": f"ow:repair:{job['id']}"}]
        if job["status"] in {"queued", "preparing", "coding", "validating"}:
            actions.append({"text": "Cancel fix", "callback_data": f"ow:cancelfix:{job['id']}"})
        rows = [actions, [{"text": "Incident evidence", "callback_data": f"ow:incident:{job['incident']}"}]]
    if config and (dashboard := dashboard_link(config, "/agents")):
        rows.insert(0, [{"text": "Open agent work", "url": dashboard}])
    url = str(job.get("pr_url", ""))
    parsed = urlsplit(url)
    if parsed.scheme == "https" and parsed.netloc == "github.com" and "/pull/" in parsed.path:
        rows.insert(0, [{"text": "Review draft PR", "url": url}])
    return {"inline_keyboard": rows} if rows else None
