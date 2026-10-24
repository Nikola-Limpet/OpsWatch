from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import httpx
import psycopg
from psycopg.rows import dict_row

from .config import Account, Config, Target, secret
from .process import parse_json, parse_json_lines, run
from .railway_auth import saved_account_token
from .redact import redact_text, scrub_sql
from .storage import Store
from .types import AccessEvent, Observation


def account_env(provider: str, account: Account) -> dict[str, str]:
    env = os.environ.copy()
    # Prevent a token exported for one account from silently overriding another.
    keys = ("RAILWAY_API_TOKEN", "RAILWAY_TOKEN") if provider == "railway" else ("VERCEL_TOKEN",)
    for key in keys:
        env.pop(key, None)
    if account.token_env:
        env[keys[0]] = secret(account.token_env)
    if provider == "railway":
        if account.cli_user_id and not account.token_env and not account.credentials_path:
            try:
                cli_user = json.loads((Path.home() / ".railway/config.json").read_text())["user"].get("id")
            except (OSError, ValueError, KeyError):
                cli_user = None
            if cli_user != account.cli_user_id:
                raise ValueError("Railway CLI is signed into a different account; reconnect this account")
        env["RAILWAY_CALLER"] = "skill:use-railway@1.3.7"
        env["RAILWAY_AGENT_SESSION"] = "opswatch-local"
    else:
        env["CI"] = "1"
    return env


async def railway_account_env(account: Account, client: httpx.AsyncClient | None = None) -> dict[str, str]:
    env = account_env("railway", account)
    if account.credentials_path:
        if client is None:
            async with httpx.AsyncClient() as connection:
                env["RAILWAY_API_TOKEN"] = await saved_account_token(account.credentials_path, connection)
        else:
            env["RAILWAY_API_TOKEN"] = await saved_account_token(account.credentials_path, client)
    return env


def scope_args(account: Account) -> list[str]:
    return ["--scope", account.scope] if account.scope else []


async def vercel_production_url(account: Account, project: dict) -> str | None:
    domains, cursor = [], None
    for _ in range(20):
        params = {"limit": 100}
        if cursor:
            params["until"] = cursor
        endpoint = f"/v9/projects/{quote(project['name'], safe='')}/domains?{urlencode(params)}"
        result = parse_json(
            await run(
                ["vercel", "api", endpoint, "--raw", *scope_args(account)],
                env=account_env("vercel", account),
                timeout=45,
            )
        )
        domains.extend(result["domains"])
        cursor = result.get("pagination", {}).get("next")
        if not cursor:
            break
    if cursor:
        raise ValueError("Vercel domain inventory exceeded 20 pages")
    project["domains"] = domains
    candidates = sorted(
        {
            domain["name"].lower().rstrip(".")
            for domain in domains
            if domain.get("verified") is True
            and not domain.get("gitBranch")
            and not domain.get("customEnvironmentId")
            and not domain.get("redirect")
            and "*" not in domain["name"]
            and not domain["name"].lower().rstrip(".").endswith(".vercel.app")
            and domain["name"].lower().rstrip(".") != "vercel.app"
        },
        key=lambda name: (name.count("."), name),
    )
    preferred = urlsplit(project.get("latestProductionUrl", "")).hostname
    host = preferred if preferred in candidates else candidates[0] if candidates else None
    return "https://" + host if host else None


def epoch(value) -> float:
    if isinstance(value, (float, int)):
        while value > 100_000_000_000:
            value /= 1000
        return float(value)
    if isinstance(value, str):
        if value.isdigit():
            return epoch(int(value))
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=UTC).timestamp() if parsed.tzinfo is None else parsed.timestamp()
        except ValueError:
            pass
    return 0


