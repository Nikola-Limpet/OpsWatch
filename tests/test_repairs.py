import importlib
import json

import pytest

from opswatch.config import Config
from opswatch.storage import Store
from opswatch.types import Observation


def module():
    try:
        return importlib.import_module("opswatch.repairs")
    except ModuleNotFoundError:
        pytest.fail("Manual repair workflow is not implemented")


def configuration(tmp_path):
    return Config.model_validate(
        {
            "data_dir": str(tmp_path),
            "repair": {
                "enabled": True,
                "repositories": [
                    {"id": "web", "github": "owner/web", "checks": [["python3", "-c", "print('pass')"]]}
                ],
            },
            "targets": [
                {
                    "id": "web",
                    "name": "Web",
                    "kind": "http",
                    "url": "https://example.com",
                    "repair_repo": "web",
                }
            ],
        }
    )


def incident(store, config, kind="application error"):
    return store.observe(
        Observation("web", "error", kind, False, "Unhandled exception", {}, immediate=True), config
    )


def test_repeated_fix_requests_share_one_job(tmp_path):
    m = module()
    c = configuration(tmp_path)
    s = Store(c.data_dir)
    item = incident(s, c)
    jobs = m.Repairs(c, s)
    first = jobs.enqueue(item["ref"], "owner")
    second = jobs.enqueue(item["ref"], "owner")
    assert first["id"] == second["id"]
    assert first["status"] == "queued"
    assert len(jobs.list()) == 1
    s.close()


def test_missing_repository_and_monitoring_incidents_do_not_queue(tmp_path):
    m = module()
    c = configuration(tmp_path)
    s = Store(c.data_dir)
    item = incident(s, c, "monitoring")
    jobs = m.Repairs(c, s)
    with pytest.raises(ValueError, match="monitoring"):
        jobs.enqueue(item["ref"], "owner")
    c.targets[0].repair_repo = None
    with pytest.raises(ValueError, match="repository"):
        jobs.enqueue(item["ref"], "owner")
    assert jobs.list() == []
    s.close()


def test_queue_claim_is_atomic_across_connections(tmp_path):
    m = module()
    c = configuration(tmp_path)
    s = Store(c.data_dir)
    other = Store(c.data_dir)
    jobs = m.Repairs(c, s)
    jobs.enqueue(incident(s, c)["ref"], "owner")
    assert jobs.claim()["status"] == "preparing"
    assert m.Repairs(c, other).claim() is None
    s.close()
    other.close()


def test_restart_preserves_worktree_and_marks_interrupted(tmp_path):
    m = module()
    c = configuration(tmp_path)
    s = Store(c.data_dir)
    jobs = m.Repairs(c, s)
    job = jobs.enqueue(incident(s, c)["ref"], "owner")
    jobs.update(job["id"], "publishing", worktree="/saved/tree", branch="opswatch/fix-1")
    jobs.recover_interrupted()
    saved = jobs.get(job["id"])
    assert saved["status"] == "interrupted"
    assert saved["worktree"] == "/saved/tree"
    assert jobs.enqueue(job["incident"], "owner")["id"] == job["id"]
    s.close()


def test_restart_and_notifications_include_jobs_older_than_status_page(tmp_path):
    m = module()
    c = configuration(tmp_path)
    c.repair.max_per_day = 100
    s = Store(c.data_dir)
    jobs = m.Repairs(c, s)
    first = jobs.enqueue(incident(s, c)["ref"], "owner")
    jobs.claim()
    for n in range(24):
        item = s.observe(
            Observation("web", f"error-{n}", "application error", False, "Exception", {}, immediate=True), c
        )
        jobs.enqueue(item["ref"], "owner")
    jobs.recover_interrupted()
    assert jobs.get(first["id"])["status"] == "interrupted"
    pending = jobs.pending_notifications()
    assert len(pending) == 25
    assert first["id"] in {row["id"] for row in pending}
    s.close()


def test_cancel_queued_job_does_not_start_it(tmp_path):
    m = module()
    c = configuration(tmp_path)
    s = Store(c.data_dir)
    jobs = m.Repairs(c, s)
    job = jobs.enqueue(incident(s, c)["ref"], "owner")
    jobs.cancel(job["id"])
    assert jobs.claim() is None
    assert jobs.get(job["id"])["status"] == "cancelled"
    s.close()


def test_cancellation_cannot_be_overwritten_by_publication(tmp_path):
    import asyncio

    m = module()
    c = configuration(tmp_path)
    s = Store(c.data_dir)
    jobs = m.Repairs(c, s)
    job = jobs.enqueue(incident(s, c)["ref"], "owner")
    jobs.update(job["id"], "validating")
    jobs.cancel(job["id"])
    with pytest.raises(asyncio.CancelledError):
        jobs.update(job["id"], "publishing")
    assert jobs.get(job["id"])["status"] == "cancel_requested"
    s.close()


