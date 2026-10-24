"""Private cloud dashboard: bounded display data and a fixed local-worker command queue."""

from __future__ import annotations

import base64
import binascii
import copy
import json
import math
import re
import secrets
import zlib
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from .cloud_store import RelayError
from .favicons import image_type
from .studio_api import read_studio_action
from .web_assets import SPA_PATHS, frontend_asset, frontend_page

ASSETS = Path(__file__).with_name("dashboard_assets")
LIMIT = 4 * 1024 * 1024
IDENTITY = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}\Z")
INCIDENT = re.compile(r"OW-[0-9]{1,17}\Z")
ASSET_NAMES = {"app.css", "app.js", "geist-sans.woff2", "geist-mono.woff2", "FONT-LICENSE.txt"}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def uuid_value(value):
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError("Invalid request ID")
    return str(UUID(value))


def json_tree(value, depth=0):
    """Reject non-JSON numbers and excessive nesting before data reaches Postgres JSONB."""
    if depth > 32:
        raise ValueError("Display data is nested too deeply")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 256:
                raise ValueError("Invalid display field")
            json_tree(key, depth + 1)
            json_tree(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            json_tree(item, depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Invalid number")
    elif isinstance(value, str):
        if "\x00" in value:
            raise ValueError("Invalid text")
        value.encode("utf-8")


def validate_service(service):
    if not isinstance(service.get("id"), str) or not IDENTITY.fullmatch(service["id"]):
        raise ValueError("Invalid service identity")
    checks = service.get("checks", [])
    if not isinstance(checks, list) or any(not isinstance(c, dict) for c in checks):
        raise ValueError("Invalid service checks")
    if "enabled" in service and type(service["enabled"]) is not bool:
        raise ValueError("Invalid service enabled state")
    if "status" in service and service["status"] not in {
        "healthy",
        "warning",
        "critical",
        "unknown",
        "stale",
        "disabled",
    }:
        raise ValueError("Invalid service status")


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        raise ValueError("Snapshot must be an object")
    for key in (
        "services",
        "projects",
        "incidents",
        "queries",
        "repairs",
        "repair_incidents",
        "investigations",
    ):
        items = snapshot.get(key, [])
        if not isinstance(items, list) or len(items) > 5000 or any(not isinstance(i, dict) for i in items):
            raise ValueError("Invalid snapshot collection")
    for key in ("monitor", "summary", "automation"):
        if key in snapshot and not isinstance(snapshot[key], dict):
            raise ValueError("Invalid snapshot details")
    for service in snapshot.get("services", []):
        validate_service(service)
    for project in snapshot.get("projects", []):
        if not isinstance(project.get("id"), str) or not IDENTITY.fullmatch(project["id"]):
            raise ValueError("Invalid project identity")
        services = project.get("services", [])
        if not isinstance(services, list) or any(not isinstance(s, dict) for s in services):
            raise ValueError("Invalid project services")
        for service in services:
            validate_service(service)
    for incident in snapshot.get("incidents", []) + snapshot.get("repair_incidents", []):
        if not isinstance(incident.get("ref"), str) or not INCIDENT.fullmatch(incident["ref"]):
            raise ValueError("Invalid incident reference")
        if "repair" in incident and not isinstance(incident["repair"], dict):
            raise ValueError("Invalid repair capability")
    for count in snapshot.get("summary", {}).values():
        if type(count) is not int or count < 0:
            raise ValueError("Invalid summary count")
    monitor = snapshot.get("monitor", {})
    if "status" in monitor and monitor["status"] not in {"running", "stale", "not_started"}:
        raise ValueError("Invalid monitor status")
    if "selection_editable" in snapshot and type(snapshot["selection_editable"]) is not bool:
        raise ValueError("Invalid monitoring selection state")


def validate_sync(payload):
    if not isinstance(payload, dict) or not set(payload) <= {"snapshot", "icons", "results"}:
        raise ValueError("Invalid synchronization payload")
    json_tree(payload)
    if "snapshot" in payload:
        validate_snapshot(payload["snapshot"])
    icons = payload.get("icons", [])
    if not isinstance(icons, list) or len(icons) > 200:
        raise ValueError("Invalid icon collection")
    for icon in icons:
        if not isinstance(icon, dict) or set(icon) != {"target", "content_type", "data_base64"}:
            raise ValueError("Invalid icon")
        if not isinstance(icon["target"], str) or not IDENTITY.fullmatch(icon["target"]):
            raise ValueError("Invalid icon target")
        encoded = icon["data_base64"]
        if not isinstance(encoded, str) or len(encoded) > 349528:
            raise ValueError("Invalid icon size")
        data = base64.b64decode(encoded, validate=True)
        kind = image_type(data)
        if not data or len(data) > 262144 or not kind or kind != icon["content_type"]:
            raise ValueError("Invalid icon content")
        icon["data"] = data
        del icon["data_base64"]
    results = payload.get("results", [])
    if not isinstance(results, list) or len(results) > 100:
        raise ValueError("Invalid results collection")
    for result in results:
        if not isinstance(result, dict) or set(result) != {"id", "result", "error"}:
            raise ValueError("Invalid command result")
        result["id"] = uuid_value(result["id"])
        if result["result"] is not None and not isinstance(result["result"], dict):
            raise ValueError("Invalid command result")
        if result["error"] is not None and (
            not isinstance(result["error"], str) or not result["error"] or len(result["error"]) > 2000
        ):
            raise ValueError("Invalid command error")
        if result["result"] is not None and result["error"] is not None:
            raise ValueError("Supply a result or an error")
        if len(json.dumps(result).encode()) > 65536:
            raise ValueError("Command result is too large")
    return payload


async def read_json(request, limit=LIMIT, *, compressed=False, allow_empty=False):
    encoding = request.headers.get("content-encoding", "identity").lower()
    if encoding not in ({"identity", "gzip"} if compressed else {"identity"}):
        raise RelayError(415, "Unsupported content encoding.")
    body = bytearray()
    size = 0
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
    try:
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise RelayError(413, "Request is too large.")
            body.extend(decoder.decompress(chunk, limit + 1 - len(body)) if decoder else chunk)
            if len(body) > limit or (decoder and decoder.unconsumed_tail):
                raise RelayError(413, "Request is too large.")
        if decoder and (not decoder.eof or decoder.unused_data):
            raise ValueError("Invalid gzip document")
        return {} if allow_empty and not body else json.loads(body)
    except (ValueError, TypeError, zlib.error, RecursionError) as error:
        raise RelayError(400, "Supply a valid JSON document.") from error


def public_snapshot(snapshot, freshness, now):
    data = (
        copy.deepcopy(snapshot)
        if snapshot is not None
        else {
            "generated_at": None,
            "timezone": "UTC",
            "monitor": {"status": "stale", "last_cycle": None},
            "summary": {"total": 0, "healthy": 0, "attention": 0, "unknown": 0, "open_incidents": 0},
            "services": [],
            "projects": [],
            "selection_editable": False,
            "incidents": [],
            "incidents_limit": 200,
            "queries": [],
            "repairs": [],
            "automation": {},
        }
    )
    if snapshot is None or now - freshness > 60:
        data.setdefault("monitor", {})["status"] = "stale"
        summary = data.setdefault("summary", {})
        summary.update(healthy=0, attention=0, unknown=summary.get("total", len(data.get("services", []))))
        for service in data.get("services", []) + [
            s for p in data.get("projects", []) for s in p.get("services", [])
        ]:
            if service.get("enabled", True) and service.get("status") != "disabled":
                service.update(status="stale", latency_ms=None)
                for check in service.get("checks", []):
                    check.update(stale=True, healthy=None)
        for query in data.get("queries", []):
            query.update(stale=True, healthy=None)
    return data


def application(store, bridge_token: str, csrf_token: str, assets_path=None) -> FastAPI:
    """Build a relay with stable, separately provisioned machine and browser secrets."""
    if not bridge_token or not csrf_token or bridge_token == csrf_token:
        raise ValueError("Distinct bridge and CSRF secrets are required")
    assets = Path(assets_path) if assets_path is not None else ASSETS

    def authorized(request):
        return request.headers.get("origin") == f"{request.url.scheme}://{request.url.netloc}" and (
            secrets.compare_digest(request.headers.get("x-opswatch-csrf", "").encode(), csrf_token.encode())
        )

    async def overview(request: Request):
        return frontend_page(assets, csrf_token)

    async def asset(request: Request):
        return frontend_asset(assets, request.path_params["name"])

    async def snapshot(request: Request):
        data, freshness = await run_in_threadpool(store.read_snapshot)
        return JSONResponse(public_snapshot(data, freshness, store.clock()))

    async def incident(request: Request):
        ref = request.path_params["ref"]
        if not INCIDENT.fullmatch(ref):
            return Response(status_code=404)
        data, _ = await run_in_threadpool(store.read_snapshot)
        items = (data or {}).get("incidents", []) + (data or {}).get("repair_incidents", [])
        item = next((i for i in items if i.get("ref") == ref), None)
        return JSONResponse(item) if item else Response(status_code=404)

    async def icon(request: Request):
        target = request.path_params["target"]
        if not IDENTITY.fullmatch(target):
            return Response(status_code=404)
        cached = await run_in_threadpool(store.icon, target)
        if not cached:
            return Response(status_code=404)
        content, kind = cached
        if len(content) > 262144 or image_type(content) != kind:
            return Response(status_code=404)
        return Response(
            content,
            media_type=kind,
            headers={
                "Cache-Control": "private, max-age=86400",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            },
        )

    async def sync(request: Request):
        if not secrets.compare_digest(
            request.headers.get("authorization", "").encode(),
            f"Bearer {bridge_token}".encode(),
        ):
            raise RelayError(401, "Bridge authentication is required.")
        try:
            payload = validate_sync(await read_json(request, compressed=True))
        except (ValueError, TypeError, binascii.Error, RecursionError) as error:
            raise RelayError(400, "Invalid synchronization payload.") from error
        return JSONResponse(await run_in_threadpool(store.sync, payload))

    async def enqueue(request: Request):
        if not authorized(request):
            raise RelayError(403, "Reload the dashboard before changing monitoring or starting a fix.")
        kind = request.path_params["kind"] if "kind" in request.path_params else "fix"
        if kind not in {"fix", "services", "projects"}:
            return Response(status_code=404)
        target = request.path_params["target"]
        if not (INCIDENT if kind == "fix" else IDENTITY).fullmatch(target):
            return Response(status_code=404)
        payload = await read_json(request, 128, allow_empty=kind == "fix")
        if (
            not isinstance(payload, dict)
            or (kind == "fix" and payload)
            or (kind != "fix" and (set(payload) != {"enabled"} or type(payload.get("enabled")) is not bool))
        ):
            raise RelayError(400, "Supply only an enabled value of true or false.")
        identity = request.headers.get("x-opswatch-request-id")
        if identity is not None:
            try:
                identity = uuid_value(identity)
            except ValueError as error:
                raise RelayError(400, "Request ID must be a UUID.") from error
        command_id = await run_in_threadpool(store.enqueue, kind, target, payload, identity)
        return JSONResponse({"command_id": command_id}, status_code=202)

    async def command(request: Request):
        try:
            identity = uuid_value(request.path_params["identity"])
        except ValueError:
            return Response(status_code=404)
        data = await run_in_threadpool(store.command, identity)
        return JSONResponse(data) if data else Response(status_code=404)

    async def studio_state(request: Request):
        data, freshness = await run_in_threadpool(store.read_snapshot)
        studio = copy.deepcopy(data.get("studio")) if data else None
        if not isinstance(studio, dict):
            raise RelayError(503, "Agent workspace is waiting for an updated local bridge.")
        if store.clock() - freshness > 60:
            studio.setdefault("worker", {})["status"] = "stale"
            studio.setdefault("context", {})["stale"] = True
        return JSONResponse(studio)

    async def studio_action(request: Request):
        if not authorized(request):
            raise RelayError(403, "Reload the dashboard before changing the agent workspace.")
        try:
            action, payload, identity = await read_studio_action(request)
        except ValueError as error:
            raise RelayError(400, str(error)) from error
        command_id = await run_in_threadpool(
            store.enqueue, "studio", "workspace", {"action": action, "payload": payload}, identity
        )
        return JSONResponse({"command_id": command_id}, status_code=202)

    app = FastAPI(
        title="OpsWatch relay API", version="0.2.0", docs_url=None, redoc_url=None, openapi_url=None
    )
    for path in SPA_PATHS:
        app.add_api_route(path, overview, methods=["GET"], include_in_schema=False)
    for path, endpoint, methods in [
        ("/assets/{name:path}", asset, ["GET"]),
        ("/favicons/{target}", icon, ["GET"]),
        ("/api/snapshot", snapshot, ["GET"]),
        ("/api/incidents/{ref}", incident, ["GET"]),
        ("/api/bridge/sync", sync, ["POST"]),
        ("/api/incidents/{target}/fix", enqueue, ["POST"]),
        ("/api/{kind}/{target}/monitoring", enqueue, ["POST"]),
        ("/api/commands/{identity}", command, ["GET"]),
        ("/api/studio", studio_state, ["GET"]),
        ("/api/studio/actions", studio_action, ["POST"]),
    ]:
        app.add_api_route(path, endpoint, methods=methods, response_model=None)

    async def headers(request, call_next):
        try:
            response = await call_next(request)
        except RelayError as error:
            response = JSONResponse({"error": str(error)}, status_code=error.status)
            if error.status == 401:
                response.headers["WWW-Authenticate"] = "Bearer"
        except Exception:
            response = JSONResponse({"error": "Relay data is temporarily unavailable."}, status_code=503)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=headers)
    return app
