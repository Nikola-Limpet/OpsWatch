import time

import httpx
import pytest

from opswatch.config import Blocker, Config, Policy, Target
from opswatch.security import Security, scan_path
from opswatch.storage import Store
from opswatch.types import AccessEvent


@pytest.fixture
def target():
    return Target(
        id="web",
        name="Web",
        kind="http",
        url="https://example.com",
        blocker=Blocker(kind="webhook", url="https://firewall.example.com/block"),
    )


def event(i, trusted=True, ip="8.8.4.4"):
    paths = ["/.env", "/.git/config", "/wp-login.php"]
    return AccessEvent("web", str(i), time.time(), ip, paths[i % 3], 404, trusted)


@pytest.mark.parametrize("path", ["/.env", "/.git/config", "/%2e%2e/%2e%2e/etc/passwd", "/actuator/env"])
def test_known_probe_paths(path):
    assert scan_path(path)


@pytest.mark.parametrize("path", ["/api/users", "/login", "/assets/theme.css", "/docs/git/tutorial"])
def test_regular_paths_are_not_probes(path):
    assert scan_path(path) is None


async def test_repeated_scan_blocks_only_once_and_records_evidence(tmp_path, target, monkeypatch):
    monkeypatch.setenv("OPSWATCH_BLOCK_TOKEN", "secret-test-token")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "blocked", "ttl_enforced": True})

    cfg = Config(
        data_dir=str(tmp_path),
        policy=Policy(auto_block=True, dry_run=False, scan_min_requests=6, scan_min_paths=3),
    )
    store = Store(str(tmp_path))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        security = Security(cfg, store, client)
        for i in range(5):
            assert await security.inspect(target, event(i)) is None
        alert = await security.inspect(target, event(5))
        assert "Blocked" in alert.action
        assert alert.evidence["requests"] == 6
        assert await security.inspect(target, event(5)) is None  # Overlapping poll.
        await security.inspect(target, event(6))
        assert len(requests) == 1
    store.close()


async def test_untrusted_requests_cannot_seed_a_trusted_block(tmp_path, target):
    cfg = Config(
        data_dir=str(tmp_path),
        policy=Policy(auto_block=True, dry_run=False, scan_min_requests=6, scan_min_paths=3),
    )
    store = Store(str(tmp_path))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("must not block"))
    ) as client:
        security = Security(cfg, store, client)
        for i in range(8):
            await security.inspect(target, event(i, trusted=False))
        assert await security.inspect(target, event(9)) is None
    store.close()


async def test_dry_run_allowlist_and_old_events(tmp_path, target):
    cfg = Config(
        data_dir=str(tmp_path),
        policy=Policy(
            auto_block=True, dry_run=True, scan_min_requests=3, scan_min_paths=3, allow_cidrs=["1.1.1.0/24"]
        ),
    )
    store = Store(str(tmp_path))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("dry run made request"))
    ) as client:
        security = Security(cfg, store, client)
        assert not security.permitted("1.1.1.1")
        assert not security.permitted("10.0.0.1")
        assert not security.permitted("203.0.113.42")
        old = event(9)
        old.timestamp -= 900
        assert await security.inspect(target, old) is None
        for i in range(2):
            assert await security.inspect(target, event(i)) is None
        alert = await security.inspect(target, event(2))
        assert "Dry run" in alert.action
        assert store.db.execute("SELECT count(*) FROM blocks").fetchone()[0] == 0
    store.close()


async def test_firewall_failure_does_not_claim_success(tmp_path, target, monkeypatch):
    monkeypatch.setenv("OPSWATCH_BLOCK_TOKEN", "test-secret-value")
    cfg = Config(
        data_dir=str(tmp_path),
        policy=Policy(auto_block=True, dry_run=False, scan_min_requests=3, scan_min_paths=3),
    )
    store = Store(str(tmp_path))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403))) as client:
        security = Security(cfg, store, client)
        for i in range(2):
            await security.inspect(target, event(i))
        alert = await security.inspect(target, event(2))
        assert alert.action.startswith("Blocking failed")
        assert store.db.execute("SELECT count(*) FROM blocks").fetchone()[0] == 0
    store.close()


async def test_unattributed_probes_alert_without_blocking(tmp_path, target):
    cfg = Config(
        data_dir=str(tmp_path),
        policy=Policy(auto_block=True, dry_run=False, scan_min_requests=3, scan_min_paths=3),
    )
    store = Store(str(tmp_path))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("no IP to block"))
    ) as client:
        security = Security(cfg, store, client)
        for i in range(2):
            await security.inspect(target, event(i, trusted=False, ip=""))
        result = await security.inspect(target, event(2, trusted=False, ip=""))
        assert result.severity == "warning"
        assert "did not include a client IP" in result.action
    store.close()