def test_repair_environment_excludes_monitor_secrets(monkeypatch):
    m = module()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "private")
    monkeypatch.setenv("OPSWATCH_NEON_DSN", "private")
    monkeypatch.setenv("DATABASE_URL", "private")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = m.repair_environment()
    assert env["PATH"] == "/usr/bin"
    assert not any(k in env for k in ("TELEGRAM_BOT_TOKEN", "OPSWATCH_NEON_DSN", "DATABASE_URL"))


@pytest.mark.parametrize("path", [".env", "nested/.env.local", "credentials.pem", "id_rsa"])
def test_secret_files_cannot_be_published(path):
    with pytest.raises(ValueError, match="sensitive"):
        module().validate_paths([path])


async def test_failed_checks_never_push_or_create_pr(tmp_path, monkeypatch):
    m = module()
    c = configuration(tmp_path)
    s = Store(c.data_dir)
    jobs = m.Repairs(c, s)
    job = jobs.enqueue(incident(s, c)["ref"], "owner")
    worker = m.RepairWorker(c, s)
    calls = []

    async def command(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["gh", "repo", "view"]:
            return json.dumps({"nameWithOwner": "owner/web", "viewerPermission": "WRITE"})
        if argv[0] == "codex":
            report = argv[argv.index("--output-last-message") + 1]
            from pathlib import Path

            Path(report).write_text(json.dumps({"outcome": "fixed", "summary": "Fix exception"}))
        if "rev-parse" in argv:
            return "a" * 40
        if "symbolic-ref" in argv:
            return job["branch"]
        if "diff" in argv and "--name-only" in argv:
            return "app.py\0"
        if argv[:2] == ["python3", "-c"]:
            raise RuntimeError("test failed")
        return ""

    monkeypatch.setattr(m, "run", command)
    await worker.execute(job)
    assert jobs.get(job["id"])["status"] == "blocked"
    assert not any("push" in args or args[:3] == ["gh", "pr", "create"] for args in calls)
    s.close()


async def test_complete_repair_uses_real_worktree_and_pushes_only_its_branch(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path

    m = module()
    source, remote = tmp_path / "source", tmp_path / "remote.git"
    source.mkdir()

    def git(*args, cwd=source):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout

    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    (source / "app.py").write_text("value = 1\n")
    git("add", ".")
    git("commit", "-m", "baseline")
    original_sha = git("rev-parse", "HEAD").strip()
    git("clone", "--bare", str(source), str(remote))
    (source / "app.py").write_text("owner's unfinished edits\n")
    cfg = configuration(tmp_path / "data")
    cfg.repair.repositories[0].checks = [["python3", "-c", "from app import value; assert value == 2"]]
    store = Store(cfg.data_dir)
    jobs = m.Repairs(cfg, store)
    job = jobs.enqueue(incident(store, cfg)["ref"], "owner")
    real_run = m.run
    publication = []

    async def command(argv, **kwargs):
        argv = list(argv)
        if argv[:3] == ["gh", "repo", "view"]:
            return json.dumps({"nameWithOwner": "owner/web", "viewerPermission": "WRITE"})
        if argv[0] == "codex":
            tree = Path(kwargs["cwd"])
            assert (tree / ".git").is_file()
            assert (tree / "app.py").read_text() == "value = 1\n"
            (tree / "app.py").write_text("value = 2\n")
            Path(argv[argv.index("--output-last-message") + 1]).write_text(
                json.dumps({"outcome": "fixed", "summary": "Correct the value; regression test passes."})
            )
            return ""
        if argv[:3] == ["gh", "pr", "create"]:
            publication.append(argv)
            body = Path(argv[argv.index("--body-file") + 1]).read_text()
            assert "Validation completed" in body
            assert "--draft" in argv
            assert argv[argv.index("--head") + 1] == job["branch"]
            return "https://github.com/owner/web/pull/12"
        argv = [str(remote) if a == "https://github.com/owner/web.git" else a for a in argv]
        env = kwargs.get("env", {}).copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_COMMITTER_EMAIL": "test@example.com",
            }
        )
        kwargs["env"] = env
        return await real_run(argv, **kwargs)

    monkeypatch.setattr(m, "run", command)
    await m.RepairWorker(cfg, store).execute(job)
    result = jobs.get(job["id"])
    assert result["status"] == "pr_open", result
    assert result["pr_url"] == "https://github.com/owner/web/pull/12"
    assert len(publication) == 1
    assert (source / "app.py").read_text() == "owner's unfinished edits\n"
    assert git("--git-dir", str(remote), "rev-parse", "main").strip() == original_sha
    assert git("--git-dir", str(remote), "show", job["branch"] + ":app.py") == "value = 2\n"
    store.close()
