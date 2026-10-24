from __future__ import annotations

import asyncio
import ipaddress
import json

import httpx

from .config import Config, Target
from .connectors import railway_account_env
from .process import parse_json, run

MANAGED_RULE = "Opswatch: blocked scanner IPs"
ENDPOINT = "https://backboard.railway.com/graphql/v2"


class RailwayEdge:
    def __init__(self, config: Config, client: httpx.AsyncClient):
        self.config, self.client = config, client
        self.locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    async def query(self, target: Target, document: str, variables: dict) -> dict:
        env = await railway_account_env(self.config.account_for(target), self.client)
        token = env.get("RAILWAY_API_TOKEN")
        if token:
            response = await self.client.post(
                ENDPOINT,
                headers={"Authorization": "Bearer " + token},
                json={"query": document, "variables": variables},
                timeout=20,
                follow_redirects=False,
            )
            response.raise_for_status()
            result = response.json()
        else:
            result = parse_json(
                await run(
                    ["railway", "api", "--compact", "--variables", json.dumps(variables)],
                    env=env,
                    stdin=document,
                    timeout=30,
                )
            )
        if result.get("errors"):
            raise ValueError("Railway edge API rejected the request: " + json.dumps(result["errors"]))
        if not isinstance(result.get("data"), dict):
            raise ValueError("Railway edge API returned no data")
        return result["data"]

    async def block(self, target: Target, ip: str):
        address = ipaddress.ip_address(ip)
        if address.version != 4:
            raise ValueError("Railway edge IP rules currently support IPv4 only")
        if not address.is_global or any(
            address in ipaddress.ip_network(cidr) for cidr in self.config.policy.allow_cidrs
        ):
            raise ValueError("IP is private, reserved, invalid, or allowlisted")
        key = (target.account, target.environment, target.service)
        async with self.locks.setdefault(key, asyncio.Lock()):
            variables = {"serviceId": target.service, "environmentId": target.environment}
            result = await self.query(
                target,
                "query($serviceId:String!,$environmentId:String!){"
                "serviceInstance(serviceId:$serviceId,environmentId:$environmentId){"
                "edgeConfig{enabled edgeRules}}}",
                variables,
            )
            edge = result["serviceInstance"]["edgeConfig"]
            ruleset = (edge or {}).get("edgeRules") or {"version": 1, "rules": []}
            if ruleset.get("version") != 1 or not isinstance(ruleset.get("rules"), list):
                raise ValueError("Unsupported Railway edge ruleset; existing rules were left unchanged")
            managed = [rule for rule in ruleset["rules"] if rule.get("description") == MANAGED_RULE]
            if len(managed) > 1:
                raise ValueError("Multiple Opswatch IP rules exist; review them before adding blocks")
            rule = managed[0] if managed else None
            ips = set()
            if rule:
                condition = rule["if"]
                clauses = condition.get("or", [condition])
                for clause in clauses:
                    if clause.get("attr") != "ipv4.src" or clause.get("op") != "in":
                        raise ValueError("The Opswatch IP rule was changed; review it before adding blocks")
                    ips.update(clause["value"])
                if (
                    ip in ips
                    and edge["enabled"]
                    and rule.get("enabled", True)
                    and rule.get("then", {}).get("action") == "block"
                ):
                    return
            ips.add(ip)
            addresses = sorted(ips)
            if len(addresses) > 2048:
                raise ValueError("Railway scanner block list is full; review existing bans")
            clauses = [
                {"attr": "ipv4.src", "op": "in", "value": addresses[start : start + 64]}
                for start in range(0, len(addresses), 64)
            ]
            if rule is None:
                rule = {
                    "description": MANAGED_RULE,
                    "priority": max((r["priority"] for r in ruleset["rules"]), default=0) + 1,
                }
                ruleset["rules"].append(rule)
            rule.update(
                enabled=True,
                **{
                    "if": clauses[0] if len(clauses) == 1 else {"or": clauses},
                    "then": {"action": "block", "params": {"status": 403, "body": "Forbidden"}},
                },
            )
            payload = {"input": {**variables, "edgeRules": ruleset}}
            checked = await self.query(
                target,
                "query($input:ValidateServiceEdgeRulesInput!){"
                "validateServiceEdgeRules(input:$input){code message path}}",
                payload,
            )
            if checked["validateServiceEdgeRules"]:
                raise ValueError(
                    "Railway rejected the IP rule: " + json.dumps(checked["validateServiceEdgeRules"])
                )
            saved = await self.query(
                target,
                "mutation($input:UpdateServiceEdgeRulesInput!){"
                "updateServiceEdgeRules(input:$input){enabled edgeRules}}",
                payload,
            )
            acknowledged = saved["updateServiceEdgeRules"]
            if not acknowledged.get("enabled") or not any(
                all(
                    candidate.get(field) == rule.get(field)
                    for field in ("description", "enabled", "if", "then")
                )
                for candidate in (acknowledged.get("edgeRules") or {}).get("rules", [])
            ):
                raise ValueError("Railway did not acknowledge the IP block")
