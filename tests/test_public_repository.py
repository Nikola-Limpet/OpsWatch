import runpy
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.production",
        "config.local.yaml",
        "inventory.local.json",
        "data/opswatch.sqlite3",
        ".vercel/project.json",
        ".superpowers/session/progress.md",
        ".impeccable/critique/report.md",
        "docs/ui-verification/incidents-desktop.jpg",
        "docs/dashboard-preview.html",
        "docs/superpowers/specs/internal.md",
    ],
)
def test_public_repository_excludes_local_operational_data(tmp_path, name):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    shutil.copyfile(ROOT / ".gitignore", tmp_path / ".gitignore")
    result = subprocess.run(["git", "-C", str(tmp_path), "check-ignore", name], capture_output=True)
    assert result.returncode == 0, f"Private operational file would be publishable: {name}"


async def test_git_deployment_entrypoint_serves_dashboard_without_local_configuration(monkeypatch):
    for key, value in {
        "DATABASE_URL": "postgresql://monitor@example.invalid/relay",
        "BRIDGE_TOKEN": "test-bridge-token",
        "CSRF_TOKEN": "different-test-browser-token",
    }.items():
        monkeypatch.setenv("OPSWATCH_RELAY_" + key, value)
    entry = runpy.run_path(str(ROOT / "api" / "index.py"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=entry["app"]), base_url="https://dashboard.example"
    ) as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert "OpsWatch" in response.text
    assert "test-bridge-token" not in response.text
    assert "application" not in entry
