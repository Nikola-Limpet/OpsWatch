"""Serve only the built React application and explicitly supported static assets."""

from __future__ import annotations

import html
import re
from pathlib import Path

from starlette.responses import FileResponse, HTMLResponse, Response

SPA_PATHS = ("/", "/projects", "/incidents", "/agents", "/queries", "/studio", "/reports", "/workflow")
FONT_NAMES = {"geist-sans.woff2", "geist-mono.woff2", "FONT-LICENSE.txt"}
WEB_NOTICES = "THIRD-PARTY-LICENSES.txt"
ASSET_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)*\Z")


def frontend_page(assets: Path, token: str) -> HTMLResponse:
    page = assets / "web" / "index.html"
    if not page.is_file():
        return HTMLResponse(
            "<html><head><title>OpsWatch</title></head><body><h1>Build the OpsWatch frontend</h1>"
            "<p>Run <code>npm ci --prefix frontend &amp;&amp; npm run build --prefix frontend</code>, "
            "then restart the dashboard.</p></body></html>",
            status_code=503,
        )
    return HTMLResponse(page.read_text().replace("__OPSWATCH_CSRF__", html.escape(token, quote=True)))


def frontend_asset(assets: Path, name: str) -> Response:
    if name in FONT_NAMES:
        path = assets / name
    elif name == WEB_NOTICES:
        path = assets / "web" / "assets" / name
    elif ASSET_PATH.fullmatch(name) and Path(name).suffix in {".js", ".css", ".woff2"}:
        directory = (assets / "web" / "assets").resolve()
        path = (directory / name).resolve()
        if not path.is_relative_to(directory):
            return Response(status_code=404)
    else:
        return Response(status_code=404)
    if not path.is_file():
        return Response(status_code=404)
    return FileResponse(path)
