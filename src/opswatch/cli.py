from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

import httpx
import yaml

from .agent import Agent
from .config import Config, load_config
from .connectors import discover
from .formatting import incident_message, status_message
from .redact import redact_text
from .service import Service
from .storage import Store
from .telegram import PlainText
from .types import Observation


def plain(message: str) -> str:
    parser = PlainText()
    parser.feed(message)
    return "".join(parser.parts)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Monitor your development stack and ask Hermes for incident fixes."
    )
    root.add_argument("--config", default="config.local.yaml", help="Path to the local YAML configuration")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create a local configuration without credentials")
    find = commands.add_parser("discover", help="Read project inventories through signed-in CLIs")
    find.add_argument(
        "--providers",
        nargs="+",
        choices=["railway", "vercel", "sentry", "neon"],
        default=["railway", "vercel", "sentry", "neon"],
    )
    commands.add_parser("check", help="Collect once without sending messages or changing firewalls")
    commands.add_parser("run", help="Run continuous monitoring and the configured Telegram mode")
    commands.add_parser("repair-worker", help="Process manually queued repair jobs")
    commands.add_parser("cloud-bridge", help="Synchronize the private cloud dashboard from this computer")
    commands.add_parser(
        "install-cloud-service", help="Install the separate outbound dashboard bridge service"
    )
    repair = commands.add_parser("fix", help="Queue a worktree repair and draft PR for an incident")
    repair.add_argument("id")
    repair.add_argument("--repo", help="Explicit configured repository ID for an unmapped target")
    commands.add_parser("repairs", help="List repair jobs")
    commands.add_parser("install-repair-service", help="Install the separate repair worker service")
    commands.add_parser("status", help="Show the most recent recorded status")
    dashboard = commands.add_parser("dashboard", help="Open the local monitoring and repair dashboard")
    dashboard.add_argument("--port", type=int, default=8767, help="Local dashboard port, default 8767")
    commands.add_parser("incidents", help="List open incidents")
    incident = commands.add_parser("incident", help="Show an incident and its evidence")
    incident.add_argument("id")
    diagnose = commands.add_parser("diagnose", help="Get an agent diagnosis for a recorded incident")
    diagnose.add_argument("id")
    model = commands.add_parser("model", help="Show or change the model used for future diagnoses")
    model.add_argument("id", nargs="?")
    commands.add_parser(
        "doctor", help="Check local tools and required secret presence without showing values"
    )
    commands.add_parser("demo", help="Print sample alerts without contacting services or Telegram")
    commands.add_parser("mcp", help="Expose redacted monitor state over read-only stdio MCP")
    commands.add_parser("install-service", help="Install a Linux systemd user service without starting it")
    commands.add_parser("hermes-config", help="Write an isolated Hermes gateway configuration example")
    return root


def write_private(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o600)


