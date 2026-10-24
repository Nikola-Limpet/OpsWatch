#!/usr/bin/env python3
"""Send a normalized JSON batch over a timestamped HMAC connection."""

import argparse
import hashlib
import hmac
import os
import time
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("url", help="HTTPS ingest URL or localhost URL over an SSH tunnel")
parser.add_argument("batch", type=Path, help="JSON array of normalized access/error records")
parser.add_argument("--secret-env", default="OPSWATCH_INGEST_SECRET")
args = parser.parse_args()
body = args.batch.read_bytes()
timestamp = str(int(time.time()))
signature = hmac.new(
    os.environ[args.secret_env].encode(), timestamp.encode() + b"." + body, hashlib.sha256
).hexdigest()
request = urllib.request.Request(
    args.url,
    data=body,
    headers={
        "Content-Type": "application/json",
        "X-Opswatch-Timestamp": timestamp,
        "X-Opswatch-Signature": signature,
    },
    method="POST",
)
with urllib.request.urlopen(request, timeout=15) as response:
    print(response.status)
