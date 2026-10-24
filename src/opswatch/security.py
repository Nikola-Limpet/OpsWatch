from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
import time
from urllib.parse import unquote, urlsplit

import httpx

from .config import Config, Target, secret
from .process import run
from .railway_edge import RailwayEdge
from .redact import redact_text
from .storage import Store
from .types import AccessEvent, Observation

PROBE = re.compile(
    r"(?:^|/)(?:\.env(?:[./]|$)|\.git(?:/|$)|wp-login\.php$|xmlrpc\.php$|phpmyadmin(?:/|$)|"
    r"vendor/phpunit(?:/|$)|actuator/(?:env|heapdump)|etc/passwd|proc/self/environ)|"
    r"(?:\.\./){2,}",
    re.I,
)


def scan_path(path: str) -> str | None:
    decoded = unquote(unquote(urlsplit(path).path)).lower()
    return decoded[:200] if PROBE.search(decoded) else None


class Security:
    def __init__(self, config: Config, store: Store, client: httpx.AsyncClient):
        self.config, self.store, self.client = config, store, client
        self.locks: dict[str, asyncio.Lock] = {}
        self.railway_edge = RailwayEdge(config, client)

    def permitted(self, ip: str) -> bool:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return address.is_global and not any(
            address in ipaddress.ip_network(cidr) for cidr in self.config.policy.allow_cidrs
        )

    async def inspect(self, target: Target, event: AccessEvent) -> Observation | None:
        now = time.time()
        policy = self.config.policy
        path = scan_path(event.path)
        if not path or event.target != target.id or (event.client_ip and not self.permitted(event.client_ip)):
            return None
        if event.timestamp < now - policy.scan_window_seconds or event.timestamp > now + 30:
            return None
        unique = hashlib.sha256(f"{target.id}:{event.event_id}".encode()).hexdigest()
        with self.store.db:
            cursor = self.store.db.execute(
                "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?)",
                (unique, target.id, event.timestamp, event.client_ip, path, event.status, event.trusted),
            )
            if not cursor.rowcount:
                return None
            rows = self.store.db.execute(
                """SELECT path,status,seen FROM events
                WHERE target=? AND ip=? AND seen>=? AND trusted=?""",
                (target.id, event.client_ip, now - policy.scan_window_seconds, event.trusted),
            ).fetchall()
        distinct = sorted({row["path"] for row in rows})
        if len(rows) < policy.scan_min_requests or len(distinct) < policy.scan_min_paths:
            return None
        evidence = {
            "client_ip": event.client_ip or "unavailable; evidence is aggregated across this target",
            "requests": len(rows),
            "distinct_probe_paths": distinct[:10],
            "window_seconds": policy.scan_window_seconds,
            "trusted_source": event.trusted,
            "first_request": min(row["seen"] for row in rows),
            "last_request": max(row["seen"] for row in rows),
        }
        action = "Alert only. Automatic blocking is disabled."
        if policy.auto_block:
            if not event.client_ip:
                action = (
                    "Alert only. Request logs did not include a client IP. Configure trusted edge events."
                )
            elif not event.trusted:
                action = "Alert only. Client IP was not supplied by a trusted access-log source."
            elif target.blocker.kind == "none":
                action = "Alert only. This target has no configured firewall adapter."
            elif policy.dry_run:
                if target.blocker.permanent:
                    action = (
                        f"Dry run. Would block {event.client_ip} at Railway's edge until manually unblocked."
                    )
                else:
                    action = f"Dry run. Would block {event.client_ip} for {policy.block_ttl_seconds} seconds."
            else:
                try:
                    action = await self.block(target, event.client_ip)
                except Exception as error:
                    action = f"Blocking failed: {redact_text(str(error), 400)}"
                    self.store.audit(target.id, "block_failed", {"ip": event.client_ip, "error": str(error)})
        return Observation(
            target.id,
            "scan:" + (event.client_ip or "unattributed"),
            "security",
            False,
            "Repeated vulnerability probes detected",
            evidence,
            "critical" if event.client_ip else "warning",
            True,
            action,
            "Review paths and IP evidence. Keep your own IPs and proxy ranges in allow_cidrs. Use /diagnose.",
        )

    async def block(self, target: Target, ip: str) -> str:
        lock = self.locks.setdefault(target.id + ":" + ip, asyncio.Lock())
        async with lock:
            return await self._block(target, ip)

    async def _block(self, target: Target, ip: str) -> str:
        if not self.permitted(ip):
            raise ValueError("IP is private, reserved, invalid, or allowlisted")
        existing = self.store.db.execute(
            "SELECT * FROM blocks WHERE target=? AND ip=?", (target.id, ip)
        ).fetchone()
        permanent = target.blocker.kind == "railway-edge"
        if (
            not permanent
            and existing
            and existing["state"] == "blocked"
            and existing["expires"] > time.time()
        ):
            return f"Already blocked until {int(existing['expires'])}."
        ttl = self.config.policy.block_ttl_seconds
        expires = 0 if permanent else time.time() + ttl
        payload = {
            "action": "block",
            "target": target.id,
            "ip": ip,
            "ttl_seconds": ttl,
            "expires_at": expires,
        }
        if permanent:
            payload.update(permanent=True, expires_at=None)
            payload.pop("ttl_seconds")
        self.store.audit(target.id, "block_requested", payload)
        if permanent:
            await self.railway_edge.block(target, ip)
        elif target.blocker.kind == "webhook":
            response = await self.client.post(
                target.blocker.url,
                headers={"Authorization": "Bearer " + secret(target.blocker.token_env)},
                json=payload,
                timeout=15,
            )
            response.raise_for_status()
            result = response.json()
            if result.get("status") != "blocked" or result.get("ttl_enforced") is not True:
                raise ValueError("firewall must acknowledge blocked and ttl_enforced=true")
        elif target.blocker.kind == "ssh-nft":
            host = target.blocker.ssh_host
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]*", host):
                raise ValueError("invalid SSH host alias")
            await run(
                [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=10",
                    host,
                    "sudo -n /usr/local/sbin/opswatch-block",
                ],
                stdin=json.dumps(payload),
                timeout=20,
            )
        else:
            raise ValueError("no firewall adapter configured")
        with self.store.db:
            self.store.db.execute(
                "INSERT OR REPLACE INTO blocks VALUES (?,?,?,?,?)",
                (target.id, ip, expires, "blocked", json.dumps(payload)),
            )
        self.store.audit(target.id, "blocked", payload)
        if permanent:
            return f"Blocked {ip} at Railway's edge until manually unblocked."
        return f"Blocked {ip} for {ttl} seconds. The firewall expires the block automatically."
