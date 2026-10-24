"""Strict, dependency-free Studio action contract shared by local and cloud APIs.

Validation never resolves application identities or performs I/O. The local Studio
checks current configuration and ownership after this transport-level validation.
"""

from __future__ import annotations

import json
import math
import re
from uuid import UUID

ROLES = {"chat", "report", "improvement"}
PLAYBOOKS = {"investigation", "bug-fix", "feature", "refactoring", "perf-issue", "reflection"}
PROBLEM_STATES = {"proposed", "accepted", "dismissed", "in_progress", "done"}
READ_ACTIONS = {"session_read", "report_read", "collection_read"}
ACTION_FIELDS = {
    "session_create": {"title", "role", "scope", "playbook"},
    "session_read": {"session_id", "before", "limit"},
    "collection_read": {"collection", "before", "limit"},
    "session_update": {"session_id", "title", "archived"},
    "message_send": {"session_id", "content"},
    "report_create": {"scope", "period", "window_start", "window_end", "prompt", "title"},
    "report_read": {"report_id"},
    "improvement_create": {"scope", "report_id", "focus", "title"},
    "preferences_update": {"revision", "principles", "stack", "review_focus", "verification", "notes"},
    "problem_update": {"problem_id", "status", "notes", "revision"},
    "feedback": {"subject_type", "subject_id", "rating", "comment"},
    "job_cancel": {"job_id"},
}
IDENTITY = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}\Z")


def uuid_value(value) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError("An identity must be a UUID.")
    try:
        return str(UUID(value))
    except ValueError as error:
        raise ValueError("An identity must be a UUID.") from error


