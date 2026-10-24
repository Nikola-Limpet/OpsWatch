from __future__ import annotations

import os
import re
from typing import Any

SECRET_KEY = re.compile(
    r"password|passwd|secret|api[_-]?key|token|authorization|cookie|dsn|connection.?string", re.I
)
PATTERNS = [
    (re.compile(r"(?i)(?:postgres(?:ql)?|mysql|redis)://[^\s\"<>]+"), "[database URL redacted]"),
    (re.compile(r"(?i)\b(?:bearer|basic)\s+[a-z0-9+/_.=:\-]+"), "[authorization redacted]"),
    (re.compile(r"(?i)([?&](?:token|key|password|secret|code|auth)[^=\s]*=)[^&\s]+"), r"\1[redacted]"),
    (
        re.compile(
            r"""(?i)(["'][^"'\\\r\n]*(?:password|passwd|secret|api[_-]?key|token|authorization|cookie|dsn|"""
            r"""connection.?string)[^"'\\\r\n]*["']\s*:\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')"""
        ),
        r'\1"[redacted]"',
    ),
    (
        re.compile(
            r"(?i)((?:password|passwd|secret|api[_-]?key|token|authorization|cookie)\s*[=:]\s*)[^\s,;]+"
        ),
        r"\1[redacted]",
    ),
    (re.compile(r"\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b"), "[email redacted]"),
    (re.compile(r"\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b"), "[JWT redacted]"),
]


def redact_text(text: str, limit: int = 2000) -> str:
    for key, value in os.environ.items():
        if len(value) >= 8 and SECRET_KEY.search(key):
            text = text.replace(value, "[redacted]")
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:limit]


def sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "[truncated]"
    if isinstance(value, dict):
        return {
            str(k): "[redacted]" if SECRET_KEY.search(str(k)) else sanitize(v, depth + 1)
            for k, v in list(value.items())[:30]
        }
    if isinstance(value, list):
        return [sanitize(v, depth + 1) for v in value[:20]]
    if isinstance(value, str):
        return redact_text(value)
    return value


def scrub_sql(query: str) -> str:
    # Query texts can contain literals even when pg_stat_statements normalizes most values.
    query = re.sub(r"\$([a-zA-Z_][a-zA-Z_0-9]*|)\$.*?\$\1\$", "?", query, flags=re.S)
    query = re.sub(r"'(?:''|[^'])*'", "?", query)
    query = re.sub(r"/\*.*?\*/|--[^\n]*", " ", query, flags=re.S)
    return redact_text(query, 1600)