def parse_logs(
    target: Target,
    records: list[dict],
    store: Store,
    *,
    platform_http: bool = False,
    oldest: float | None = None,
) -> tuple[list[Observation], list[AccessEvent]]:
    observations, events = [], []
    flattened = []
    for record in records:
        nested_logs = record.get("logs")
        if isinstance(nested_logs, list) and nested_logs:
            flattened.extend({**record, **entry} for entry in nested_logs if isinstance(entry, dict))
        else:
            flattened.append(record)
    for record in flattened:
        message = str(record.get("message", record.get("MESSAGE", "")))
        nested = record
        if message.startswith("{"):
            try:
                nested = json.loads(message)
            except json.JSONDecodeError:
                pass
        if not isinstance(nested, dict):
            nested = record
        timestamp = epoch(record.get("timestamp", record.get("__REALTIME_TIMESTAMP")))
        timestamp = timestamp or epoch(nested.get("timestamp", nested.get("time")))
        estimated = not timestamp
        timestamp = timestamp or time.time()
        cutoff = oldest if oldest is not None else time.time() - max(600, target.interval_seconds * 3)
        if timestamp < cutoff:
            continue
        # Only platform-produced srcIp or an explicitly trusted normalized access log can block.
        ip = record.get("srcIp") if platform_http else nested.get("client_ip")
        path = record.get("path") if platform_http else nested.get("path", record.get("requestPath"))
        status = (
            record.get("httpStatus")
            if platform_http
            else nested.get("status", record.get("responseStatusCode", 0))
        )
        try:
            status = int(status or 0)
        except (TypeError, ValueError):
            status = 0
        fingerprint = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
        event_id = str(record.get("requestId", record.get("id", nested.get("event_id", fingerprint))))
        if path:
            events.append(
                AccessEvent(
                    target.id,
                    event_id,
                    timestamp,
                    str(ip) if ip else "",
                    str(path),
                    status,
                    bool(ip) and (platform_http or target.trusted_access_log),
                )
            )
        level = str(nested.get("level", record.get("level", ""))).lower()
        postgres_level = re.match(
            r"^\d{4}-\d{2}-\d{2} .*?\[\d+\]\s+(LOG|DEBUG\d?|NOTICE|WARNING|ERROR|FATAL|PANIC):",
            message,
        )
        if postgres_level:
            # Postgres writes routine LOG/checkpoint messages to stderr. Railway may
            # label that stream error even when the database reports normal activity.
            level = {"ERROR": "error", "FATAL": "fatal", "PANIC": "fatal"}.get(
                postgres_level.group(1), "info"
            )
        if level not in {"error", "fatal", "critical"} and status < 500:
            continue
        seen_key = f"logseen:{target.id}:{fingerprint}"
        if store.get(seen_key):
            continue
        normalized = re.sub(r"\b[0-9a-f-]{8,}\b|\d+", "?", message[:300])
        condition = json.dumps([normalized, urlsplit(str(path)).path if path else None, status, level])
        key = "log:" + hashlib.sha256(condition.encode()).hexdigest()[:16]
        observations.append(
            Observation(
                target.id,
                key,
                "application error",
                False,
                "Application error in recent logs",
                {
                    "timestamp": timestamp,
                    "timestamp_estimated": estimated,
                    "level": level,
                    "message": redact_text(message, 1200),
                    "path": urlsplit(str(path)).path if path else None,
                    "status": status,
                },
                "critical" if level in {"fatal", "critical"} else "warning",
                True,
                next_step="Inspect the stack trace and recent changes. Use /diagnose for a fix plan.",
                dedup_key=seen_key,
            )
        )
    return observations, events


