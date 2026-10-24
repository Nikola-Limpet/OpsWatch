"""Transactional Postgres relay storage. Construction never connects to the database.

Call ``PostgresRelay(dsn).initialize()`` once when provisioning the dedicated database.
The synchronous public methods are intended for Starlette's worker thread pool.
``sync`` atomically publishes display data, acknowledges results, and leases one command.
``enqueue`` validates against that same published snapshot under the singleton row lock.
"""

from __future__ import annotations

import copy
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .studio_protocol import READ_ACTIONS, validate_action

READ_RESULT_RETENTION = 600


class RelayError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class RelayData:
    snapshot: dict | None = None
    last_seen: float = 0
    published_at: float = 0
    commands: list[dict] = field(default_factory=list)
    icons: dict[str, tuple[bytes, str]] = field(default_factory=dict)


def expire(commands: list[dict], now: float):
    for command in commands:
        if command["state"] in {"pending", "running"} and command["expires_at"] <= now:
            command.update(state="expired", error="Command expired before completion.")
    # Studio history is durable locally. Relay read results are temporary transport
    # pages; mutation receipts remain available for idempotency without a time limit.
    commands[:] = [
        command
        for command in commands
        if not (
            command["kind"] == "studio"
            and command["payload"].get("action") in READ_ACTIONS
            and command["state"] not in {"pending", "running"}
            and command["expires_at"] + READ_RESULT_RETENTION <= now
        )
    ]


def validate_target(snapshot: dict, kind: str, target: str):
    if kind == "studio":
        if target != "workspace":
            raise RelayError(404, "Agent workspace not found.")
        return
    if kind == "fix":
        incidents = snapshot.get("incidents", []) + snapshot.get("repair_incidents", [])
        incident = next((i for i in incidents if i.get("ref") == target), None)
        if not incident:
            raise RelayError(404, "Incident not found.")
        if not incident.get("repair", {}).get("available"):
            raise RelayError(409, "A repair is unavailable for this incident.")
        return
    if kind == "projects":
        items = snapshot.get("projects", [])
    else:
        items = snapshot.get("services", []) + [
            service for project in snapshot.get("projects", []) for service in project.get("services", [])
        ]
    if not any(item.get("id") == target for item in items):
        raise RelayError(404, "Project or service not found.")
    if not snapshot.get("selection_editable"):
        raise RelayError(409, "Monitoring selection is unavailable.")


