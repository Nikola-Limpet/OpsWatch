"""Build an allowlisted Vercel source bundle; never copy local configuration or data."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path


def build(root: Path) -> Path:
    source = root / "src" / "opswatch"
    (root / "data").mkdir(mode=0o700, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="vercel-dashboard-", dir=root / "data"))
    (stage / "api").mkdir()
    package = stage / "opswatch"
    package.mkdir()
    (package / "__init__.py").write_text("")
    for name in ("cloud_relay.py", "cloud_store.py", "favicons.py"):
        shutil.copyfile(source / name, package / name)
    assets = package / "dashboard_assets"
    assets.mkdir()
    for name in (
        "app.js",
        "app.css",
        "index.html",
        "geist-sans.woff2",
        "geist-mono.woff2",
        "FONT-LICENSE.txt",
    ):
        shutil.copyfile(source / "dashboard_assets" / name, assets / name)
    (stage / "api" / "index.py").write_text(
        "import os\n"
        "from opswatch.cloud_relay import application as create_app\n"
        "from opswatch.cloud_store import PostgresRelay\n"
        "app = create_app(\n"
        "    PostgresRelay(os.environ['OPSWATCH_RELAY_DATABASE_URL']),\n"
        "    os.environ['OPSWATCH_RELAY_BRIDGE_TOKEN'],\n"
        "    os.environ['OPSWATCH_RELAY_CSRF_TOKEN'],\n"
        ")\n"
    )
    (stage / "requirements.txt").write_text("starlette>=0.47,<2\npsycopg[binary]>=3.2,<4\nhttpx>=0.28,<1\n")
    (stage / ".python-version").write_text("3.12\n")
    (stage / ".vercelignore").write_text("**/.env*\n.env*\n.vercel/**\n**/__pycache__/**\n.git/**\n")
    (stage / "vercel.json").write_text(
        json.dumps(
            {
                "$schema": "https://openapi.vercel.sh/vercel.json",
                "regions": ["sin1"],
                "functions": {"api/index.py": {"maxDuration": 30, "includeFiles": "opswatch/**"}},
                "rewrites": [{"source": "/(.*)", "destination": "/api/index"}],
            },
            indent=2,
        )
    )
    return stage


if __name__ == "__main__":
    print(build(Path(__file__).resolve().parents[1]))
