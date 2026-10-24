from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .config import Config
from .redact import sanitize
from .types import Observation


class Store:
    def __init__(self, data_dir: str):
        directory = Path(data_dir)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        self.db = sqlite3.connect(directory / "opswatch.sqlite3", timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS state (
                name TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS checks (
                target TEXT, key TEXT, kind TEXT, healthy INTEGER,
                failures INTEGER DEFAULT 0, successes INTEGER DEFAULT 0,
                title TEXT, evidence TEXT, checked REAL,
                PRIMARY KEY(target, key)
            );
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY, target TEXT, key TEXT, kind TEXT,
                severity TEXT, title TEXT, evidence TEXT, action TEXT, next_step TEXT,
                status TEXT, opened REAL, updated REAL, notified REAL, occurrences INTEGER DEFAULT 1
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_incident
                ON incidents(target, key) WHERE status = 'open';
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY, payload TEXT, created REAL,
                attempts INTEGER DEFAULT 0, retry_at REAL DEFAULT 0, last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, target TEXT, seen REAL, ip TEXT, path TEXT,
                status INTEGER, trusted INTEGER
            );
            CREATE INDEX IF NOT EXISTS events_window ON events(target, ip, seen);
            CREATE TABLE IF NOT EXISTS blocks (
                target TEXT, ip TEXT, expires REAL, state TEXT, detail TEXT,
                PRIMARY KEY(target, ip)
            );
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY, created REAL, target TEXT, action TEXT, detail TEXT
            );
            CREATE TABLE IF NOT EXISTS agent_runs (
                id INTEGER PRIMARY KEY, created REAL, backend TEXT, model TEXT, response TEXT
            );
        """)
        os.chmod(directory / "opswatch.sqlite3", 0o600)

    def close(self):
        self.db.close()

    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM state WHERE name=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value: Any):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value)))

    @staticmethod
    def incident_dict(row) -> dict:
        result = dict(row)
        result["evidence"] = json.loads(result["evidence"])
        result["ref"] = f"OW-{result['id']:04d}"
        return result

    def incident(self, ref: str) -> dict | None:
        try:
            number = int(ref.upper().removeprefix("OW-"))
        except ValueError:
            return None
        row = self.db.execute("SELECT * FROM incidents WHERE id=?", (number,)).fetchone()
        return self.incident_dict(row) if row else None

    def incidents(self, limit: int = 20, status: str | None = "open") -> list[dict]:
        if status:
            rows = self.db.execute(
                "SELECT * FROM incidents WHERE status=? ORDER BY updated DESC LIMIT ?", (status, limit)
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM incidents ORDER BY updated DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self.incident_dict(row) for row in rows]

    def checks(self) -> list[dict]:
        result = []
        for row in self.db.execute("SELECT * FROM checks ORDER BY target,key"):
            item = dict(row)
            item["evidence"] = json.loads(item["evidence"])
            result.append(item)
        return result

    def observe(self, item: Observation, config: Config, now: float | None = None) -> dict | None:
        now = time.time() if now is None else now
        evidence = json.dumps(sanitize(item.evidence))
        item.title = sanitize(item.title)
        item.action = sanitize(item.action)
        item.next_step = sanitize(item.next_step)
        with self.db:
            if item.dedup_key and self.get(item.dedup_key):
                return None
            previous = self.db.execute(
                "SELECT * FROM checks WHERE target=? AND key=?", (item.target, item.key)
            ).fetchone()
            failures = ((previous["failures"] if previous else 0) + 1) if item.healthy is False else 0
            successes = ((previous["successes"] if previous else 0) + 1) if item.healthy is True else 0
            self.db.execute(
                """INSERT OR REPLACE INTO checks
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    item.target,
                    item.key,
                    item.kind,
                    item.healthy,
                    failures,
                    successes,
                    item.title,
                    evidence,
                    now,
                ),
            )
            row = self.db.execute(
                "SELECT * FROM incidents WHERE target=? AND key=? AND status='open'", (item.target, item.key)
            ).fetchone()
            required = 1 if item.immediate else config.failures_before_alert
            notification = None
            if item.healthy is False and failures >= required:
                if row:
                    escalate = item.severity == "critical" and row["severity"] != "critical"
                    due = escalate or now - row["notified"] >= config.reminder_seconds
                    self.db.execute(
                        """UPDATE incidents SET updated=?, occurrences=occurrences+1,
                        title=?, severity=?, evidence=?, action=?, next_step=?, notified=? WHERE id=?""",
                        (
                            now,
                            item.title,
                            item.severity,
                            evidence,
                            item.action,
                            item.next_step,
                            now if due else row["notified"],
                            row["id"],
                        ),
                    )
                    number = row["id"]
                else:
                    cursor = self.db.execute(
                        """INSERT INTO incidents
                        (target,key,kind,severity,title,evidence,action,next_step,status,opened,updated,notified)
                        VALUES (?,?,?,?,?,?,?,?,'open',?,?,?)""",
                        (
                            item.target,
                            item.key,
                            item.kind,
                            item.severity,
                            item.title,
                            evidence,
                            item.action,
                            item.next_step,
                            now,
                            now,
                            now,
                        ),
                    )
                    number, due = cursor.lastrowid, True
                if due or config.telegram.notifications == "important":
                    notification = self.incident(str(number))
                    notification["event"] = "opened" if not row else "reminder"
            elif item.healthy is True and row and successes >= config.recoveries_before_alert:
                # A recovery describes a new observation, not the failure being investigated.
                failure = json.loads(row["evidence"])
                failure["recovery"] = json.loads(evidence)
                self.db.execute(
                    "UPDATE incidents SET status='resolved',updated=?,evidence=? WHERE id=?",
                    (now, json.dumps(failure), row["id"]),
                )
                notification = self.incident(str(row["id"]))
                notification["event"] = "resolved"
            if notification:
                from .notifications import Notifications

                if Notifications(config, self).eligible(notification, now, check_pending=True):
                    self.db.execute(
                        "INSERT INTO outbox(payload,created) VALUES (?,?)", (json.dumps(notification), now)
                    )
                else:
                    notification = None
            if item.dedup_key:
                self.db.execute(
                    "INSERT OR REPLACE INTO state VALUES (?,?)", (item.dedup_key, json.dumps(now))
                )
            for key, value in item.checkpoint.items():
                self.db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value)))
            return notification

    def pending(self) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                """SELECT p.* FROM outbox p WHERE p.retry_at<=? AND NOT EXISTS (
                    SELECT 1 FROM outbox e WHERE e.id<p.id
                    AND json_extract(e.payload,'$.id')=json_extract(p.payload,'$.id')
                ) ORDER BY p.id LIMIT 20""",
                (time.time(),),
            )
        ]

    def delivered(self, number: int, notification: dict | None = None):
        with self.db:
            self.db.execute("DELETE FROM outbox WHERE id=?", (number,))
            if notification:
                self.db.execute(
                    "INSERT OR REPLACE INTO state VALUES (?,?)",
                    (
                        "notification:" + notification["ref"],
                        json.dumps(
                            {
                                "sent_at": time.time(),
                                "severity": notification["severity"],
                                "recovered": notification.get("status") == "resolved",
                            }
                        ),
                    ),
                )

    def retry(self, number: int, error: str, delay: float = 0):
        with self.db:
            row = self.db.execute("SELECT attempts FROM outbox WHERE id=?", (number,)).fetchone()
            if row:
                self.db.execute(
                    "UPDATE outbox SET attempts=attempts+1,retry_at=?,last_error=? WHERE id=?",
                    (time.time() + max(delay, min(900, 2 ** min(row[0] + 3, 10))), sanitize(error), number),
                )

    def audit(self, target: str, action: str, detail: dict):
        with self.db:
            self.db.execute(
                "INSERT INTO audit(created,target,action,detail) VALUES (?,?,?,?)",
                (time.time(), target, action, json.dumps(sanitize(detail))),
            )

    def prune(self, retention_days: int):
        cutoff = time.time() - retention_days * 86400
        with self.db:
            self.db.execute("DELETE FROM events WHERE seen<?", (cutoff,))
            self.db.execute("DELETE FROM incidents WHERE status='resolved' AND updated<?", (cutoff,))
            self.db.execute("DELETE FROM audit WHERE created<?", (cutoff,))
            self.db.execute("DELETE FROM agent_runs WHERE created<?", (cutoff,))
            self.db.execute(
                "DELETE FROM state WHERE (name LIKE 'logseen:%' OR name LIKE 'sentryseen:%') "
                "AND CAST(value AS REAL)<?",
                (cutoff,),
            )
