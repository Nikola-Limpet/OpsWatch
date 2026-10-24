"""Local monitoring dashboard with explicit, protected manual repair actions."""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from fastapi import FastAPI, Request
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

from .config import Config
from .favicons import Favicons
from .formatting import incident_markup
from .redact import sanitize
from .repairs import Repairs
from .selection import projects, refresh_selection, set_selection
from .storage import Store
from .studio_api import read_studio_action
from .web_assets import SPA_PATHS, frontend_asset, frontend_page

ASSETS = Path(__file__).with_name("dashboard_assets")
EVENT_KINDS = {"application error", "security", "query performance"}


def provider_name(target) -> str:
    if target.kind == "postgres" and target.neon_project_id:
        return "Neon"
    return {"http": "HTTP", "postgres": "PostgreSQL", "logfile": "Log file"}.get(
        target.kind, target.kind.capitalize()
    )


def repair_capability(item, config: Config) -> dict:
    target = next((t for t in config.targets if t.id == item["target"] and t.enabled), None)
    reason = ""
    if not config.repair.enabled:
        reason = "Repairs are disabled in this workspace."
    elif item["kind"] in {"monitoring", "security"}:
        reason = "This incident needs configuration or security review. Use the diagnosis command below."
    elif not target or not target.repair_repo:
        reason = "No verified repository is mapped. Choose one with /fix in Hermes before starting a repair."
    return {"available": not reason, "reason": reason}


def public_repair(job: dict) -> dict:
    return sanitize(
        {
            "id": job["id"],
            "incident": job["incident"],
            "status": job["status"],
            "summary": job.get("summary") or job.get("error", ""),
            "pr_url": job.get("pr_url"),
            "updated": job["updated"],
        }
    )


def incident_record(row, config: Config) -> dict:
    item = dict(row)
    item["evidence"] = json.loads(item["evidence"])
    item["ref"] = f"OW-{item['id']:04d}"
    item["repair"] = repair_capability(item, config)
    markup = incident_markup(item, config) or {}
    item["links"] = [
        button for buttons in markup.get("inline_keyboard", []) for button in buttons if "url" in button
    ]
    return sanitize(item)


