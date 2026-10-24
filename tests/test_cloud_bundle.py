import importlib.util
import json
from pathlib import Path


def test_vercel_static_output_exists_without_bypassing_fastapi_csrf_html():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "vercel.json").read_text())
    output = root / config.get("outputDirectory", "public")
    assert output.is_dir(), "Vercel requires its configured static output directory after the build"
    assert not list(output.rglob("*.html")), (
        "FastAPI must inject the CSRF token before serving application HTML"
    )
    assert (root / "src/opswatch/dashboard_assets/web/index.html").is_file()
    assert config["rewrites"] == [{"source": "/(.*)", "destination": "/api/index"}]


def test_cloud_bundle_contains_only_public_sources_and_assets(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "build-cloud-dashboard.py"
    spec = importlib.util.spec_from_file_location("cloud_bundle", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "src" / "opswatch"
    source.mkdir(parents=True)
    for name in (
        "cloud_relay.py",
        "cloud_store.py",
        "favicons.py",
        "web_assets.py",
        "studio_api.py",
        "studio_protocol.py",
    ):
        (source / name).write_text("# public application source\n")
    assets = source / "dashboard_assets"
    assets.mkdir()
    for name in (
        "geist-sans.woff2",
        "geist-mono.woff2",
        "FONT-LICENSE.txt",
    ):
        (assets / name).write_text("public asset")
    (assets / "web" / "assets" / "chunks").mkdir(parents=True)
    (assets / "web" / "index.html").write_text("public React index")
    (assets / "web" / "assets" / "app.js").write_text("public compiled React")
    (assets / "web" / "assets" / "THIRD-PARTY-LICENSES.txt").write_text("MIT notices")
    (assets / "web" / "assets" / "chunks" / "chat-123.js").write_text("public lazy chat")
    (assets / "web" / "assets" / "private.map").write_text("PRIVATE-CREDENTIAL-MUST-NOT-UPLOAD")
    (assets / "web" / "secret.json").write_text("PRIVATE-CREDENTIAL-MUST-NOT-UPLOAD")
    for path in (tmp_path / ".env", tmp_path / "config.local.yaml", source / "private_credentials.json"):
        path.write_text("PRIVATE-CREDENTIAL-MUST-NOT-UPLOAD")
    stage = module.build(tmp_path)
    assert stage.parent == tmp_path / "data"
    assert (stage / "api" / "index.py").exists()
    assert (stage / "opswatch" / "cloud_relay.py").exists()
    assert (stage / "opswatch" / "dashboard_assets" / "web" / "assets" / "app.js").exists()
    assert (stage / "opswatch" / "dashboard_assets" / "web" / "assets" / "THIRD-PARTY-LICENSES.txt").exists()
    assert (stage / "opswatch" / "dashboard_assets" / "web" / "assets" / "chunks" / "chat-123.js").exists()
    assert not any(
        "PRIVATE-CREDENTIAL-MUST-NOT-UPLOAD" in path.read_text()
        for path in stage.rglob("*")
        if path.is_file()
    )
    assert not (stage / ".env").exists() and not (stage / "config.local.yaml").exists()


def test_entrypoint_exports_only_the_instantiated_asgi_app(tmp_path, monkeypatch):
    import runpy
    import sys
    import types

    from starlette.applications import Starlette

    script = Path(__file__).resolve().parents[1] / "scripts" / "build-cloud-dashboard.py"
    spec = importlib.util.spec_from_file_location("cloud_bundle_entrypoint", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = script.parents[1]
    stage = module.build(root)
    try:
        fake = types.ModuleType("opswatch.cloud_store")
        fake.PostgresRelay = lambda dsn: object()
        monkeypatch.setitem(sys.modules, "opswatch.cloud_store", fake)
        relay = types.ModuleType("opswatch.cloud_relay")
        relay.application = lambda *args: Starlette()
        monkeypatch.setitem(sys.modules, "opswatch.cloud_relay", relay)
        for name in ("DATABASE_URL", "BRIDGE_TOKEN", "CSRF_TOKEN"):
            monkeypatch.setenv("OPSWATCH_RELAY_" + name, "test-placeholder")
        entry = runpy.run_path(str(stage / "api" / "index.py"))
        assert isinstance(entry["app"], Starlette)
        assert "application" not in entry
    finally:
        import shutil

        shutil.rmtree(stage)
