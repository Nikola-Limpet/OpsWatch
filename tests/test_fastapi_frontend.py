import re
from pathlib import Path

import httpx
import pytest

from opswatch.config import Config
from opswatch.dashboard import application


def test_local_dashboard_is_a_fastapi_application_with_documented_routes(tmp_path):
    from fastapi import FastAPI

    app = application(Config(data_dir=str(tmp_path / "data")))
    assert isinstance(app, FastAPI)
    paths = app.openapi()["paths"]
    assert "/api/snapshot" in paths
    assert "/api/csrf" in paths
    assert "/api/services/{identity}/monitoring" in paths
    assert "/api/studio" in paths
    assert "/api/studio/actions" in paths


@pytest.fixture
def frontend(tmp_path, monkeypatch):
    from opswatch import dashboard

    assets = tmp_path / "assets"
    (assets / "web" / "assets" / "chunks").mkdir(parents=True)
    (assets / "web" / "index.html").write_text(
        '<html><head><meta name="opswatch-csrf" content="__OPSWATCH_CSRF__"></head>'
        '<body><div id="root">OpsWatch React</div><script src="/assets/app.js"></script></body></html>'
    )
    (assets / "web" / "assets" / "app.js").write_text("/* compiled react entry */")
    (assets / "web" / "assets" / "chunks" / "chat-a1b2.js").write_text("/* lazy chat */")
    (assets / "web" / "assets" / "leak.map").write_text("private source map")
    (assets / "web" / "assets" / "secret.json").write_text("private source data")
    (assets / "web" / "assets" / "THIRD-PARTY-LICENSES.txt").write_text("MIT notices")
    (assets / "web" / "assets" / "private.txt").write_text("private notes")
    (assets / "geist-sans.woff2").write_bytes(b"font")
    monkeypatch.setattr(dashboard, "ASSETS", assets)
    return Config(data_dir=str(tmp_path / "monitor"))


async def test_react_routes_and_assets_preserve_csrf_and_reject_arbitrary_files(frontend):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(frontend)), base_url="http://localhost"
    ) as client:
        for route in [
            "/",
            "/projects",
            "/incidents",
            "/agents",
            "/queries",
            "/studio",
            "/reports",
            "/workflow",
        ]:
            page = await client.get(route)
            assert page.status_code == 200
            assert "OpsWatch React" in page.text
            assert "__OPSWATCH_CSRF__" not in page.text
            assert re.search(r'name="opswatch-csrf" content="[A-Za-z0-9_-]+"', page.text)
        assert (await client.get("/assets/app.js")).text == "/* compiled react entry */"
        assert (await client.get("/assets/chunks/chat-a1b2.js")).status_code == 200
        assert (await client.get("/assets/geist-sans.woff2")).status_code == 200
        assert (await client.get("/assets/THIRD-PARTY-LICENSES.txt")).text == "MIT notices"
        for route in [
            "/.env",
            "/unknown",
            "/assets/leak.map",
            "/assets/secret.json",
            "/assets/.env",
            "/assets/private.txt",
        ]:
            assert (await client.get(route)).status_code == 404
    assert not Path(frontend.data_dir).exists()


async def test_development_bootstrap_uses_the_same_csrf_token_without_creating_runtime_data(tmp_path):
    config = Config(data_dir=str(tmp_path / "monitor"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(config)), base_url="http://localhost:5173"
    ) as client:
        response = await client.get("/api/csrf")
        token = response.json()["token"]
        assert response.headers["cache-control"] == "no-store"
        assert token and token != "__OPSWATCH_CSRF__"
        assert not Path(config.data_dir).exists()
        saved = await client.post(
            "/api/studio/actions",
            json={"action": "session_create", "payload": {}},
            headers={"Origin": "http://localhost:5173", "X-OpsWatch-CSRF": token},
        )
        assert saved.status_code == 200
        rejected = await client.post(
            "/api/studio/actions",
            json={"action": "session_create", "payload": {}},
            headers={"Origin": "https://unrelated.example", "X-OpsWatch-CSRF": token},
        )
        assert rejected.status_code == 403


async def test_studio_get_handles_a_saved_configuration_becoming_invalid(tmp_path):
    from opswatch.config import load_config

    source = tmp_path / "config.yaml"
    source.write_text("targets: []\ndata_dir: runtime\n")
    config = load_config(source)
    app = application(config)
    source.write_text("targets: invalid")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        response = await client.get("/api/studio")
        assert response.status_code == 503
        assert "temporarily unavailable" in response.json()["error"]
        assert not Path(config.data_dir).exists()