def snapshot(config: Config) -> dict:
    refresh_selection(config)
    now = time.time()
    targets = {t.id: t for t in config.targets if t.enabled}
    checks, incidents, open_items, heartbeat, repairs = [], [], [], None, []
    worker_heartbeat = None
    repair_incidents = []
    database = Path(config.data_dir).resolve() / "opswatch.sqlite3"
    if database.exists():
        # A separate read-only connection per request coexists with the running monitor.
        # Do not construct Store here: its constructor creates tables and changes file modes.
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=3)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            row = db.execute("SELECT value FROM state WHERE name='heartbeat'").fetchone()
            heartbeat = json.loads(row[0]) if row else None
            row = db.execute("SELECT value FROM state WHERE name='repair_heartbeat'").fetchone()
            worker_heartbeat = json.loads(row[0]) if row else None
            checks = [dict(r) for r in db.execute("SELECT * FROM checks") if r["target"] in targets]
            open_items = [
                dict(r)
                for r in db.execute("SELECT target,severity FROM incidents WHERE status='open'")
                if r["target"] in targets
            ]
            placeholders = ",".join("?" for _ in targets)
            if targets:
                rows = db.execute(
                    f"SELECT * FROM incidents WHERE target IN ({placeholders}) AND status='open' "
                    "UNION ALL SELECT * FROM ("
                    f"SELECT * FROM incidents WHERE target IN ({placeholders}) AND status='resolved' "
                    "ORDER BY updated DESC LIMIT 200) ORDER BY updated DESC",
                    (*targets, *targets),
                )
                incidents = [incident_record(r, config) for r in rows]
            if targets and db.execute("SELECT 1 FROM sqlite_master WHERE name='repair_jobs'").fetchone():
                rows = db.execute(
                    "WITH visible AS (SELECT r.*, i.target, i.title FROM repair_jobs r "
                    "JOIN incidents i ON r.incident=printf('OW-%04d',i.id) "
                    f"WHERE i.target IN ({placeholders})) "
                    "SELECT * FROM visible WHERE status NOT IN ('pr_open','blocked','cancelled') "
                    "OR id IN (SELECT id FROM visible ORDER BY updated DESC LIMIT 100) "
                    "ORDER BY updated DESC",
                    tuple(targets),
                )
                for row in rows:
                    repairs.append(
                        {
                            **public_repair(Repairs.decode(row)),
                            "target": row["target"],
                            "service": sanitize(targets[row["target"]].name),
                            "title": sanitize(row["title"]),
                        }
                    )
                visible = {item["ref"] for item in incidents}
                missing = {job["incident"] for job in repairs} - visible
                if missing:
                    refs = ",".join("?" for _ in missing)
                    repair_incidents = [
                        incident_record(row, config)
                        for row in db.execute(
                            f"SELECT * FROM incidents WHERE printf('OW-%04d',id) IN ({refs})",
                            tuple(missing),
                        )
                    ]
    monitor_limit = max((t.interval_seconds for t in targets.values()), default=60) * 3 + 30
    live = bool(heartbeat and now - heartbeat <= monitor_limit)
    monitor = "running" if live else "stale" if heartbeat else "not_started"
    services, queries = [], []
    for target in targets.values():
        observations = []
        for row in checks:
            if row["target"] == target.id:
                check = dict(row)
                check["evidence"] = json.loads(check["evidence"])
                check["stale"] = now - check["checked"] > target.interval_seconds * 3 + 30
                observations.append(sanitize(check))
        periodic = [r for r in observations if r["kind"] not in EVENT_KINDS]
        active = [i for i in open_items if i["target"] == target.id]
        failed = [r for r in observations if r["healthy"] == 0 and not r["stale"]]
        if not observations:
            status = "unknown"
        elif not live:
            status = "stale"
        elif failed or active:
            status = "critical" if any(i["severity"] == "critical" for i in active) else "warning"
        elif not periodic or any(r["stale"] for r in periodic):
            status = "stale"
        elif any(r["healthy"] is None for r in periodic):
            status = "unknown"
        else:
            status = "healthy"
        sample = next(
            (
                r
                for r in observations
                if r["key"] == "http"
                and not r["stale"]
                and r["healthy"] is not None
                and isinstance(r["evidence"].get("latency_ms"), (int, float))
                and r["evidence"]["latency_ms"] > 0
            ),
            None,
        )
        parsed = urlsplit(target.url or "")
        app_url = (
            f"{parsed.scheme}://{parsed.netloc}"
            if parsed.scheme in {"https", "http"} and not parsed.username and not parsed.password
            else None
        )
        service = {
            "id": target.id,
            "name": sanitize(target.name),
            "kind": target.kind,
            "provider": provider_name(target),
            "account": sanitize(target.account),
            "environment": sanitize(
                target.environment_name
                or (target.environment if len(target.environment) < 30 else "production")
            ),
            "status": status,
            "checked": max((r["checked"] for r in periodic), default=None),
            "latency_ms": sample["evidence"]["latency_ms"] if sample and live else None,
            "threshold_ms": target.slow_ms,
            "url": app_url,
            "favicon": "/favicons/" + target.id,
            "auto_repair_mapped": bool(target.repair_repo),
            "open_incidents": len(active),
            "checks": observations,
        }
        services.append(service)
        for check in observations:
            if check["kind"] == "query performance":
                queries.append({**check, "name": service["name"]})
    order = {"critical": 0, "warning": 1, "unknown": 2, "stale": 3, "healthy": 4}
    services.sort(key=lambda s: (order[s["status"]], s["name"].lower()))
    summary = {
        "total": len(services),
        "healthy": sum(s["status"] == "healthy" for s in services),
        "attention": sum(s["status"] in {"critical", "warning"} for s in services),
        "unknown": sum(s["status"] in {"unknown", "stale"} for s in services),
        "open_incidents": len(open_items),
    }
    active = {service["id"]: service for service in services}
    groups = []
    for project in projects(config):
        entries = []
        for target in project["targets"]:
            entries.append(
                {
                    **active.get(
                        target.id,
                        {
                            "id": target.id,
                            "name": sanitize(target.name),
                            "provider": provider_name(target),
                            "status": "disabled",
                            "environment": sanitize(target.environment_name or target.environment),
                            "latency_ms": None,
                            "threshold_ms": target.slow_ms,
                            "checked": None,
                            "favicon": None,
                            "open_incidents": 0,
                            "checks": [],
                        },
                    ),
                    "enabled": target.enabled,
                }
            )
        groups.append(
            {
                "id": project["id"],
                "name": sanitize(project["name"]),
                "enabled_count": sum(t.enabled for t in project["targets"]),
                "services": entries,
            }
        )
    return {
        "generated_at": now,
        "timezone": config.timezone,
        "monitor": {"status": monitor, "last_cycle": heartbeat},
        "repair_worker": {
            "status": (
                "running"
                if worker_heartbeat and now - worker_heartbeat < 60
                else "stale"
                if worker_heartbeat
                else "not_started"
            ),
            "last_cycle": worker_heartbeat,
        },
        "summary": summary,
        "services": services,
        "projects": groups,
        "selection_editable": config._source_path is not None,
        "incidents": incidents,
        "incidents_limit": 200,
        "queries": sorted(queries, key=lambda q: q["checked"], reverse=True),
        "repairs": repairs,
        "repair_incidents": repair_incidents,
        "automation": {
            "auto_fix": config.repair.enabled and config.repair.auto_fix,
            "daily_limit": config.repair.max_per_day,
            "notifications": config.telegram.notifications,
        },
    }


