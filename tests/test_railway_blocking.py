import asyncio
import copy
import json
import time

import httpx
import pytest
from pydantic import ValidationError

from opswatch.config import Account, Config, Policy, Target
from opswatch.security import Security
from opswatch.storage import Store
from opswatch.types import AccessEvent


class Railway:
    def __init__(self, rules=None, *, fail=False, acknowledge=True):
        self.rules = copy.deepcopy(rules or {"version": 1, "rules": []})
        self.fail = fail
        self.acknowledge = acknowledge
        self.writes = 0

    def handle(self, request):
        assert request.headers["Authorization"] == "Bearer example-app-token"
        document = json.loads(request.content)
        query, variables = document["query"], document.get("variables", {})
        if "validateServiceEdgeRules" in query:
            return httpx.Response(200, json={"data": {"validateServiceEdgeRules": []}})
        if "updateServiceEdgeRules" in query:
            assert variables["input"]["serviceId"] == "dashboard"
            assert variables["input"]["environmentId"] == "production-id"
            self.writes += 1
            if self.fail:
                return httpx.Response(200, json={"errors": [{"message": "Rule limit reached"}]})
            if self.acknowledge:
                self.rules = copy.deepcopy(variables["input"]["edgeRules"])
            return httpx.Response(200, json={"data": {"updateServiceEdgeRules": self.edge()}})
        assert "serviceInstance" in query
        assert variables == {"serviceId": "dashboard", "environmentId": "production-id"}
        return httpx.Response(200, json={"data": {"serviceInstance": {"edgeConfig": self.edge()}}})

    def edge(self):
        return {"enabled": True, "edgeRules": copy.deepcopy(self.rules)}


def configuration(tmp_path, **policy):
    credentials = tmp_path / "example-app.json"
    credentials.write_text(json.dumps({"user": {"accessToken": "example-app-token"}}))
    target = Target(
        id="example-app-dashboard",
        name="Example app dashboard",
        kind="railway",
        project="example-app-project",
        service="dashboard",
        environment="production-id",
        blocker={"kind": "railway-edge", "permanent": True},
    )
    config = Config(
        targets=[target],
        data_dir=str(tmp_path),
        railway_accounts=[Account(name="default", credentials_path=str(credentials))],
        policy=Policy(auto_block=True, dry_run=False, scan_min_requests=3, scan_min_paths=3, **policy),
    )
    return config, target


async def scan(security, target, ip="8.8.4.4", *, trusted=True):
    result = None
    for i, path in enumerate(("/.env", "/.git/config", "/wp-login.php")):
        result = await security.inspect(
            target, AccessEvent(target.id, f"{ip}:{trusted}:{i}", time.time(), ip, path, 403, trusted)
        )
    return result


async def test_repeated_probes_ban_ip_without_replacing_existing_rules(tmp_path):
    original = {
        "description": "Existing probe protection",
        "id": "rul_existing",
        "priority": 10,
        "enabled": True,
        "if": {"attr": "http.path", "op": "contains", "value": "/.env"},
        "then": {"action": "block"},
    }
    railway = Railway({"version": 1, "rules": [original]})
    config, target = configuration(tmp_path)
    store = Store(str(tmp_path))
    async with httpx.AsyncClient(transport=httpx.MockTransport(railway.handle)) as client:
        alert = await scan(Security(config, store, client), target)
    assert "until manually unblocked" in alert.action
    assert "900 seconds" not in alert.action
    assert original in railway.rules["rules"]
    ban = next(rule for rule in railway.rules["rules"] if rule != original)
    assert ban["if"] == {"attr": "ipv4.src", "op": "in", "value": ["8.8.4.4"]}
    assert ban["enabled"] and ban["then"]["action"] == "block"
    assert railway.writes == 1
    row = store.db.execute("SELECT * FROM blocks").fetchone()
    assert row["ip"] == "8.8.4.4" and row["expires"] == 0 and row["state"] == "blocked"
    assert json.loads(row["detail"])["permanent"] is True
    store.close()


async def test_parallel_scanners_are_added_to_one_rule(tmp_path):
    config, target = configuration(tmp_path)
    railway = Railway()
    store = Store(str(tmp_path))
    async with httpx.AsyncClient(transport=httpx.MockTransport(railway.handle)) as client:
        security = Security(config, store, client)
        await asyncio.gather(*(scan(security, target, ip) for ip in ("8.8.4.4", "1.0.0.1")))
        await security.block(target, "8.8.4.4")
    assert len(railway.rules["rules"]) == 1
    assert set(railway.rules["rules"][0]["if"]["value"]) == {"8.8.4.4", "1.0.0.1"}
    assert railway.writes == 2
    store.close()


@pytest.mark.parametrize("failure", ["api", "unacknowledged"])
async def test_failed_railway_ban_never_claims_success(tmp_path, failure):
    config, target = configuration(tmp_path)
    railway = Railway(fail=failure == "api", acknowledge=failure != "unacknowledged")
    store = Store(str(tmp_path))
    async with httpx.AsyncClient(transport=httpx.MockTransport(railway.handle)) as client:
        alert = await scan(Security(config, store, client), target)
    assert alert.action.startswith("Blocking failed")
    assert store.db.execute("SELECT count(*) FROM blocks").fetchone()[0] == 0
    store.close()


async def test_allowlisted_and_untrusted_clients_cannot_trigger_railway_ban(tmp_path):
    config, target = configuration(tmp_path, allow_cidrs=["8.8.4.0/24"])
    store = Store(str(tmp_path))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("must not call the firewall"))
    ) as client:
        security = Security(config, store, client)
        assert await scan(security, target) is None
        alert = await scan(security, target, "1.0.0.1", trusted=False)
    assert "not supplied by a trusted" in alert.action
    store.close()


async def test_dry_run_reports_permanent_railway_ban_without_writing(tmp_path):
    config, target = configuration(tmp_path)
    config.policy.dry_run = True
    store = Store(str(tmp_path))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("dry run must not call Railway"))
    ) as client:
        alert = await scan(Security(config, store, client), target)
    assert "Dry run" in alert.action and "until manually unblocked" in alert.action
    assert "900 seconds" not in alert.action
    store.close()


@pytest.mark.parametrize("kind,permanent", [("railway", False), ("http", True)])
def test_railway_blocking_requires_explicit_permanent_mode_on_railway_target(kind, permanent):
    with pytest.raises(ValidationError, match="railway-edge"):
        Target(
            id="invalid",
            name="Invalid",
            kind=kind,
            project="project",
            service="service",
            url="https://example.com",
            blocker={"kind": "railway-edge", "permanent": permanent},
        )
