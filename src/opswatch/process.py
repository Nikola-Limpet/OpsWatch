from __future__ import annotations

import asyncio
import json
import os
import signal
from typing import Any

from .redact import redact_text

MAX_OUTPUT = 4 * 1024 * 1024


async def run(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 30,
    stdin: str | None = None,
    cwd: str | None = None,
) -> str:
    process = await asyncio.create_subprocess_exec(
        *argv,
        env=env or os.environ.copy(),
        cwd=cwd,
        start_new_session=True,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def read(stream):
        result = bytearray()
        while chunk := await stream.read(65536):
            result.extend(chunk)
            if len(result) > MAX_OUTPUT:
                raise RuntimeError(f"{argv[0]} exceeded the output limit")
        return result.decode("utf-8", errors="replace")

    async def execute():
        if stdin is not None:
            process.stdin.write(stdin.encode())
            await process.stdin.drain()
            process.stdin.close()
        stdout, stderr, code = await asyncio.gather(
            read(process.stdout), read(process.stderr), process.wait()
        )
        if code:
            raise RuntimeError(f"{argv[0]} exited {code}: {redact_text(stderr or stdout, 700)}")
        return stdout

    try:
        return await asyncio.wait_for(execute(), timeout)
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()


def parse_json(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Some CLIs print a progress label before their JSON document.
        decoder = json.JSONDecoder()
        for i, char in enumerate(text):
            if char in "[{":
                try:
                    value, end = decoder.raw_decode(text[i:])
                    if not text[i + end :].strip():
                        return value
                except json.JSONDecodeError:
                    pass
    raise ValueError("CLI returned no valid JSON document")


def parse_json_lines(text: str) -> list[dict]:
    try:
        value = json.loads(text)
        if isinstance(value, list):
            return [v for v in value if isinstance(v, dict)]
        if isinstance(value, dict):
            for key in ("logs", "entries", "data"):
                if isinstance(value.get(key), list):
                    return value[key]
            return [value]
    except (ValueError, TypeError):
        pass
    result = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                result.append(value)
        except json.JSONDecodeError:
            continue
    if text.strip() and not result:
        raise ValueError("log source returned nonempty output without JSON records")
    return result