async def execute(args, config: Config, store: Store):
    if args.command == "cloud-bridge":
        from .cloud_bridge import CloudBridge

        await CloudBridge(config, store).serve()
        return
    if args.command == "install-cloud-service":
        cfg = Path(args.config).resolve()
        binary = Path(sys.executable).parent / "opswatch"
        unit = Path.home() / ".config/systemd/user/opswatch-cloud-bridge.service"
        if unit.exists():
            raise ValueError(f"service already exists: {unit}")

        def quote(value):
            return '"' + str(value).replace("\\", "\\\\").replace("%", "%%").replace('"', '\\"') + '"'

        command_path = str(Path(sys.executable).parent) + ":" + os.environ.get("PATH", "/usr/bin:/bin")
        write_private(
            unit,
            f"""[Unit]
Description=Opswatch outbound cloud dashboard bridge
After=network-online.target

[Service]
Type=simple
WorkingDirectory={str(cfg.parent).replace("%", "%%")}
ExecStart={quote(binary)} --config {quote(cfg)} cloud-bridge
Environment={quote("PATH=" + command_path)}
Restart=on-failure
RestartSec=10
KillMode=control-group
TimeoutStopSec=30
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
""",
        )
        print(f"Installed {unit}")
        return
    agent = Agent(config, store)
    if args.command in {"repair-worker", "fix", "repairs"}:
        from .repairs import Repairs, RepairWorker, repair_message

        if args.command == "repair-worker":
            await RepairWorker(config, store).serve()
        elif args.command == "fix":
            print(repair_message(Repairs(config, store).enqueue(args.id, "local-cli", args.repo)))
        else:
            jobs = Repairs(config, store).list()
            print("\n\n".join(repair_message(j) for j in jobs) or "No repair jobs.")
    elif args.command == "install-repair-service":
        cfg = Path(args.config).resolve()
        binary = Path(sys.executable).parent / "opswatch"
        unit = Path.home() / ".config/systemd/user/opswatch-repairs.service"
        if unit.exists():
            raise ValueError(f"service already exists: {unit}")

        def quote(value):
            return '"' + str(value).replace("%", "%%").replace('"', '\\"') + '"'

        write_private(
            unit,
            f"""[Unit]
Description=Opswatch manual repair worker
After=network-online.target

[Service]
Type=simple
WorkingDirectory={str(cfg.parent).replace("%", "%%")}
ExecStart={quote(binary)} --config {quote(cfg)} repair-worker
Environment=PATH={Path(sys.executable).parent}:{os.environ.get("PATH", "/usr/bin:/bin")}
Restart=on-failure
RestartSec=10
KillMode=control-group
TimeoutStopSec=20
UMask=0077

[Install]
WantedBy=default.target
""",
        )
        print(f"Installed {unit}")
    elif args.command == "discover":
        targets, inventory = await discover(config, args.providers)
        existing = {t.id: t for t in config.targets}
        added = 0
        for target in targets:
            if target.id not in existing:
                existing[target.id] = target
                added += 1
        config.targets = list(existing.values())
        document = config.model_dump(exclude_defaults=True)
        # Store portable paths relative to the config directory.
        document["data_dir"] = os.path.relpath(config.data_dir, Path(args.config).resolve().parent)
        write_private(Path(args.config), yaml.safe_dump(document, sort_keys=False, allow_unicode=True))
        write_private(
            Path(args.config).resolve().parent / "inventory.local.json", json.dumps(inventory, indent=2)
        )
        print(f"Added {added} targets. Existing target settings preserved.")
        for provider, rows in inventory["providers"].items():
            print(f"{provider}: {len(rows)} projects")
        for provider, error in inventory["errors"].items():
            print(f"{provider}: {error}", file=sys.stderr)
        print("Neon targets start disabled until monitoring DSNs are set. Platform log checks are enabled.")
    elif args.command in {"run", "check"}:
        if args.command == "check":
            config = config.model_copy(deep=True)
            config.telegram.enabled = False
            config.policy.dry_run = True
            config.agent.auto_diagnose = False
        async with httpx.AsyncClient(limits=httpx.Limits(max_connections=20)) as client:
            service = Service(config, store, client)
            if args.command == "run":
                await service.run()
            else:
                await service.cycle(all_targets=True)
                print(plain(status_message(store.checks(), config, store.get("heartbeat"))))
    elif args.command == "status":
        print(plain(status_message(store.checks(), config, store.get("heartbeat"))))
    elif args.command == "incidents":
        rows = store.incidents()
        for row in rows:
            print(f"{row['ref']}  {row['severity']:8}  {row['target']}  {row['title']}")
        if not rows:
            print("No open incidents.")
    elif args.command in {"incident", "diagnose"}:
        item = store.incident(args.id)
        if not item:
            raise ValueError("incident not found")
        if args.command == "diagnose":
            print(await agent.diagnose("Suggest a fix for this incident.", item))
        else:
            print(plain(incident_message(item, config)))
    elif args.command == "model":
        print(agent.set_model(args.id) if args.id else agent.model())
    elif args.command == "doctor":
        for name in ("hermes", "codex", "railway", "vercel", "sentry", "neon", "ssh"):
            print(f"{name:10} {'installed' if shutil.which(name) else 'missing'}")
        names = set()
        if config.telegram.enabled:
            names.update(
                (config.telegram.token_env, config.telegram.chat_id_env, config.telegram.allowed_user_ids_env)
            )
        for account in [*config.railway_accounts, *config.vercel_accounts]:
            if account.token_env:
                names.add(account.token_env)
        for target in config.targets:
            if target.enabled and target.dsn_env:
                names.add(target.dsn_env)
        for name in sorted(names):
            print(f"{name}: {'set' if os.environ.get(name) else 'missing'}")
        print(f"Model: {agent.model()} via {config.agent.backend}")
        print(f"Telegram: {'enabled' if config.telegram.enabled else 'disabled'} / {config.telegram.mode}")
        print(f"Automatic blocking: {config.policy.auto_block}; dry run: {config.policy.dry_run}")
    elif args.command == "install-service":
        binary = Path(sys.executable).parent / "opswatch"
        cfg = Path(args.config).resolve()
        unit = Path.home() / ".config/systemd/user/opswatch.service"

        # systemd expands % specifiers even inside quoted strings.
        def quoted(value):
            return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'

        body = f"""[Unit]
Description=Opswatch development operations monitor
After=network-online.target

[Service]
Type=simple
WorkingDirectory={str(cfg.parent).replace("%", "%%")}
ExecStart={quoted(binary)} --config {quoted(cfg)} run
Environment=PATH={Path(sys.executable).parent}:{os.environ.get("PATH", "/usr/bin:/bin")}
Restart=on-failure
RestartSec=15
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
"""
        if unit.exists():
            raise ValueError(f"service already exists: {unit}; review before replacing it")
        write_private(unit, body)
        print(
            f"Installed {unit}\nStart with: systemctl --user daemon-reload\n"
            "systemctl --user enable --now opswatch"
        )
    elif args.command == "hermes-config":
        home = Path(config.data_dir) / "hermes-gateway"
        profile = {
            "model": {"default": agent.model(), "provider": config.agent.provider},
            "agent": {"reasoning_effort": config.agent.reasoning, "max_turns": 8},
            "platform_toolsets": {"cli": ["opswatch"], "telegram": ["opswatch"]},
            "known_builtin_toolsets": {"cli": ["bfl"], "telegram": ["bfl"]},
            "mcp_servers": {
                "opswatch": {
                    "command": str(Path(sys.executable).parent / "opswatch"),
                    "args": ["--config", str(Path(args.config).resolve()), "mcp"],
                }
            },
            "memory": {"memory_enabled": False, "user_profile_enabled": False},
        }
        destination = home / "config.yaml"
        if destination.exists():
            raise ValueError("Hermes gateway config already exists; edit it to preserve your changes")
        write_private(destination, yaml.safe_dump(profile, sort_keys=False))
        print(
            f"Wrote {destination}\nSet telegram.mode: hermes before starting this gateway.\n"
            f"HERMES_HOME={home} hermes gateway run"
        )


