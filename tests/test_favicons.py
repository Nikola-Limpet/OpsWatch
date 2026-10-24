import base64
import importlib

import httpx
import pytest

from opswatch.config import Config, Target

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9V8AAAAASUVORK5CYII="
)


def configuration(url="https://example.com"):
    return Config(targets=[Target(id="web", name="Web", kind="http", url=url)])


def module():
    return importlib.import_module("opswatch.favicons")


async def test_discovers_site_icon_and_reuses_cached_result(monkeypatch):
    m = module()
    calls = []

    async def public(url):
        return "93.184.215.14"

    monkeypatch.setattr(m, "public_url", public)

    def serve(request):
        calls.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(200, text='<link rel="icon" href="/brand.png">')
        if request.url.path == "/brand.png":
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        icons = m.Favicons(configuration(), client)
        first = await icons.get("web")
        assert first.data == PNG and first.content_type == "image/png"
        assert (await icons.get("web")).data == PNG
        assert calls == ["/favicon.ico", "/", "/brand.png"]


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1", "http://localhost", "http://169.254.169.254", "http://[::1]"]
)
async def test_private_website_never_receives_a_favicon_request(url):
    m = module()
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, content=PNG))
    ) as client:
        assert await m.Favicons(configuration(url), client).get("web") is None
    assert calls == []


async def test_redirect_to_a_private_or_unconfigured_origin_is_not_followed(monkeypatch):
    m = module()

    async def public(url):
        return "93.184.215.14"

    monkeypatch.setattr(m, "public_url", public)
    calls = []

    def serve(request):
        calls.append(request.headers["host"])
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        assert await m.Favicons(configuration(), client).get("web") is None
    assert calls and set(calls) == {"example.com"}


@pytest.mark.parametrize(
    "body,content_type",
    [
        (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "image/svg+xml"),
        (b"<html>not an icon</html>", "image/png"),
        (PNG + b"x" * 262144, "image/png"),
    ],
    ids=["scripted-svg", "not-an-image", "oversize"],
)
async def test_unsafe_malformed_and_oversize_images_keep_the_initials(monkeypatch, body, content_type):
    m = module()

    async def public(url):
        return "93.184.215.14"

    monkeypatch.setattr(m, "public_url", public)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=body, headers={"content-type": content_type})
        )
    ) as client:
        assert await m.Favicons(configuration(), client).get("web") is None


async def test_unknown_target_does_not_fetch_any_arbitrary_website():
    m = module()
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200))
    ) as client:
        assert await m.Favicons(configuration(), client).get("https://unconfigured.example.com") is None
    assert not calls


async def test_transport_connects_to_validated_address_preserving_host_and_tls_name(monkeypatch):
    m = module()
    calls = []

    async def public(url):
        return "93.184.215.14"

    monkeypatch.setattr(m, "public_url", public)

    def serve(request):
        calls.append(request)
        return httpx.Response(200, content=PNG)

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        assert await m.Favicons(configuration(), client).get("web")
    assert len(calls) == 1
    assert calls[0].url.host == "93.184.215.14"
    assert calls[0].headers["host"] == "example.com"
    assert calls[0].extensions["sni_hostname"] == "example.com"