class Collector:
    def __init__(self, config: Config, store: Store, client: httpx.AsyncClient):
        self.config, self.store, self.client = config, store, client

    def log_window(self, target: Target, key: str) -> tuple[float, float, str]:
        collected = time.time()
        previous = self.store.get(f"log-poll:{target.id}:{key}", collected - 600)
        oldest = max(collected - 86400, previous - 120)
        since = datetime.fromtimestamp(oldest, UTC).isoformat().replace("+00:00", "Z")
        return collected, oldest, since

    def log_result(self, target: Target, key: str, count: int, collected: float) -> Observation:
        capped = count >= 1000
        return Observation(
            target.id,
            key,
            "monitoring",
            not capped,
            "Log collection reached its limit" if capped else "Log collection connected",
            {"records": count, "limit": 1000},
            immediate=capped,
            next_step="Configure push log ingestion for complete high-volume error coverage.",
            checkpoint={} if capped else {f"log-poll:{target.id}:{key}": collected},
        )

    async def collect(self, target: Target) -> tuple[list[Observation], list[AccessEvent]]:
        method = getattr(self, target.kind)
        try:
            observations, events = await method(target)
        except Exception as error:
            return [
                Observation(
                    target.id,
                    "collector",
                    "monitoring",
                    False,
                    "Monitor cannot collect data",
                    {"error": redact_text(str(error), 800)},
                    "warning",
                    False,
                    next_step="Check CLI login, permissions, network, and this target's settings.",
                )
            ], []
        observations.append(Observation(target.id, "collector", "monitoring", True, "Collector connected"))
        return observations, events

    async def health(self, target: Target) -> list[Observation]:
        headers = json.loads(secret(target.headers_env)) if target.headers_env else {}
        started = time.monotonic()
        challenged = False
        try:
            # Do not collect response bodies into incident evidence.
            async with self.client.stream(
                "GET", target.url, headers=headers, timeout=target.timeout_seconds, follow_redirects=True
            ) as response:
                body = bytearray()
                if target.body_contains:
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 256 * 1024:
                            break
                elapsed = round((time.monotonic() - started) * 1000, 1)
                healthy = response.status_code in target.expected_status
                if target.body_contains:
                    healthy = healthy and target.body_contains in body.decode(errors="replace")
                evidence = {
                    "url": target.url,
                    "http_status": response.status_code,
                    "latency_ms": elapsed,
                    "body_check": bool(target.body_contains),
                }
                challenged = response.headers.get("x-vercel-mitigated") == "challenge"
                if challenged:
                    healthy = None
                    evidence["mitigation"] = "challenge"
        except httpx.HTTPError as error:
            healthy, elapsed = False, 0
            evidence = {"url": target.url, "error": redact_text(str(error), 500)}
        observations = [
            Observation(
                target.id,
                "http",
                "availability",
                healthy,
                "Application is reachable"
                if healthy
                else "Application health cannot be verified"
                if challenged
                else "Application health check failed",
                evidence,
                "critical",
                immediate=not challenged and evidence.get("http_status", 0) >= 500,
                next_step="Check the service logs and dependencies. Use /diagnose for a fix plan.",
            )
        ]
        observations.append(
            Observation(
                target.id,
                "http-latency",
                "performance",
                elapsed <= target.slow_ms if healthy else None,
                "Health endpoint response is slow"
                if healthy and elapsed > target.slow_ms
                else "Health endpoint latency within threshold"
                if healthy
                else "No latency sample",
                {"latency_ms": elapsed, "threshold_ms": target.slow_ms},
            )
        )
        observations.append(
            Observation(
                target.id,
                "http-probe",
                "monitoring",
                False if challenged else True if "http_status" in evidence else None,
                "Vercel security challenge prevents health verification"
                if challenged
                else "Health check is not challenged",
                evidence if challenged else {},
                "warning",
                next_step="Allow this monitor through a narrowly scoped WAF rule for its production "
                "health check. The application state is unknown while challenged.",
            )
        )
        return observations

    async def http(self, target: Target):
        return await self.health(target), []

    async def railway(self, target: Target):
        env = await railway_account_env(self.config.account_for(target), self.client)
        context = [
            "--project",
            target.project,
            "--service",
            target.service,
            "--environment",
            target.environment,
        ]
        result = parse_json(
            await run(
                ["railway", "deployment", "list", *context, "--limit", "3", "--json"],
                env=env,
                timeout=target.timeout_seconds,
            )
        )
        rows = result if isinstance(result, list) else result.get("deployments", [])
        latest = rows[0] if rows else {}
        status = latest.get("status", "NO_DEPLOYMENTS")
        failed = status in {"FAILED", "CRASHED", "REMOVED"}
        observations = [
            Observation(
                target.id,
                "deployment",
                "deployment",
                False if failed else True if status == "SUCCESS" else None,
                f"Latest Railway deployment: {status}",
                {
                    "status": status,
                    "deployment_id": latest.get("id"),
                    "account": target.account,
                    "project": target.project,
                    "environment": target.environment,
                    "service": target.service,
                },
                "critical" if status == "CRASHED" else "warning",
                immediate=failed,
            )
        ]
        events = []
        if target.url:
            observations.extend(await self.health(target))
        if target.logs:
            for http in (False, True):
                log_key = "http-logs" if http else "runtime-logs"
                collected, oldest, since = self.log_window(target, log_key)
                try:
                    records = parse_json_lines(
                        await run(
                            [
                                "railway",
                                "logs",
                                *context,
                                "--json",
                                "--lines",
                                "1000",
                                "--since",
                                since,
                                *(["--http"] if http else []),
                                *([] if http else ["--latest"]),
                            ],
                            env=env,
                            timeout=target.timeout_seconds,
                        )
                    )
                except Exception as error:
                    observations.append(
                        Observation(
                            target.id,
                            "http-logs" if http else "runtime-logs",
                            "monitoring",
                            False,
                            "Railway log collection failed",
                            {"error": redact_text(str(error), 500)},
                        )
                    )
                    continue
                logs, access = parse_logs(target, records, self.store, platform_http=http, oldest=oldest)
                observations.extend(logs)
                events.extend(access)
                observations.append(self.log_result(target, log_key, len(records), collected))
        return observations, events

    async def vercel(self, target: Target):
        account = self.config.account_for(target)
        env = account_env("vercel", account)
        result = parse_json(
            await run(
                [
                    "vercel",
                    "list",
                    target.project,
                    "--environment",
                    target.environment,
                    "--json",
                    "--limit",
                    "3",
                    *scope_args(account),
                ],
                env=env,
                timeout=target.timeout_seconds,
            )
        )
        rows = result.get("deployments", [])
        latest = rows[0] if rows else {}
        state = latest.get("state", "NO_DEPLOYMENTS")
        observations = [
            Observation(
                target.id,
                "deployment",
                "deployment",
                False if state == "ERROR" else True if state == "READY" else None,
                f"Latest Vercel deployment: {state}",
                {
                    "state": state,
                    "deployment_url": latest.get("url"),
                    "project": target.project,
                    "account": target.account,
                },
                "warning",
                immediate=state == "ERROR",
            )
        ]
        observations.append(
            Observation(
                target.id,
                "health-url",
                "monitoring",
                bool(target.url),
                "Production health URL configured"
                if target.url
                else "Production health URL is not configured",
                {"url": target.url},
                "warning",
                next_step="Set this project's custom production URL. "
                "Deployment and log checks remain active.",
            )
        )
        if target.url:
            observations.extend(await self.health(target))
        events = []
        if target.logs:
            collected, oldest, since = self.log_window(target, "runtime-logs")
            try:
                records = parse_json_lines(
                    await run(
                        [
                            "vercel",
                            "logs",
                            "--project",
                            target.project,
                            "--environment",
                            target.environment,
                            "--since",
                            since,
                            "--limit",
                            "1000",
                            "--json",
                            *scope_args(account),
                        ],
                        env=env,
                        timeout=target.timeout_seconds,
                    )
                )
                logs, events = parse_logs(target, records, self.store, oldest=oldest)
                observations.extend(logs)
                observations.append(self.log_result(target, "runtime-logs", len(records), collected))
            except Exception as error:
                observations.append(
                    Observation(
                        target.id,
                        "runtime-logs",
                        "monitoring",
                        False,
                        "Vercel log collection failed",
                        {"error": redact_text(str(error), 500)},
                    )
                )
        return observations, events

    async def sentry(self, target: Target):
        result = parse_json(
            await run(
                [
                    "sentry",
                    "issue",
                    "list",
                    f"{target.organization}/{target.project}",
                    "--query",
                    f"environment:{target.environment}",
                    "--period",
                    "24h",
                    "--limit",
                    "1000",
                    "--sort",
                    "date",
                    "--json",
                    "--fresh",
                    "--fields",
                    "id,shortId,title,culprit,count,userCount,lastSeen,level,status,permalink",
                ],
                timeout=target.timeout_seconds,
            )
        )
        rows = result if isinstance(result, list) else result.get("data", result.get("issues", []))
        observations = []
        collected = time.time()
        previous = self.store.get(f"sentry-poll:{target.id}", collected - 600)
        cutoff = max(collected - 86400, previous - 120)
        for issue in rows:
            if epoch(issue.get("lastSeen")) < cutoff:
                continue
            # An unresolved issue remains visible without announcing the same count every poll.
            observations.append(
                Observation(
                    target.id,
                    "sentry:" + str(issue["id"]),
                    "application error",
                    False,
                    str(issue.get("title", "Sentry issue")),
                    {
                        k: issue.get(k)
                        for k in ("shortId", "culprit", "count", "userCount", "lastSeen", "permalink")
                    },
                    "critical" if issue.get("level") == "fatal" else "warning",
                    True,
                    dedup_key=f"sentryseen:{target.id}:{issue['id']}:"
                    f"{issue.get('lastSeen')}:{issue.get('count')}",
                )
            )
        capped = isinstance(result, dict) and result.get("hasMore", False)
        observations.append(
            Observation(
                target.id,
                "issue-coverage",
                "monitoring",
                not capped,
                "Sentry issue collection reached its limit" if capped else "Sentry issue collection complete",
                {"limit": 1000, "records": len(rows)},
                immediate=bool(capped),
                next_step="Use push ingestion or narrow the query window to avoid missing issue activity.",
                checkpoint={} if capped else {f"sentry-poll:{target.id}": collected},
            )
        )
        return observations, []

    async def postgres(self, target: Target):
        if target.dsn_env:
            dsn = secret(target.dsn_env)
        else:
            output = await run(
                [
                    "neon",
                    "connection-string",
                    target.neon_branch_id,
                    "--project-id",
                    target.neon_project_id,
                    "--database-name",
                    target.neon_database,
                    "--role-name",
                    target.neon_role,
                    "--ssl",
                    "require",
                ],
                timeout=target.timeout_seconds,
            )
            match = re.search(r"postgres(?:ql)?://[^\s\"]+", output)
            if not match:
                raise ValueError("Neon CLI returned no connection string")
            dsn = match.group(0)
        try:
            connection = await psycopg.AsyncConnection.connect(
                dsn,
                connect_timeout=target.timeout_seconds,
                options="-c default_transaction_read_only=on -c statement_timeout=5000",
                row_factory=dict_row,
            )
        except psycopg.OperationalError as error:
            return [
                Observation(
                    target.id,
                    "database",
                    "availability",
                    False,
                    "Database connection failed",
                    {"error": redact_text(str(error), 500)},
                    "critical",
                    immediate=True,
                )
            ], []
        async with connection:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT 1")
                observations = [
                    Observation(target.id, "database", "availability", True, "Database connected")
                ]
                await cursor.execute(
                    "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='pg_stat_statements')"
                )
                exists = await cursor.fetchone()
                if not next(iter(exists.values())):
                    observations.append(
                        Observation(
                            target.id,
                            "query-statistics",
                            "monitoring",
                            False,
                            "Query statistics extension is not enabled",
                            {"extension": "pg_stat_statements"},
                            next_step="Enable pg_stat_statements with an administrator. Repeat this check.",
                        )
                    )
                    return observations, []
                observations.append(
                    Observation(
                        target.id, "query-statistics", "monitoring", True, "Query statistics available"
                    )
                )
                await cursor.execute("SELECT stats_reset FROM pg_stat_statements_info")
                reset = str((await cursor.fetchone())["stats_reset"])
                await cursor.execute("""SELECT userid::text, dbid::text, queryid::text,
                    toplevel, query, calls,
                    total_exec_time, rows, shared_blks_hit, shared_blks_read
                    FROM pg_stat_statements WHERE dbid=(SELECT oid FROM pg_database
                    WHERE datname=current_database()) AND calls > 0
                    AND query NOT ILIKE '%%pg_stat_statements%%'
                    ORDER BY total_exec_time DESC LIMIT 200""")
                rows = await cursor.fetchall()
        current, previous = {}, self.store.get(f"pg:{target.id}", {})
        for row in rows:
            key = f"{row['userid']}:{row['dbid']}:{row['queryid']}:{row.get('toplevel', True)}"
            current[key] = {"calls": row["calls"], "time": row["total_exec_time"], "reset": reset}
            old = previous.get(key)
            if not old or old["reset"] != reset or row["calls"] < old["calls"]:
                continue
            calls = row["calls"] - old["calls"]
            elapsed = row["total_exec_time"] - old["time"]
            if calls < target.min_query_calls or elapsed < 0:
                continue
            mean = round(elapsed / calls, 2)
            observations.append(
                Observation(
                    target.id,
                    "query:" + key,
                    "query performance",
                    mean <= target.slow_ms,
                    "Query exceeds interval average threshold"
                    if mean > target.slow_ms
                    else "Query interval average within threshold",
                    {
                        "query_id": row["queryid"],
                        "query": scrub_sql(row["query"]),
                        "interval_calls": calls,
                        "interval_mean_ms": mean,
                        "interval_total_ms": round(elapsed, 2),
                        "threshold_ms": target.slow_ms,
                    },
                    "warning",
                    True,
                    next_step="Use /diagnose to review indexes, joins, and N+1 queries. "
                    "Validate EXPLAIN on a test branch.",
                )
            )
        self.store.set(f"pg:{target.id}", current)
        self.store.set(f"pg_sample:{target.id}", time.time())
        return observations, []

    async def server(self, target: Target):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]*", target.ssh_host):
            raise ValueError("use a fixed SSH config alias without options")

        async def remote(argv):
            return await run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", target.ssh_host, shlex.join(argv)],
                timeout=target.timeout_seconds,
            )

        observations, events = [], []
        for unit in target.units:
            state = (
                await remote(["systemctl", "show", "--property=ActiveState", "--value", "--", unit])
            ).strip()
            observations.append(
                Observation(
                    target.id,
                    "unit:" + unit,
                    "availability",
                    state == "active",
                    f"systemd {unit}: {state}",
                    {"unit": unit, "state": state},
                    "critical",
                )
            )
            if target.logs:
                records = parse_json_lines(
                    await remote(
                        [
                            "journalctl",
                            "--unit",
                            unit,
                            "--since",
                            "5 min ago",
                            "--lines",
                            "200",
                            "--output=json",
                            "--no-pager",
                        ]
                    )
                )
                logs, access = parse_logs(target, records, self.store)
                observations.extend(logs)
                events.extend(access)
        for container in target.containers:
            state = parse_json(
                await remote(["docker", "inspect", "--format", "{{json .State}}", "--", container])
            )
            healthy = (
                state.get("Running", False) and state.get("Health", {}).get("Status", "healthy") == "healthy"
            )
            observations.append(
                Observation(
                    target.id,
                    "docker:" + container,
                    "availability",
                    healthy,
                    f"Docker container {container}",
                    {
                        "container": container,
                        "status": state.get("Status"),
                        "health": state.get("Health", {}).get("Status"),
                        "exit_code": state.get("ExitCode"),
                    },
                    "critical",
                )
            )
        return observations, events

    async def logfile(self, target: Target):
        path = Path(target.log_path)
        stat = path.stat()
        saved = self.store.get(f"file:{target.id}", {})
        offset = saved.get("offset", max(0, stat.st_size - 256 * 1024))
        if saved.get("inode", stat.st_ino) != stat.st_ino or stat.st_size < offset:
            offset = 0
        with path.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(512 * 1024)
        # Retain an unfinished last line for the next pass.
        end = data.rfind(b"\n") + 1
        if not end:
            return [], []
        content = data[:end].decode(errors="replace")
        if not saved and offset:
            content = content.partition("\n")[2]
        observations, events = parse_logs(target, parse_json_lines(content), self.store)
        observations.append(
            Observation(
                target.id,
                "file-cursor",
                "monitoring",
                True,
                "Log file read",
                checkpoint={f"file:{target.id}": {"inode": stat.st_ino, "offset": offset + end}},
            )
        )
        return observations, events


