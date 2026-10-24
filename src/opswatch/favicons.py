"""Bounded favicon discovery for configured public sites, with in-memory caching."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx


def origin(url: str | None) -> str | None:
    try:
        parsed = urlsplit(url or "")
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return None
        return f"{parsed.scheme}://{parsed.netloc}"
    except ValueError:
        return None


async def public_url(url: str) -> str | None:
    """Resolve once and return an approved connection address, preventing DNS rebinding."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if not host or host.lower() == "localhost" or host.lower().endswith(".localhost"):
            return None
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            result = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(
                    host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM
                ),
                3,
            )
            addresses = [ipaddress.ip_address(row[4][0]) for row in result]
        if not addresses or not all(ip.is_global and not ip.is_multicast for ip in addresses):
            return None
        return str(min(addresses, key=lambda ip: ip.version))
    except (ValueError, OSError, TimeoutError):
        return None


@dataclass(frozen=True)
class Icon:
    data: bytes
    content_type: str


def image_type(data: bytes) -> str | None:
    for signature, kind in [
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\x00\x00\x01\x00", "image/x-icon"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
    ]:
        if data.startswith(signature):
            return kind
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if b"<svg" not in data[:1000] or b"<!" in data:
        return None
    try:
        root = ET.fromstring(data)
        if root.tag.rsplit("}", 1)[-1] != "svg":
            return None
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1].lower() in {"script", "foreignobject", "iframe", "image"}:
                return None
            for key, value in node.attrib.items():
                key = key.rsplit("}", 1)[-1].lower()
                if key.startswith("on") or (key == "href" and not value.startswith("#")):
                    return None
                if any(part in value.lower() for part in ("url(", "javascript:", "@import", "expression(")):
                    return None
        return "image/svg+xml"
    except (ET.ParseError, ValueError):
        return None


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        rel = (values.get("rel") or "").lower().split()
        if tag == "link" and ("icon" in rel or "apple-touch-icon" in rel) and values.get("href"):
            self.icons.append(values["href"])


class Favicons:
    def __init__(self, config, client: httpx.AsyncClient | None = None):
        self.client = client
        self.origins = {}
        enabled = config.targets
        for target in enabled:
            siblings = [
                t
                for t in enabled
                if (target.repair_repo and target.repair_repo == t.repair_repo)
                or (target.project_group and target.project_group == t.project_group)
                or (
                    target.kind == t.kind == "railway"
                    and target.account == t.account
                    and target.project == t.project
                )
            ]
            self.origins[target.id] = list(
                dict.fromkeys(o for t in [target, *siblings] if (o := origin(t.url)))
            )[:3]
        self.cache = {}
        self.locks = {}
        self.semaphore = asyncio.Semaphore(3)

    async def request(self, client, url, website, limit):
        for _ in range(4):
            if origin(url) not in {
                website,
                website.replace("http://", "https://", 1),
            }:
                return None
            address = await public_url(url)
            if not address:
                return None
            logical = httpx.URL(url)
            # The socket uses the approved numeric address, while HTTP and TLS retain site identity.
            async with client.stream(
                "GET",
                logical.copy_with(host=address),
                timeout=3,
                follow_redirects=False,
                headers={
                    "User-Agent": "OpsWatch favicon/1.0",
                    "Accept": "image/*,text/html;q=0.8",
                    "Host": logical.netloc.decode("ascii"),
                },
                extensions={"sni_hostname": logical.raw_host.decode("ascii")},
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                if response.status_code != 200:
                    return None
                if int(response.headers.get("content-length", "0")) > limit:
                    return None
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > limit:
                        return None
                return bytes(data)
        return None

    async def discover(self, client, website):
        data = await self.request(client, website + "/favicon.ico", website, 262144)
        if data and (kind := image_type(data)):
            return Icon(data, kind)
        html = await self.request(client, website + "/", website, 131072)
        if not html:
            return None
        links = Links()
        links.feed(html.decode("utf-8", errors="replace"))
        for href in links.icons[:4]:
            data = await self.request(client, urljoin(website + "/", href), website, 262144)
            if data and (kind := image_type(data)):
                return Icon(data, kind)
        return None

    async def get(self, target_id: str) -> Icon | None:
        for website in self.origins.get(target_id, []):
            lock = self.locks.setdefault(website, asyncio.Lock())
            async with lock:
                expires, icon = self.cache.get(website, (0, None))
                if expires <= time.time():
                    async with self.semaphore:
                        try:
                            if self.client:
                                icon = await self.discover(self.client, website)
                            else:
                                async with httpx.AsyncClient(trust_env=False) as client:
                                    icon = await self.discover(client, website)
                        except (httpx.HTTPError, ValueError, OSError):
                            icon = None
                    self.cache[website] = (time.time() + (86400 if icon else 3600), icon)
            if icon:
                return icon
        return None
