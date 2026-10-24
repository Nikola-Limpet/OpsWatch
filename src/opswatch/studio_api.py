"""Small shared HTTP envelope for local and relay Studio actions."""

from __future__ import annotations

import json
from uuid import UUID, uuid4


async def read_studio_action(request) -> tuple[str, dict, str]:
    from .studio_protocol import validate_action

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 65536:
            raise ValueError("The Studio action is too large.")
    try:
        document = json.loads(body)
    except (ValueError, TypeError, RecursionError) as error:
        raise ValueError("Supply a valid Studio action.") from error
    if not isinstance(document, dict) or set(document) != {"action", "payload"}:
        raise ValueError("Supply only action and payload.")
    action, payload = document["action"], document["payload"]
    payload = validate_action(action, payload)
    identity = request.headers.get("x-opswatch-request-id") or str(uuid4())
    try:
        identity = str(UUID(identity))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Request ID must be a UUID.") from error
    return action, payload, identity
