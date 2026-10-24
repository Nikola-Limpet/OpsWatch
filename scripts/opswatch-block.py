#!/usr/bin/env python3
"""Root-owned fixed helper for a preconfigured nftables web-only block set."""

import ipaddress
import json
import subprocess
import sys


def main():
    data = json.loads(sys.stdin.read(4096))
    if data.get("action") != "block":
        raise ValueError("only temporary block actions are supported")
    address = ipaddress.ip_address(data["ip"])
    if not address.is_global:
        raise ValueError("only public IPs may be blocked")
    ttl = int(data["ttl_seconds"])
    if not 60 <= ttl <= 86400:
        raise ValueError("TTL must be between one minute and one day")
    name = "blocked4" if address.version == 4 else "blocked6"
    # nft receives a fixed grammar with a parsed IP and bounded integer. No shell.
    command = f"add element inet opswatch {name} {{ {address} timeout {ttl}s }}"
    result = subprocess.run(
        ["/usr/sbin/nft", "-f", "-"], input=command, text=True, capture_output=True, timeout=5
    )
    if result.returncode:
        raise RuntimeError(result.stderr[:400])
    print(json.dumps({"status": "blocked", "ttl_enforced": True}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