def application(config: Config) -> FastAPI:
    icons = Favicons(config)
    action_token = secrets.token_urlsafe(32)

    def authorized(request):
        return request.headers.get(
            "origin"
        ) == f"{request.url.scheme}://{request.url.netloc}" and secrets.compare_digest(
            request.headers.get("X-OpsWatch-CSRF", "").encode(), action_token.encode()
        )

    async def favicon(request: Request):
        from starlette.responses import Response

        icon = await icons.get(request.path_params["target_id"])
        if not icon:
            return Response(status_code=404, headers={"Cache-Control": "private, max-age=3600"})
        return Response(
            icon.data,
            media_type=icon.content_type,
            headers={
                "Cache-Control": "private, max-age=86400",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            },
        )

    async def overview(request: Request):
        return frontend_page(ASSETS, action_token)

    async def csrf(request: Request):
        # The development server injects this same-origin token into its HTML.
        # The production page already receives it in frontend_page().
        return JSONResponse({"token": action_token})

    async def asset(request: Request):
        return frontend_asset(ASSETS, request.path_params["name"])

    async def state(request: Request):
        try:
            return JSONResponse(snapshot(config))
        except sqlite3.Error:
            return JSONResponse(
                {"error": "Monitor data is temporarily unavailable. Try refreshing."}, status_code=503
            )

    async def evidence(request: Request):
        refresh_selection(config)
        ref = request.path_params["ref"]
        if not ref.startswith("OW-") or not ref[3:].isascii() or not ref[3:].isdigit() or len(ref) > 20:
            return JSONResponse({"error": "Incident not found"}, status_code=404)
        path = Path(config.data_dir).resolve() / "opswatch.sqlite3"
        if path.exists():
            try:
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)) as db:
                    db.row_factory = sqlite3.Row
                    row = db.execute("SELECT * FROM incidents WHERE id=?", (int(ref[3:]),)).fetchone()
                    if row and any(t.id == row["target"] and t.enabled for t in config.targets):
                        return JSONResponse(incident_record(row, config))
            except sqlite3.Error:
                return JSONResponse({"error": "Monitor data is temporarily unavailable."}, status_code=503)
        return JSONResponse({"error": "Incident not found"}, status_code=404)

    async def start_fix(request: Request):
        refresh_selection(config)
        if not authorized(request):
            return JSONResponse(
                {"error": "Reload this local dashboard before starting a fix."}, status_code=403
            )
        ref = request.path_params["ref"]
        if not ref.startswith("OW-") or not ref[3:].isascii() or not ref[3:].isdigit() or len(ref) > 20:
            return JSONResponse({"error": "Incident not found"}, status_code=404)
        if not (Path(config.data_dir) / "opswatch.sqlite3").exists():
            return JSONResponse({"error": "No recorded incident is available."}, status_code=404)
        try:
            with closing(Store(config.data_dir)) as store:
                item = store.incident(ref)
                if not item or not any(t.id == item["target"] and t.enabled for t in config.targets):
                    return JSONResponse({"error": "Incident not found"}, status_code=404)
                capability = repair_capability(item, config)
                if not capability["available"]:
                    return JSONResponse({"error": capability["reason"]}, status_code=409)
                return JSONResponse(
                    public_repair(Repairs(config, store).enqueue(ref, "dashboard")), status_code=202
                )
        except ValueError as error:
            return JSONResponse({"error": sanitize(str(error))}, status_code=409)
        except sqlite3.Error:
            return JSONResponse(
                {"error": "Repair queue is temporarily unavailable. Try again."}, status_code=503
            )

    async def monitoring(request: Request):
        if not authorized(request):
            return JSONResponse(
                {"error": "Reload this local dashboard before changing monitoring."}, status_code=403
            )
        try:
            body = await request.body()
            if len(body) > 128:
                raise ValueError
            data = json.loads(body)
            if not isinstance(data, dict) or set(data) != {"enabled"} or type(data["enabled"]) is not bool:
                raise ValueError
        except (ValueError, TypeError):
            return JSONResponse({"error": "Supply an enabled value of true or false."}, status_code=400)
        try:
            changed = set_selection(
                config, request.url.path.split("/")[2], request.path_params["identity"], data["enabled"]
            )
            return JSONResponse({"enabled": data["enabled"], "targets": changed})
        except KeyError:
            return JSONResponse({"error": "Project or service not found."}, status_code=404)
        except (ValueError, yaml.YAMLError):
            return JSONResponse(
                {"error": "Configuration is invalid. Check its settings before changing monitoring."},
                status_code=409,
            )
        except OSError:
            return JSONResponse(
                {"error": "Could not save monitoring settings. Check configuration file permissions."},
                status_code=503,
            )

    async def studio_state(request: Request):
        from .studio import studio_snapshot

        try:
            return JSONResponse(studio_snapshot(config))
        except (sqlite3.Error, OSError, ValueError):
            return JSONResponse({"error": "Agent workspace is temporarily unavailable."}, status_code=503)

    async def studio_action(request: Request):
        from .studio import Studio

        if not authorized(request):
            return JSONResponse(
                {"error": "Reload the dashboard before changing the agent workspace."}, status_code=403
            )
        try:
            action, payload, identity = await read_studio_action(request)
        except ValueError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        try:
            with closing(Store(config.data_dir)) as store:
                result = Studio(config, store).dispatch(action, payload, identity)
            return JSONResponse(result, status_code=202 if "job" in result else 200)
        except KeyError:
            return JSONResponse({"error": "The requested agent record was not found."}, status_code=404)
        except ValueError as error:
            return JSONResponse({"error": sanitize(str(error))}, status_code=409)
        except (sqlite3.Error, OSError):
            return JSONResponse({"error": "Could not save the agent workspace. Try again."}, status_code=503)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        from .studio import StudioWorker

        stop = asyncio.Event()
        worker = asyncio.create_task(StudioWorker(config).serve(stop))
        try:
            yield
        finally:
            stop.set()
            try:
                await asyncio.wait_for(asyncio.shield(worker), timeout=5)
            except TimeoutError:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker

    app = FastAPI(
        title="OpsWatch API",
        version="0.2.0",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
        middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])],
    )
    for path in SPA_PATHS:
        app.add_api_route(path, overview, methods=["GET"], include_in_schema=False)
    for path, endpoint, methods in [
        ("/assets/{name:path}", asset, ["GET"]),
        ("/favicons/{target_id}", favicon, ["GET"]),
        ("/api/snapshot", state, ["GET"]),
        ("/api/csrf", csrf, ["GET"]),
        ("/api/incidents/{ref}", evidence, ["GET"]),
        ("/api/incidents/{ref}/fix", start_fix, ["POST"]),
        ("/api/services/{identity}/monitoring", monitoring, ["POST"]),
        ("/api/projects/{identity}/monitoring", monitoring, ["POST"]),
        ("/api/studio", studio_state, ["GET"]),
        ("/api/studio/actions", studio_action, ["POST"]),
    ]:
        app.add_api_route(path, endpoint, methods=methods, response_model=None)

    async def headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers.setdefault(
            "Content-Security-Policy",
            (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                "connect-src 'self'; object-src 'none'; base-uri 'none'; "
                "form-action 'none'; frame-ancestors 'none'"
            ),
        )
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=headers)
    return app