async def discover(config: Config, providers: list[str]) -> tuple[list[Target], dict]:
    targets, inventory = [], {"discovered_at": time.time(), "providers": {}, "errors": {}}
    if "railway" in providers:
        for account in config.railway_accounts:
            try:
                projects = parse_json(
                    await run(
                        ["railway", "list", "--json"], env=await railway_account_env(account), timeout=45
                    )
                )
                inventory["providers"][f"railway:{account.name}"] = []
                for project in projects:
                    envs = [e["node"] for e in project.get("environments", {}).get("edges", [])]
                    services = [s["node"] for s in project.get("services", {}).get("edges", [])]
                    inventory["providers"][f"railway:{account.name}"].append(
                        {
                            "id": project["id"],
                            "name": project["name"],
                            "environments": envs,
                            "services": services,
                        }
                    )
                    for environment in envs:
                        for service in services:
                            targets.append(
                                Target(
                                    id=f"rw-{account.name}-{service['id'][:8]}-{environment['id'][:8]}",
                                    name=f"{project['name']} / {service['name']} / {environment['name']}",
                                    kind="railway",
                                    account=account.name,
                                    project=project["id"],
                                    environment=environment["id"],
                                    service=service["id"],
                                    enabled=environment["name"] == "production",
                                    interval_seconds=120,
                                    logs=True,
                                )
                            )
            except Exception as error:
                inventory["errors"][f"railway:{account.name}"] = redact_text(str(error))
    if "vercel" in providers:
        for account in config.vercel_accounts:
            try:
                projects, next_page = [], None
                for _ in range(20):
                    result = parse_json(
                        await run(
                            [
                                "vercel",
                                "project",
                                "ls",
                                "--json",
                                "--limit",
                                "100",
                                *scope_args(account),
                                *(["--next", str(next_page)] if next_page else []),
                            ],
                            env=account_env("vercel", account),
                            timeout=45,
                        )
                    )
                    projects.extend(result["projects"])
                    next_page = result.get("pagination", {}).get("next")
                    if not next_page:
                        break
                if next_page:
                    raise ValueError("Vercel inventory exceeded 20 pages; scope accounts more narrowly")
                inventory["providers"][f"vercel:{account.name}"] = projects
                for project in projects:
                    try:
                        url = await vercel_production_url(account, project)
                    except Exception as error:
                        url = None
                        inventory["errors"][f"vercel:{account.name}:{project['name']}"] = redact_text(
                            str(error)
                        )
                    targets.append(
                        Target(
                            id=f"vc-{account.name}-{project['name']}",
                            name=project["name"],
                            kind="vercel",
                            account=account.name,
                            project=project["name"],
                            url=url,
                            enabled=bool(url),
                            interval_seconds=120,
                            logs=True,
                        )
                    )
            except Exception as error:
                inventory["errors"][f"vercel:{account.name}"] = redact_text(str(error))
    if "sentry" in providers:
        try:
            organizations = parse_json(await run(["sentry", "org", "list", "--json"], timeout=45))
            inventory["providers"]["sentry"] = []
            for org in organizations:
                result = parse_json(
                    await run(
                        [
                            "sentry",
                            "project",
                            "list",
                            org["slug"] + "/",
                            "--json",
                            "--limit",
                            "100",
                            "--fields",
                            "id,slug,name",
                        ],
                        timeout=45,
                    )
                )
                rows = result if isinstance(result, list) else result.get("data", result.get("projects", []))
                for project in rows:
                    inventory["providers"]["sentry"].append({"organization": org["slug"], **project})
                    targets.append(
                        Target(
                            id=f"sn-{org['slug']}-{project['slug']}",
                            name=f"Sentry / {project['slug']}",
                            kind="sentry",
                            organization=org["slug"],
                            project=project["slug"],
                            interval_seconds=120,
                        )
                    )
        except Exception as error:
            inventory["errors"]["sentry"] = redact_text(str(error))
    if "neon" in providers:
        try:
            result = parse_json(await run(["neon", "projects", "list", "--output", "json"], timeout=45))
            inventory["providers"]["neon"] = [
                {k: p.get(k) for k in ("id", "name", "region_id", "pg_version")}
                for p in result.get("projects", [])
            ]
            for project in result.get("projects", []):
                env_name = "NEON_" + re.sub(r"[^A-Z0-9]", "_", project["name"].upper()) + "_DATABASE_URL"
                targets.append(
                    Target(
                        id="pg-" + project["id"],
                        name=f"Neon / {project['name']}",
                        kind="postgres",
                        dsn_env=env_name,
                        enabled=False,
                        interval_seconds=300,
                        slow_ms=200,
                    )
                )
        except Exception as error:
            inventory["errors"]["neon"] = redact_text(str(error))
    return targets, inventory
