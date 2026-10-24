import importlib
import importlib.util
from uuid import uuid4

import pytest


def validate(action, payload):
    assert importlib.util.find_spec("opswatch.studio_protocol"), "Studio validation is not implemented"
    return importlib.import_module("opswatch.studio_protocol").validate_action(action, payload)


def test_session_defaults_are_normalized_without_changing_input():
    payload = {"title": "  Investigate checkout  "}
    result = validate("session_create", payload)
    assert result == {
        "title": "Investigate checkout",
        "role": "chat",
        "playbook": "investigation",
        "scope": {"kind": "workspace", "id": None},
    }
    assert payload == {"title": "  Investigate checkout  "}


@pytest.mark.parametrize(
    "action,payload",
    [
        ("shell", {}),
        ("session_create", {"role": "root"}),
        ("session_create", {"role": []}),
        ("session_create", {"playbook": "automatic-deploy"}),
        ("session_create", {"playbook": []}),
        ("session_create", {"scope": {"kind": [], "id": "shop"}}),
        ("session_create", {"command": "echo hello"}),
        ("session_create", {"scope": {"kind": "workspace", "id": "anything"}}),
        ("session_create", {"scope": {"kind": "service", "id": "../secret"}}),
        ("message_send", {"session_id": str(uuid4()), "content": "  "}),
        ("message_send", {"session_id": str(uuid4()), "content": "x" * 8001}),
        ("message_send", {"session_id": "not-a-uuid", "content": "Hello"}),
        ("session_read", {"session_id": str(uuid4()), "limit": True}),
        ("session_read", {"session_id": str(uuid4()), "limit": 21}),
        ("report_create", {"period": "custom", "window_start": 10, "window_end": 9}),
        ("report_create", {"period": "custom", "window_start": 0, "window_end": float("inf")}),
        ("report_create", {"period": {}}),
        ("preferences_update", {"revision": True, "notes": "hello"}),
        ("preferences_update", {"revision": 1, "stack": ["Python"] * 21}),
        ("problem_update", {"problem_id": str(uuid4()), "status": "merged"}),
        ("feedback", {"subject_type": "message", "subject_id": str(uuid4()), "rating": "automatic"}),
        ("feedback", {"subject_type": [], "subject_id": str(uuid4()), "rating": "helpful"}),
        ("collection_read", {"collection": "credentials"}),
        ("collection_read", {"collection": "sessions", "limit": 21}),
        ("problem_update", {"problem_id": str(uuid4()), "notes": "😀" * 4000}),
    ],
)
def test_untrusted_action_payloads_are_rejected(action, payload):
    with pytest.raises(ValueError):
        validate(action, payload)


def test_read_pages_and_updates_keep_only_valid_fields():
    identity = str(uuid4())
    assert validate("session_read", {"session_id": identity}) == {"session_id": identity, "limit": 20}
    assert validate("session_update", {"session_id": identity, "archived": True}) == {
        "session_id": identity,
        "archived": True,
    }
    assert validate("preferences_update", {"revision": 1, "notes": "  Prefer focused patches.  "}) == {
        "revision": 1,
        "notes": "Prefer focused patches.",
    }


def test_report_custom_window_is_bounded_and_preserved():
    result = validate("report_create", {"period": "custom", "window_start": 100, "window_end": 500})
    assert result["window_start"] == 100
    assert result["window_end"] == 500
    assert result["scope"] == {"kind": "workspace", "id": None}


def test_playbook_defaults_follow_the_selected_agent_role():
    assert validate("session_create", {"role": "improvement"})["playbook"] == "reflection"
    assert validate("session_create", {"role": "report"})["playbook"] == "investigation"
    assert validate("session_create", {"playbook": "bug-fix"})["playbook"] == "bug-fix"


def test_collection_pages_require_a_known_collection_and_normalize_the_limit():
    assert validate("collection_read", {"collection": "sessions"}) == {"collection": "sessions", "limit": 20}
    identity = str(uuid4())
    assert validate("collection_read", {"collection": "problems", "before": identity, "limit": 5}) == {
        "collection": "problems",
        "before": identity,
        "limit": 5,
    }
