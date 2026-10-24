"""Vercel entrypoint for the private dashboard relay, never the local monitor."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _create_relay():
    from opswatch.cloud_relay import application
    from opswatch.cloud_store import PostgresRelay

    return application(
        PostgresRelay(os.environ["OPSWATCH_RELAY_DATABASE_URL"]),
        os.environ["OPSWATCH_RELAY_BRIDGE_TOKEN"],
        os.environ["OPSWATCH_RELAY_CSRF_TOKEN"],
    )


app = _create_relay()
