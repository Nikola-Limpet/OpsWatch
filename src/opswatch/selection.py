"""Project display groups and atomic, private monitoring selection changes."""

from __future__ import annotations

import fcntl
import hashlib
import os
import tempfile
import time
from pathlib import Path

import yaml

from .config import Config


def refresh_selection(config: Config):
    path = config._source_path
    if not path:
        return
    try:
        stat = path.stat()
        revision = (stat.st_mtime_ns, stat.st_size)
        if revision == config._selection_revision:
            return
        current = Config.model_validate(yaml.safe_load(path.read_text()) or {})
    except (OSError, ValueError, yaml.YAMLError):
        return  # Keep the last valid selection during an incomplete manual edit.
    saved = {t.id: t for t in current.targets}
    for target in config.targets:
        if target.id in saved:
            target.enabled = saved[target.id].enabled
            target.project_group = saved[target.id].project_group
        else:
            target.enabled = False
    config._selection_revision = revision


def projects(config: Config) -> list[dict]:
    groups = {}
    for target in config.targets:
        if target.project_group:
            key, name = "group:" + target.project_group.casefold(), target.project_group
        elif target.kind == "railway":
            key = f"railway:{target.account}:{target.project}"
            name = target.name.split(" · ")[0].split(" / ")[0]
        elif target.repair_repo:
            key = "repo:" + target.repair_repo
            web = next(
                (t for t in config.targets if t.repair_repo == target.repair_repo and t.kind == "vercel"),
                target,
            )
            name = web.name
        else:
            key = f"{target.kind}:{target.account}:{target.organization}:{target.project or target.id}"
            name = target.name.split(" / ")[-1]
        identity = "project-" + hashlib.sha256(key.encode()).hexdigest()[:16]
        group = groups.setdefault(identity, {"id": identity, "name": name, "targets": []})
        group["targets"].append(target)
    return sorted(groups.values(), key=lambda group: group["name"].casefold())


def set_selection(config: Config, scope: str, identity: str, enabled: bool) -> list[str]:
    path = config._source_path
    if not path:
        raise ValueError(
            "Monitoring controls require a saved configuration. Start the dashboard with --config."
        )
    lock_path = path.with_name(".opswatch-selection.lock")
    with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        raw = yaml.safe_load(path.read_text()) or {}
        latest = Config.model_validate(raw)
        if scope == "services":
            selected = [t.id for t in latest.targets if t.id == identity]
        else:
            group = next((group for group in projects(latest) if group["id"] == identity), None)
            selected = [t.id for t in group["targets"]] if group else []
        if not selected:
            raise KeyError("Project or service is no longer configured. Refresh the dashboard.")
        for target in raw["targets"]:
            if target["id"] in selected:
                target["enabled"] = enabled
        Config.model_validate(raw)
        backup = Path(config.data_dir) / "backups" / f"config-before-selection-{time.time_ns()}.yaml"
        backup.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.fdopen(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(path.read_bytes())
        fd, temporary = tempfile.mkstemp(prefix=".selection-", suffix=".yaml", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                yaml.safe_dump(raw, stream, sort_keys=False, allow_unicode=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    refresh_selection(config)
    return selected
