"""Notification relevance without discarding incident evidence."""

from __future__ import annotations

import time


def coverage_limit(item: dict) -> bool:
    evidence = item.get("evidence", {})
    count, limit = evidence.get("records"), evidence.get("limit")
    return (
        item.get("kind") == "monitoring"
        and isinstance(count, (int, float))
        and isinstance(limit, (int, float))
        and limit > 0
        and count >= limit
    )


class Notifications:
    def __init__(self, config, store):
        self.settings, self.store = config.telegram, store

    def eligible(self, item: dict, now: float | None = None, check_pending: bool = False) -> bool:
        if self.settings.notifications == "all":
            return True
        now = time.time() if now is None else now
        if coverage_limit(item):
            return False
        previous = self.store.get("notification:" + item["ref"], {})
        recovered = item.get("event") == "resolved" or item.get("status") == "resolved"
        if recovered:
            return bool(
                previous
                and not previous.get("recovered")
                and item["kind"] not in {"application error", "security"}
                and item["updated"] - item["opened"] >= self.settings.recovery_after_seconds
            )
        if item["severity"] != "critical" and now - item["opened"] < self.settings.warning_after_seconds:
            return False
        if (
            previous
            and not (item["severity"] == "critical" and previous.get("severity") != "critical")
            and now - previous["sent_at"] < self.settings.repeat_after_seconds
        ):
            return False
        if (
            check_pending
            and self.store.db.execute(
                "SELECT 1 FROM outbox WHERE json_extract(payload,'$.id')=? LIMIT 1", (item["id"],)
            ).fetchone()
        ):
            return False
        return True

    def diagnoses(self, item: dict) -> bool:
        if self.settings.notifications == "all":
            return True
        return item.get("kind") != "monitoring" and item.get("severity") == "critical"