def text_value(value, name: str, limit: int, *, empty=False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text.")
    value = value.strip()
    if (
        len(value) > limit
        or (not empty and not value)
        or any(ord(char) < 32 and char not in "\n\r\t" for char in value)
    ):
        raise ValueError(f"{name} must contain {'0' if empty else '1'} to {limit} characters.")
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise ValueError(f"{name} contains invalid text.") from error
    return value


def scope_value(value) -> dict:
    if not isinstance(value, dict) or set(value) != {"kind", "id"}:
        raise ValueError("Scope must contain a kind and an id.")
    kind, identity = value["kind"], value["id"]
    if kind == "workspace" and identity is None:
        return {"kind": kind, "id": None}
    if (
        not isinstance(kind, str)
        or kind not in {"project", "service"}
        or not isinstance(identity, str)
        or not IDENTITY.fullmatch(identity)
    ):
        raise ValueError("Scope must identify a workspace, project, or service.")
    return {"kind": kind, "id": identity}


def positive_integer(value, name: str, maximum=2**31 - 1) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be a positive integer no greater than {maximum}.")
    return value


def validate_action(action: str, payload: dict) -> dict:
    """Return a new normalized payload, or raise ValueError for unsupported input."""
    if not isinstance(action, str) or action not in ACTION_FIELDS:
        raise ValueError("Unknown Studio action.")
    if not isinstance(payload, dict) or not set(payload) <= ACTION_FIELDS[action]:
        raise ValueError("The action contains unsupported fields.")
    try:
        if len(json.dumps(payload, allow_nan=False).encode()) > 49152:
            raise ValueError("The action is too large.")
    except (TypeError, OverflowError, RecursionError) as error:
        raise ValueError("Supply a valid action document.") from error
    result = dict(payload)
    for name in ("session_id", "report_id", "problem_id", "subject_id", "job_id", "before"):
        if name in result:
            result[name] = uuid_value(result[name])
    required = {
        "session_read": {"session_id"},
        "collection_read": {"collection"},
        "session_update": {"session_id"},
        "message_send": {"session_id", "content"},
        "report_read": {"report_id"},
        "preferences_update": {"revision"},
        "problem_update": {"problem_id"},
        "feedback": {"subject_type", "subject_id", "rating"},
        "job_cancel": {"job_id"},
    }.get(action, set())
    if not required <= result.keys():
        raise ValueError("The action is missing required fields.")
    if action in {"session_create", "report_create", "improvement_create"}:
        result["scope"] = scope_value(result.get("scope", {"kind": "workspace", "id": None}))
    if action == "session_create":
        result["title"] = text_value(result.get("title", "New conversation"), "Title", 120)
        result["role"] = result.get("role", "chat")
        if not isinstance(result["role"], str) or result["role"] not in ROLES:
            raise ValueError("Choose a chat, report, or improvement agent.")
        result["playbook"] = result.get(
            "playbook", "reflection" if result["role"] == "improvement" else "investigation"
        )
        if not isinstance(result["playbook"], str) or result["playbook"] not in PLAYBOOKS:
            raise ValueError("Choose a supported pstack playbook.")
    elif action == "session_read":
        result["limit"] = positive_integer(result.get("limit", 20), "Page size", 20)
    elif action == "collection_read":
        if not isinstance(result["collection"], str) or result["collection"] not in {
            "sessions",
            "reports",
            "problems",
        }:
            raise ValueError("Choose a session, report, or problem collection.")
        result["limit"] = positive_integer(result.get("limit", 20), "Page size", 20)
    elif action == "session_update":
        if set(result) == {"session_id"}:
            raise ValueError("Supply a title or archived state.")
        if "title" in result:
            result["title"] = text_value(result["title"], "Title", 120)
        if "archived" in result and type(result["archived"]) is not bool:
            raise ValueError("Archived must be true or false.")
    elif action == "message_send":
        result["content"] = text_value(result["content"], "Message", 8000)
    elif action == "report_create":
        result["period"] = result.get("period", "day")
        if not isinstance(result["period"], str) or result["period"] not in {"day", "week", "custom"}:
            raise ValueError("Choose a day, week, or custom reporting period.")
        if result["period"] == "custom":
            start, end = result.get("window_start"), result.get("window_end")
            if any(type(value) not in {int, float} or not math.isfinite(value) for value in (start, end)):
                raise ValueError("A custom report needs finite start and end timestamps.")
            if not 0 <= start < end or end - start > 31 * 86400:
                raise ValueError("The report window must cover no more than 31 days.")
        elif "window_start" in result or "window_end" in result:
            raise ValueError("Explicit timestamps require a custom reporting period.")
        result["prompt"] = text_value(result.get("prompt", ""), "Report instructions", 4000, empty=True)
        result["title"] = text_value(result.get("title", "Operations report"), "Title", 120)
    elif action == "improvement_create":
        result["focus"] = text_value(result.get("focus", ""), "Improvement focus", 4000, empty=True)
        result["title"] = text_value(result.get("title", "Reliability improvements"), "Title", 120)
    elif action == "preferences_update":
        result["revision"] = positive_integer(result["revision"], "Preference revision")
        if set(result) == {"revision"}:
            raise ValueError("Supply at least one preference to update.")
        for name in ("principles", "stack", "review_focus", "verification"):
            if name in result:
                if not isinstance(result[name], list) or len(result[name]) > 20:
                    raise ValueError("Preference lists may contain no more than 20 values.")
                result[name] = [text_value(value, name, 300) for value in result[name]]
        if "notes" in result:
            result["notes"] = text_value(result["notes"], "Preference notes", 4000, empty=True)
    elif action == "problem_update":
        if not set(result) & {"status", "notes"}:
            raise ValueError("Supply a problem status or notes.")
        if "status" in result and (
            not isinstance(result["status"], str) or result["status"] not in PROBLEM_STATES
        ):
            raise ValueError("Unknown problem status.")
        if "notes" in result:
            result["notes"] = text_value(result["notes"], "Problem notes", 4000, empty=True)
            if len(json.dumps(result["notes"]).encode()) > 24576:
                raise ValueError(
                    "Problem notes exceed the encoded text limit. Shorten the notes before saving."
                )
        if "revision" in result:
            result["revision"] = positive_integer(result["revision"], "Problem revision")
    elif action == "feedback":
        if not isinstance(result["subject_type"], str) or result["subject_type"] not in {
            "message",
            "report",
            "problem",
        }:
            raise ValueError("Feedback must refer to a message, report, or problem.")
        if not isinstance(result["rating"], str) or result["rating"] not in {"helpful", "unhelpful"}:
            raise ValueError("Choose helpful or unhelpful feedback.")
        result["comment"] = text_value(result.get("comment", ""), "Feedback", 2000, empty=True)
    return result
