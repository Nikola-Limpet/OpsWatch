import hashlib
import json

import pytest

from opswatch.pstack import REVISION, SOURCE_ROOT, catalog, workflow_document


def test_catalog_links_to_pinned_licensed_sources():
    value = catalog()
    assert value["name"] == "pstack"
    assert value["revision"] == REVISION
    assert value["license"] == "MIT"
    assert {item["id"] for item in value["playbooks"]} == {
        "investigation",
        "bug-fix",
        "feature",
        "refactoring",
        "perf-issue",
        "reflection",
    }
    assert len(value["principles"]) == 7
    for item in [*value["playbooks"], *value["principles"]]:
        assert f"/blob/{REVISION}/pstack/skills/" in item["url"]
        assert "source" not in item
    assert all(item["steps"] for item in value["playbooks"])


def test_job_document_contains_real_upstream_text_and_cannot_mutate_catalog():
    value = workflow_document("bug-fix")
    assert value["playbook"]["id"] == "bug-fix"
    assert "Binary-search the cause" in value["playbook"]["source"]
    assert all(item["source"] for item in value["principles"])
    assert any("Cursor" in line for line in value["adaptation"])
    value["playbook"]["title"] = "changed"
    assert workflow_document("bug-fix")["playbook"]["title"] == "Bug fix"
    assert catalog()["playbooks"][1]["title"] == "Bug fix"


def test_reflection_document_and_unknown_playbook():
    assert "Structural enforcement check" in workflow_document("reflection")["playbook"]["source"]
    with pytest.raises(ValueError, match="playbook"):
        workflow_document("../../etc/passwd")


def test_vendored_source_manifest_matches_the_pinned_documents():
    manifest = json.loads((SOURCE_ROOT / "sources.json").read_text())
    assert manifest["revision"] == REVISION
    assert manifest["path"] == "pstack"
    assert "LICENSE" in manifest["files"]
    for path, digest in manifest["files"].items():
        assert hashlib.sha256((SOURCE_ROOT / path).read_bytes()).hexdigest() == digest
