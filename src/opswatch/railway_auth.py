from __future__ import annotations

import asyncio
import fcntl
import json
import os
import tempfile
import time
from pathlib import Path

import httpx

CLI_CLIENT_ID = "rlwy_oaci_onEklvmksh1hRUiCo7E2zX12"


async def saved_account_token(path: str, client: httpx.AsyncClient) -> str:
    credentials = Path(path)
    lock_fd = os.open(str(credentials) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock_fd, "w") as lock:
        deadline = time.monotonic() + 15
        while True:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Saved Railway session is busy") from None
                await asyncio.sleep(0.1)
        profile = json.loads(credentials.read_text())
        user = profile["user"]
        token = user.get("accessToken") or user.get("token")
        expires = user.get("tokenExpiresAt")
        if token and (not expires or float(expires) > time.time() + 60):
            return token
        refresh_token = user.get("refreshToken")
        if not refresh_token:
            raise ValueError("Saved Railway session has expired; reconnect this account")
        try:
            response = await client.post(
                "https://backboard.railway.com/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": profile.get("client_id", CLI_CLIENT_ID),
                },
                timeout=20,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise ValueError("Railway session refresh failed: network error") from None
        if not response.is_success:
            raise ValueError(f"Railway session refresh failed: HTTP {response.status_code}")
        try:
            result = response.json()
            token = result["access_token"]
            expires_in = float(result["expires_in"])
            if not isinstance(token, str) or not token or expires_in <= 0:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise ValueError("Railway session refresh returned invalid credentials") from None
        user.update(
            accessToken=token,
            refreshToken=result.get("refresh_token") or refresh_token,
            tokenExpiresAt=time.time() + expires_in,
            token=None,
        )
        fd, temporary = tempfile.mkstemp(dir=credentials.parent, prefix=".railway-session-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(profile, stream)
            os.replace(temporary, credentials)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return token