class PostgresRelay:
    """A small serialized queue, protected across serverless instances by one row lock.

    Public methods: read_snapshot()->(dict|None, freshness_epoch), icon(target),
    enqueue(kind,target,payload,request_id=None)->UUID string, command(UUID)->dict|None,
    and sync(validated_payload)->{acknowledged,command}. ``clock`` is injectable for
    deterministic lease testing; database I/O happens only inside method calls.
    """

    def __init__(self, dsn: str, *, clock=time.time):
        self.dsn = dsn
        self.clock = clock

    def _connect(self):
        return psycopg.connect(self.dsn, connect_timeout=10, row_factory=dict_row)

    def initialize(self):
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS opswatch_relay_state (
                    id smallint PRIMARY KEY CHECK (id=1), snapshot jsonb,
                    last_seen double precision NOT NULL DEFAULT 0,
                    published_at double precision NOT NULL DEFAULT 0
                )
            """)
            db.execute("INSERT INTO opswatch_relay_state(id) VALUES (1) ON CONFLICT DO NOTHING")
            db.execute("""
                CREATE TABLE IF NOT EXISTS opswatch_relay_commands (
                    id uuid PRIMARY KEY, sequence bigserial UNIQUE NOT NULL,
                    document jsonb NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS opswatch_relay_icons (
                    target text PRIMARY KEY, data bytea NOT NULL, content_type text NOT NULL
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS opswatch_relay_active_commands
                ON opswatch_relay_commands(sequence)
                WHERE document->>'state' IN ('pending','running')
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS opswatch_relay_terminal_commands
                ON opswatch_relay_commands((document->>'kind'),(document#>>'{payload,action}'))
                WHERE document->>'state' NOT IN ('pending','running')
            """)

    @contextmanager
    def _transaction(self, identities=()):
        with self._connect() as db:
            row = db.execute("SELECT * FROM opswatch_relay_state WHERE id=1 FOR UPDATE").fetchone()
            if not row:
                raise RuntimeError("Relay schema is not initialized")
            db.execute(
                "DELETE FROM opswatch_relay_commands WHERE document->>'kind'='studio' "
                "AND document#>>'{payload,action}'=ANY(%s::text[]) "
                "AND document->>'state' NOT IN ('pending','running') "
                "AND (document->>'expires_at')::double precision<=%s",
                (sorted(READ_ACTIONS), self.clock() - READ_RESULT_RETENTION),
            )
            data = RelayData(
                snapshot=row["snapshot"],
                last_seen=row["last_seen"],
                published_at=row["published_at"],
                commands=[
                    row["document"]
                    for row in db.execute(
                        "SELECT document FROM opswatch_relay_commands "
                        "WHERE document->>'state' IN ('pending','running') OR id=ANY(%s::uuid[]) "
                        "ORDER BY sequence",
                        (list(identities),),
                    )
                ],
            )
            before = copy.deepcopy(data)
            yield data
            if (data.snapshot, data.last_seen, data.published_at) != (
                before.snapshot,
                before.last_seen,
                before.published_at,
            ):
                db.execute(
                    "UPDATE opswatch_relay_state SET snapshot=%s,last_seen=%s,published_at=%s WHERE id=1",
                    (Jsonb(data.snapshot), data.last_seen, data.published_at),
                )
            old_commands = {c["id"]: c for c in before.commands}
            removed = old_commands.keys() - {c["id"] for c in data.commands}
            if removed:
                db.execute("DELETE FROM opswatch_relay_commands WHERE id=ANY(%s::uuid[])", (list(removed),))
            for command in data.commands:
                if command != old_commands.get(command["id"]):
                    db.execute(
                        "INSERT INTO opswatch_relay_commands(id,document) VALUES (%s,%s) "
                        "ON CONFLICT(id) DO UPDATE SET document=excluded.document",
                        (command["id"], Jsonb(command)),
                    )
            for target, (content, content_type) in data.icons.items():
                if data.icons[target] != before.icons.get(target):
                    db.execute(
                        "INSERT INTO opswatch_relay_icons(target,data,content_type) VALUES (%s,%s,%s) "
                        "ON CONFLICT(target) DO UPDATE SET "
                        "data=excluded.data,content_type=excluded.content_type",
                        (target, content, content_type),
                    )

    def read_snapshot(self):
        with self._connect() as db:
            row = db.execute(
                "SELECT snapshot,last_seen,published_at FROM opswatch_relay_state WHERE id=1"
            ).fetchone()
            if not row:
                raise RuntimeError("Relay schema is not initialized")
            return row["snapshot"], min(row["last_seen"], row["published_at"])

    def icon(self, target: str):
        with self._connect() as db:
            row = db.execute(
                "SELECT data,content_type FROM opswatch_relay_icons WHERE target=%s",
                (target,),
            ).fetchone()
            return (bytes(row["data"]), row["content_type"]) if row else None

    def enqueue(self, kind: str, target: str, payload: dict, request_id: str | None = None):
        if kind not in {"fix", "services", "projects", "studio"}:
            raise RelayError(400, "Unknown command kind.")
        if kind == "studio":
            try:
                if not isinstance(payload, dict) or set(payload) != {"action", "payload"}:
                    raise ValueError
                payload = {
                    "action": payload["action"],
                    "payload": validate_action(payload["action"], payload["payload"]),
                }
            except (ValueError, TypeError) as error:
                raise RelayError(400, "Invalid agent workspace action.") from error
        elif (kind == "fix" and payload != {}) or (
            kind != "fix" and (set(payload) != {"enabled"} or type(payload["enabled"]) is not bool)
        ):
            raise RelayError(400, "Invalid command payload.")
        command_id = request_id or str(uuid4())
        with self._transaction([command_id]) as data:
            now = self.clock()
            expire(data.commands, now)
            existing = next((c for c in data.commands if c["id"] == command_id), None)
            if existing:
                if (existing["kind"], existing["target"], existing["payload"]) != (kind, target, payload):
                    raise RelayError(409, "Request ID is already used by another action.")
                return command_id
            if not data.snapshot or now - min(data.last_seen, data.published_at) > 60:
                raise RelayError(503, "The local bridge is offline. Try again when it reconnects.")
            validate_target(data.snapshot, kind, target)
            if sum(c["state"] in {"pending", "running"} for c in data.commands) >= 100:
                raise RelayError(429, "The command queue is full. Try again shortly.")
            data.commands.append(
                {
                    "id": command_id,
                    "kind": kind,
                    "target": target,
                    "payload": copy.deepcopy(payload),
                    "expires_at": now + 120,
                    "lease_until": 0,
                    "state": "pending",
                    "result": None,
                    "error": None,
                }
            )
            return command_id

    def command(self, identity: str):
        with self._transaction([identity]) as data:
            expire(data.commands, self.clock())
            command = next((c for c in data.commands if c["id"] == identity), None)
            return {key: command[key] for key in ("id", "state", "result", "error")} if command else None

    def sync(self, payload: dict):
        with self._transaction([result["id"] for result in payload.get("results", [])]) as data:
            now = self.clock()
            data.last_seen = now
            if "snapshot" in payload:
                data.snapshot = copy.deepcopy(payload["snapshot"])
                data.published_at = now
            for icon in payload.get("icons", []):
                data.icons[icon["target"]] = (icon["data"], icon["content_type"])
            expire(data.commands, now)
            acknowledged = []
            commands = {c["id"]: c for c in data.commands}
            for result in payload.get("results", []):
                command = commands.get(result["id"])
                if not command or command["state"] == "pending":
                    continue
                if command["state"] == "running":
                    command.update(
                        state="error" if result.get("error") is not None else "done",
                        result=copy.deepcopy(result.get("result")),
                        error=result.get("error"),
                    )
                if command["id"] not in acknowledged:
                    acknowledged.append(command["id"])
            head = next((c for c in data.commands if c["state"] in {"pending", "running"}), None)
            command = None
            if head and head["lease_until"] <= now:
                head.update(state="running", lease_until=now + 30)
                command = {key: head[key] for key in ("id", "kind", "target", "payload", "expires_at")}
            return {"acknowledged": acknowledged, "command": copy.deepcopy(command)}
