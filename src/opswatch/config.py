from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Account(Model):
    name: str
    token_env: str | None = None
    scope: str | None = None
    credentials_path: str | None = None
    cli_user_id: str | None = None

    @model_validator(mode="after")
    def validate_auth_source(self):
        if self.token_env and self.credentials_path:
            raise ValueError("choose token_env or credentials_path for an account")
        return self


class Telegram(Model):
    enabled: bool = False
    mode: Literal["standalone", "hermes"] = "standalone"
    token_env: str = "TELEGRAM_BOT_TOKEN"
    chat_id_env: str = "TELEGRAM_CHAT_ID"
    allowed_user_ids_env: str = "TELEGRAM_USER_ID"
    notifications: Literal["all", "important"] = "all"
    interactive_controls: bool = False
    dashboard_url: str | None = None
    warning_after_seconds: int = Field(600, ge=0, le=86400)
    repeat_after_seconds: int = Field(21600, ge=60, le=604800)
    recovery_after_seconds: int = Field(300, ge=0, le=86400)


class Agent(Model):
    backend: Literal["hermes", "codex"] = "hermes"
    provider: str = "openai-codex"
    model: str = "gpt-6.1-sol"
    reasoning: str = "medium"
    timeout_seconds: int = Field(180, ge=10, le=900)
    auto_diagnose: bool = False
    max_per_hour: int = Field(6, ge=1, le=100)


class Ingest(Model):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = Field(8766, ge=1024, le=65535)


