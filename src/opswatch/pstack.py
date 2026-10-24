"""Pinned, licensed pstack guidance adapted to the local read-only agent runtime.

The upstream documents are application data. They do not grant tools or change
OpsWatch's execution policy. No source is downloaded during an agent job.
"""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path

REVISION = "9580c23696ac8d7abed0c166f2f9918800afb819"
REPOSITORY = "https://github.com/cursor/plugins/tree/main/pstack"
SOURCE_ROOT = Path(__file__).parent / "workflow_sources" / "pstack"
ADAPTATION = [
    "OpsWatch applies these planning, investigation, review, and verification methods to recorded "
    "application evidence. The steps below summarize the upstream playbooks for this read-only runtime.",
    "Cursor-specific commands, subagent orchestration, tools, and model defaults are reference material. "
    "OpsWatch uses its configured local Hermes or Codex engine and reports only work it actually performed.",
    "Change proposals, preference edits, and recurring problems stay reviewable. Reflection does not "
    "modify global skills, repositories, or engineering preferences automatically.",
]

_PLAYBOOKS = (
    (
        "investigation",
        "Investigation",
        "Understand the system and explain what the evidence supports.",
        [
            "Map how the affected system works and why the behavior matters.",
            "Keep the investigation read-only and distinguish observations from hypotheses.",
            "Explain key concepts, mechanisms, sources, and gotchas; compare tradeoffs for decisions.",
            "Write a clear answer with evidence and an explicit judgment where warranted.",
        ],
    ),
    (
        "bug-fix",
        "Bug fix",
        "Establish the failure mechanism before proposing a correction.",
        [
            "Reproduce the reported failure on the relevant surface, or state the missing evidence.",
            "Form candidate hypotheses and eliminate them using runtime evidence.",
            "Plan a focused correction for the surviving mechanism and identify affected boundaries.",
            "Specify the same-surface verification and a failing-then-passing regression where practical.",
            "Report the root cause, proposed fix, verification evidence, and remaining uncertainty.",
        ],
    ),
    (
        "feature",
        "Feature",
        "Understand the affected subsystem, choose the data shape, and plan verification.",
        [
            "Map the affected subsystem and explore viable designs.",
            "Name the domain model, organizing structure, boundaries, and success criteria.",
            "Plan independent work and review responsibilities where the available runtime supports them.",
            "Verify behavior on the matching surface and keep changes in small, ordered units.",
            "Resolve contested choices and report the chosen design with its tradeoffs.",
        ],
    ),
    (
        "refactoring",
        "Refactoring",
        "Change structure while preserving an observable behavior contract.",
        [
            "Pin the current behavior with a characterization test or equivalence harness.",
            "Name the missing domain structure and the intended module, type, and call shape.",
            "Subtract unused code first; migrate callers and remove the legacy path together.",
            "Move in small steps and prove behavior remains equivalent on the actual artifact.",
            "Keep the refactor only when it reduces reader load; report the evidence.",
        ],
    ),
    (
        "perf-issue",
        "Performance",
        "Measure the baseline and tie each proposed improvement to a trace.",
        [
            "Capture a trustworthy baseline on the matching surface.",
            "Ground hypotheses in how the system works; first eliminate work or repeated work.",
            "Plan from the trace and measure each attempt before trying the next.",
            "Compare baseline and post-change artifacts using the same measurement conditions.",
            "Report the measured delta and artifact references, or flag inconclusive results.",
        ],
    ),
    (
        "reflection",
        "Reflection",
        "Turn repeated lessons into reviewable structural improvements.",
        [
            "Use the current session and relevant recorded evidence; avoid unrelated private transcripts.",
            "Examine judgment, tooling, and divergent perspectives, stating which reviews actually ran.",
            "Separate accepted ideas, rejected ideas, and backlog candidates with reasons.",
            "Prefer a structural check or runtime constraint when it enforces a lesson more reliably.",
            "Present concrete proposals for owner review and summarize the applied outcomes.",
        ],
    ),
)
_PRINCIPLES = (
    (
        "foundational-thinking",
        "Foundational thinking",
        "Choose data structures early, isolate concurrent state, and build shared foundations first.",
    ),
    (
        "model-the-domain",
        "Model the domain",
        "Encode real entities and valid transitions in a clear data model instead of scattered conditions.",
    ),
    (
        "prove-it-works",
        "Prove it works",
        "Observe behavior directly on the relevant surface and preserve reproducible verification evidence.",
    ),
    (
        "fix-root-causes",
        "Fix root causes",
        "Identify the failure mechanism before selecting a fix; avoid hiding the evidence with a workaround.",
    ),
    (
        "make-operations-idempotent",
        "Make operations idempotent",
        "Derive actions from current state so retries and partial failures converge safely.",
    ),
    (
        "encode-lessons-in-structure",
        "Encode lessons in structure",
        "Prefer types, checks, scripts, and runtime constraints over instructions that must be remembered.",
    ),
    (
        "migrate-callers-then-delete-legacy-apis",
        "Migrate callers, then delete legacy APIs",
        "Move consumers to the new contract and remove obsolete paths in the same migration.",
    ),
)


def _url(path: str) -> str:
    return f"https://github.com/cursor/plugins/blob/{REVISION}/pstack/{path}"


def _playbook_path(playbook: str) -> str:
    return (
        "skills/reflect/SKILL.md"
        if playbook == "reflection"
        else f"skills/poteto-mode/playbooks/{playbook}.md"
    )


@lru_cache(maxsize=1)
def _catalog() -> dict:
    return {
        "name": "pstack",
        "repository": REPOSITORY,
        "revision": REVISION,
        "license": "MIT",
        "playbooks": [
            {
                "id": key,
                "title": title,
                "description": description,
                "url": _url(_playbook_path(key)),
                "steps": steps,
            }
            for key, title, description, steps in _PLAYBOOKS
        ],
        "principles": [
            {"id": key, "title": title, "summary": summary, "url": _url(f"skills/principle-{key}/SKILL.md")}
            for key, title, summary in _PRINCIPLES
        ],
        "adaptation": ADAPTATION,
    }


def catalog() -> dict:
    """Return public provenance and adapted workflow summaries without full prompts."""
    return deepcopy(_catalog())


@lru_cache(maxsize=6)
def _workflow_document(playbook: str) -> dict:
    value = catalog()
    selected = next((item for item in value.pop("playbooks") if item["id"] == playbook), None)
    if selected is None:
        raise ValueError("Unknown pstack playbook")
    selected["source"] = (SOURCE_ROOT / _playbook_path(playbook)).read_text(encoding="utf-8")
    value["playbook"] = selected
    for principle in value["principles"]:
        path = SOURCE_ROOT / "skills" / f"principle-{principle['id']}" / "SKILL.md"
        principle["source"] = path.read_text(encoding="utf-8")
    return value


def workflow_document(playbook: str = "investigation") -> dict:
    """Pin a complete selected playbook and principles for a durable agent job."""
    return deepcopy(_workflow_document(playbook))