def demo():
    with tempfile.TemporaryDirectory() as directory:
        config = Config(data_dir=directory, failures_before_alert=1)
        store = Store(directory)
        items = [
            Observation(
                "sms",
                "http",
                "availability",
                False,
                "Application health check failed",
                {"http_status": 503, "latency_ms": 2300, "consecutive_failures": 3},
                "critical",
                True,
            ),
            Observation(
                "sms",
                "scan",
                "security",
                False,
                "Repeated vulnerability probes detected",
                {
                    "client_ip": "203.0.113.42",
                    "requests": 18,
                    "paths": ["/.env", "/.git/config", "/wp-login.php"],
                },
                "critical",
                True,
                "Demo only. Would block at the configured firewall for 15 minutes.",
            ),
            Observation(
                "neon",
                "query",
                "query performance",
                False,
                "Query exceeds interval average threshold",
                {
                    "query": "SELECT * FROM orders WHERE customer_id = $1",
                    "interval_mean_ms": 850,
                    "interval_calls": 92,
                    "threshold_ms": 200,
                },
                "warning",
                True,
            ),
        ]
        for item in items:
            incident = store.observe(item, config)
            print(plain(incident_message(incident, config)) + "\n")
        store.close()


def main():
    args = parser().parse_args()
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if args.command == "demo":
        demo()
        return
    if args.command == "init":
        path = Path(args.config)
        if path.exists():
            raise SystemExit("Configuration already exists. Existing settings were preserved.")
        write_private(path, yaml.safe_dump(Config().model_dump(), sort_keys=False))
        print(f"Created {path}. Run opswatch --config {path} discover.")
        return
    store = None
    try:
        config = load_config(args.config)
        if args.command == "dashboard":
            import uvicorn

            from .dashboard import application

            if not 1024 <= args.port <= 65535:
                raise ValueError("dashboard port must be between 1024 and 65535")
            print(f"OpsWatch dashboard: http://127.0.0.1:{args.port}", flush=True)
            uvicorn.run(application(config), host="127.0.0.1", port=args.port, access_log=False)
            return
        if args.command == "mcp":
            from .mcp_server import serve

            serve(config)
            return
        store = Store(config.data_dir)
        asyncio.run(execute(args, config, store))
    except (Exception, KeyboardInterrupt) as error:
        raise SystemExit(redact_text(str(error), 1000) or "Stopped") from None
    finally:
        if store:
            store.close()