class RepairRepository(Model):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,80}$")
    github: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    base: str = Field(default="main", pattern=r"^[A-Za-z0-9][A-Za-z0-9_./-]*$")
    checks: list[list[str]] = Field(min_length=1)
    setup: list[list[str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_commands(self):
        if ".." in self.base or any(
            not argv or any(not arg or "\0" in arg for arg in argv) for argv in self.setup + self.checks
        ):
            raise ValueError("repair commands and base branch must be explicit and valid")
        return self


class Repair(Model):
    enabled: bool = False
    auto_fix: bool = False
    auto_fix_delay_seconds: int = Field(120, ge=0, le=86400)
    auto_fix_cooldown_seconds: int = Field(21600, ge=0, le=604800)
    model: str | None = None
    timeout_seconds: int = Field(1800, ge=60, le=7200)
    check_timeout_seconds: int = Field(900, ge=10, le=3600)
    max_per_day: int = Field(4, ge=1, le=100)
    repositories: list[RepairRepository] = Field(default_factory=list)


class Policy(Model):
    dry_run: bool = True
    auto_block: bool = False
    scan_window_seconds: int = Field(300, ge=30, le=3600)
    scan_min_requests: int = Field(12, ge=3, le=1000)
    scan_min_paths: int = Field(3, ge=2, le=100)
    block_ttl_seconds: int = Field(900, ge=60, le=86400)
    allow_cidrs: list[str] = Field(default_factory=list)


class Blocker(Model):
    kind: Literal["none", "webhook", "ssh-nft", "railway-edge"] = "none"
    permanent: bool = False
    url: str | None = None
    token_env: str = "OPSWATCH_BLOCK_TOKEN"
    ssh_host: str | None = None

    @model_validator(mode="after")
    def validate_backend(self):
        if self.kind == "webhook" and (not self.url or not self.url.startswith("https://")):
            raise ValueError("webhook blockers require an HTTPS URL")
        if self.kind == "ssh-nft" and not self.ssh_host:
            raise ValueError("ssh-nft blockers require a configured SSH host alias")
        if self.kind == "railway-edge" and not self.permanent:
            raise ValueError("railway-edge requires permanent: true; Railway IP rules have no expiry")
        if self.permanent and self.kind != "railway-edge":
            raise ValueError("permanent blocking is only supported by railway-edge")
        return self


class Target(Model):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}$")
    name: str
    enabled: bool = True
    kind: Literal["http", "railway", "vercel", "sentry", "postgres", "server", "logfile"]
    interval_seconds: int = Field(60, ge=10, le=86400)
    account: str = "default"
    url: str | None = None
    expected_status: list[int] = Field(default_factory=lambda: list(range(200, 400)))
    body_contains: str | None = None
    headers_env: str | None = None
    timeout_seconds: int = Field(20, ge=1, le=120)
    slow_ms: float = Field(1500, gt=0)
    project: str | None = None
    environment: str = "production"
    environment_name: str | None = None
    service: str | None = None
    logs: bool = False
    organization: str | None = None
    dsn_env: str | None = None
    neon_project_id: str | None = None
    neon_branch_id: str | None = None
    neon_database: str | None = None
    neon_role: str | None = None
    min_query_calls: int = Field(5, ge=1)
    ssh_host: str | None = None
    units: list[str] = Field(default_factory=list)
    containers: list[str] = Field(default_factory=list)
    log_path: str | None = None
    trusted_access_log: bool = False
    repair_repo: str | None = None
    project_group: str | None = Field(default=None, min_length=1, max_length=120)
    ingest_secret_env: str | None = None
    blocker: Blocker = Field(default_factory=Blocker)

    @model_validator(mode="after")
    def required_fields(self):
        if self.blocker.kind == "railway-edge" and self.kind != "railway":
            raise ValueError("railway-edge blocking requires a Railway target")
        required = {
            "http": ["url"],
            "railway": ["project", "service"],
            "vercel": ["project"],
            "sentry": ["organization", "project"],
            "postgres": [],
            "server": ["ssh_host"],
            "logfile": ["log_path"],
        }
        if any(not getattr(self, key) for key in required[self.kind]):
            raise ValueError(f"{self.kind} requires {', '.join(required[self.kind])}")
        if (
            self.kind == "postgres"
            and not self.dsn_env
            and not all((self.neon_project_id, self.neon_branch_id, self.neon_database, self.neon_role))
        ):
            raise ValueError("postgres requires dsn_env or explicit Neon project, branch, database, and role")
        if self.url and not self.url.startswith(("http://", "https://")):
            raise ValueError("health URL must use HTTP or HTTPS")
        if self.enabled and self.kind == "vercel" and self.url:
            host = (urlsplit(self.url).hostname or "").lower().rstrip(".")
            if host == "vercel.app" or host.endswith(".vercel.app"):
                raise ValueError("Vercel health checks require a custom production domain")
        if self.kind == "server" and not (self.units or self.containers):
            raise ValueError("server requires systemd units or Docker containers")
        return self


class Config(Model):
    _source_path: Path | None = PrivateAttr(default=None)
    _selection_revision: tuple[int, int] | None = PrivateAttr(default=None)
    timezone: str = "Asia/Phnom_Penh"
    data_dir: str = "data"
    concurrency: int = Field(4, ge=1, le=20)
    failures_before_alert: int = Field(3, ge=1, le=20)
    recoveries_before_alert: int = Field(2, ge=1, le=20)
    reminder_seconds: int = Field(1800, ge=60)
    retention_days: int = Field(14, ge=1, le=365)
    heartbeat_url_env: str | None = None
    telegram: Telegram = Field(default_factory=Telegram)
    agent: Agent = Field(default_factory=Agent)
    ingest: Ingest = Field(default_factory=Ingest)
    repair: Repair = Field(default_factory=Repair)
    policy: Policy = Field(default_factory=Policy)
    railway_accounts: list[Account] = Field(default_factory=lambda: [Account(name="default")])
    vercel_accounts: list[Account] = Field(default_factory=lambda: [Account(name="default")])
    targets: list[Target] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self):
        ZoneInfo(self.timezone)
        ids = [t.id for t in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("target IDs must be unique")
        repositories = [r.id for r in self.repair.repositories]
        if len(repositories) != len(set(repositories)):
            raise ValueError("repair repository IDs must be unique")
        for target in self.targets:
            if target.repair_repo and target.repair_repo not in repositories:
                raise ValueError(f"unknown repair repository for {target.id}: {target.repair_repo}")
        for provider in ("railway", "vercel"):
            accounts = getattr(self, f"{provider}_accounts")
            names = [a.name for a in accounts]
            if len(names) != len(set(names)):
                raise ValueError(f"{provider} account names must be unique")
            for t in self.targets:
                if t.kind == provider and t.account not in names:
                    raise ValueError(f"unknown {provider} account for {t.id}: {t.account}")
        from ipaddress import ip_network

        for cidr in self.policy.allow_cidrs:
            ip_network(cidr)
        return self

    def account_for(self, target: Target) -> Account:
        return next(a for a in getattr(self, f"{target.kind}_accounts") if a.name == target.account)


def secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"missing environment variable {name}")
    return value


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    load_dotenv(path.parent / ".env", override=False)
    stat = path.stat()
    config = Config.model_validate(yaml.safe_load(path.read_text()) or {})
    config._source_path = path
    config._selection_revision = (stat.st_mtime_ns, stat.st_size)
    data = Path(config.data_dir).expanduser()
    config.data_dir = str(data if data.is_absolute() else path.parent / data)
    for account in config.railway_accounts:
        if account.credentials_path:
            credentials = Path(account.credentials_path).expanduser()
            account.credentials_path = str(
                credentials if credentials.is_absolute() else path.parent / credentials
            )
    for target in config.targets:
        if target.log_path and not Path(target.log_path).is_absolute():
            target.log_path = str(path.parent / target.log_path)
    return config
