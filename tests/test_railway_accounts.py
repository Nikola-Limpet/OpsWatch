import asyncio
import json
import time
from urllib.parse import parse_qs

import httpx
import pytest

from opswatch import connectors
from opswatch.config import Account


def test_cli_backed_railway_account_refuses_a_different_logged_in_user(tmp_path, monkeypatch):
    folder = tmp_path / ".railway"
    folder.mkdir()
    path = folder / "config.json"
    path.write_text(json.dumps({"user": {"id": "example-app-user"}}))
    monkeypatch.setattr(connectors.Path, "home", lambda: tmp_path)
    account = Account(name="second", cli_user_id="second-user")
    with pytest.raises(ValueError, match="different account"):
        connectors.account_env("railway", account)
    path.write_text(json.dumps({"user": {"id": "second-user"}}))
    assert "RAILWAY_API_TOKEN" not in connectors.account_env("railway", account)


async def test_saved_railway_account_is_used_instead_of_the_current_cli_login(tmp_path, monkeypatch):
    path = tmp_path / "example-app.json"
    path.write_text(
        json.dumps({"user": {"accessToken": "example-app-token", "tokenExpiresAt": time.time() + 600}})
    )
    account = Account(name="example-app", credentials_path=str(path))
    monkeypatch.setenv("RAILWAY_API_TOKEN", "another-account")
    monkeypatch.setenv("RAILWAY_TOKEN", "another-project")
    async with httpx.AsyncClient() as client:
        env = await connectors.railway_account_env(account, client)
    assert env["RAILWAY_API_TOKEN"] == "example-app-token"
    assert "RAILWAY_TOKEN" not in env


async def test_expired_railway_session_refreshes_once_and_saves_rotated_credentials(tmp_path):
    path = tmp_path / "example-app.json"
    path.write_text(
        json.dumps({"user": {"accessToken": "old", "refreshToken": "refresh-old", "tokenExpiresAt": 1}})
    )
    account = Account(name="example-app", credentials_path=str(path))
    calls = []

    def refresh(request):
        assert str(request.url) == "https://backboard.railway.com/oauth/token"
        assert parse_qs(request.content.decode())["grant_type"] == ["refresh_token"]
        assert parse_qs(request.content.decode())["refresh_token"] == ["refresh-old"]
        calls.append(request)
        return httpx.Response(
            200, json={"access_token": "new", "refresh_token": "refresh-new", "expires_in": 3600}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(refresh)) as client:
        envs = await asyncio.gather(*(connectors.railway_account_env(account, client) for _ in range(3)))
    assert all(env["RAILWAY_API_TOKEN"] == "new" for env in envs)
    assert len(calls) == 1
    saved = json.loads(path.read_text())["user"]
    assert saved["refreshToken"] == "refresh-new"
    assert saved["tokenExpiresAt"] > time.time()
    assert path.stat().st_mode & 0o777 == 0o600


async def test_railway_refresh_failure_preserves_credentials_and_does_not_expose_them(tmp_path):
    path = tmp_path / "example-app.json"
    original = json.dumps(
        {"user": {"accessToken": "secret-access", "refreshToken": "secret-refresh", "tokenExpiresAt": 1}}
    )
    path.write_text(original)
    account = Account(name="example-app", credentials_path=str(path))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(503, text="secret-refresh"))
    ) as client:
        with pytest.raises(ValueError, match="HTTP 503") as error:
            await connectors.railway_account_env(account, client)
    assert "secret" not in str(error.value)
    assert path.read_text() == original
