from __future__ import annotations

import hashlib
import hmac
import json
import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .config import secret
from .connectors import parse_logs
from .selection import refresh_selection


def application(service) -> Starlette:
    async def health(request: Request):
        refresh_selection(service.config)
        last = service.store.get("heartbeat", 0)
        enabled = [t for t in service.config.targets if t.enabled]
        limit = max([t.interval_seconds for t in enabled], default=60) * 3 + 30
        live = bool(last and time.time() - last < limit)
        return JSONResponse(
            {"monitor": "running" if live else "stale", "last_cycle": last}, status_code=200 if live else 503
        )

    async def events(request: Request):
        refresh_selection(service.config)
        target = next(
            (
                t
                for t in service.config.targets
                if t.id == request.path_params["target_id"] and t.enabled and t.ingest_secret_env
            ),
            None,
        )
        if not target:
            return JSONResponse({"error": "unknown event source"}, status_code=404)
        timestamp = request.headers.get("x-opswatch-timestamp", "")
        signature = request.headers.get("x-opswatch-signature", "")
        try:
            if abs(time.time() - int(timestamp)) > 60:
                raise ValueError("expired")
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > 256 * 1024:
                    return JSONResponse({"error": "batch too large"}, status_code=413)
            expected = hmac.new(
                secret(target.ingest_secret_env).encode(), timestamp.encode() + b"." + data, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("invalid signature")
        except (ValueError, TypeError):
            return JSONResponse({"error": "authentication failed"}, status_code=401)
        refresh_selection(service.config)
        if not target.enabled:
            return JSONResponse({"error": "event source monitoring is disabled"}, status_code=404)
        try:
            records = json.loads(data)
            if not isinstance(records, list) or len(records) > 100:
                raise ValueError("expected up to 100 log records")
            if not all(isinstance(r, dict) for r in records):
                raise ValueError("each record must be an object")
            observations, access = parse_logs(target, records, service.store)
        except (ValueError, TypeError):
            return JSONResponse({"error": "invalid event batch"}, status_code=400)
        for event in access:
            refresh_selection(service.config)
            if not target.enabled:
                return JSONResponse({"error": "event source monitoring is disabled"}, status_code=404)
            # A verified signature identifies the configured producer. The producer must
            # resolve real IPs at its trusted reverse proxy, never from an arbitrary header.
            event.trusted = True
            item = await service.security.inspect(target, event)
            if item:
                observations.append(item)
        for item in observations:
            refresh_selection(service.config)
            if not target.enabled:
                return JSONResponse({"error": "event source monitoring is disabled"}, status_code=404)
            service.store.observe(item, service.config)
        return JSONResponse({"accepted": len(records)})

    return Starlette(
        routes=[Route("/healthz", health), Route("/events/{target_id}", events, methods=["POST"])]
    )
