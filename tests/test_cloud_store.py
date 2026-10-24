"""Exercise queue policy without a live database; persistence is a transaction boundary."""

import copy
import threading
from contextlib import contextmanager
from uuid import uuid4

import pytest

from opswatch.cloud_store import READ_RESULT_RETENTION, PostgresRelay, RelayData, RelayError

SNAPSHOT = {
    "generated_at": 1000,
    "monitor": {"status": "running", "last_cycle": 1000},
    "summary": {"total": 1, "healthy": 1, "attention": 0, "unknown": 0, "open_incidents": 1},
    "services": [{"id": "api", "status": "healthy", "latency_ms": 23, "checks": []}],
    "projects": [
        {
            "id": "shop",
            "services": [
                {"id": "api", "enabled": True, "status": "healthy", "latency_ms": 23, "checks": []},
                {"id": "cache", "enabled": False, "status": "disabled", "checks": []},
            ],
        }
    ],
    "selection_editable": True,
    "incidents": [{"ref": "OW-0001", "target": "api", "status": "open", "repair": {"available": True}}],
    "queries": [{"target": "api", "healthy": True, "stale": False}],
}


class MemoryRelay(PostgresRelay):
    """Same production policies, an isolated copy-on-commit transaction for tests."""

    def __init__(self, clock=None):
        self.data = RelayData()
        self.lock = threading.Lock()
        self.now = 1000.0
        self.clock = clock or (lambda: self.now)

    @contextmanager
    def _transaction(self, identities=()):
        with self.lock:
            data = copy.deepcopy(self.data)
            yield data
            self.data = data

    def read_snapshot(self):
        with self._transaction() as data:
            return copy.deepcopy(data.snapshot), min(data.last_seen, data.published_at)

    def icon(self, target):
        with self._transaction() as data:
            return data.icons.get(target)


@pytest.fixture
def store():
    relay = MemoryRelay()
    relay.sync({"snapshot": copy.deepcopy(SNAPSHOT)})
    return relay


def test_only_head_command_is_leased_and_retried_after_lease_expires(store):
    first = store.enqueue("services", "api", {"enabled": False})
    second = store.enqueue("projects", "shop", {"enabled": True})
    lease = store.sync({})["command"]
    assert lease["id"] == first and lease["expires_at"] == 1120
    assert store.command(first)["state"] == "running"
    assert store.sync({})["command"] is None
    store.now += 30
    assert store.sync({})["command"]["id"] == first
    result = {"id": first, "result": {"enabled": False}, "error": None}
    reply = store.sync({"results": [result]})
    assert reply["acknowledged"] == [first]
    assert reply["command"]["id"] == second
    assert store.command(first) == {
        "id": first,
        "state": "done",
        "result": {"enabled": False},
        "error": None,
    }
    repeated = store.sync({"results": [{**result, "result": {"enabled": True}}]})
    assert repeated["acknowledged"] == [first]
    assert store.command(first)["result"] == {"enabled": False}


def test_expired_head_never_runs_and_does_not_block_newer_command(store):
    first = store.enqueue("fix", "OW-0001", {})
    store.now += 50
    store.sync({})
    second = store.enqueue("services", "api", {"enabled": True})
    store.now += 70
    reply = store.sync({})
    assert reply["command"]["id"] == second
    assert store.command(first)["state"] == "expired"
    assert store.sync({"results": [{"id": first, "result": {}, "error": None}]})["acknowledged"] == [first]
    assert store.command(first)["state"] == "expired"


def test_idempotency_key_returns_original_command_and_rejects_reuse(store):
    key = str(uuid4())
    assert store.enqueue("fix", "OW-0001", {}, key) == key
    store.now += 61
    assert store.enqueue("fix", "OW-0001", {}, key) == key
    with pytest.raises(RelayError) as error:
        store.enqueue("services", "api", {"enabled": False}, key)
    assert error.value.status == 409
    assert len(store.data.commands) == 1


def test_ephemeral_read_results_expire_without_losing_mutation_idempotency(store):
    mutation = store.enqueue("services", "api", {"enabled": False})
    store.sync({})
    store.sync({"results": [{"id": mutation, "result": {"enabled": False}, "error": None}]})
    reads = []
    for _ in range(25):
        identity = store.enqueue(
            "studio", "workspace", {"action": "session_read", "payload": {"session_id": str(uuid4())}}
        )
        store.sync({})
        store.sync({"results": [{"id": identity, "result": {"messages": ["x" * 59000]}, "error": None}]})
        reads.append(identity)
    assert store.command(reads[-1])["state"] == "done"
    store.now += 120 + READ_RESULT_RETENTION + 1
    store.sync({"snapshot": copy.deepcopy(SNAPSHOT)})
    assert store.command(reads[-1]) is None
    assert [item["id"] for item in store.data.commands] == [mutation]
    assert store.enqueue("services", "api", {"enabled": False}, mutation) == mutation
    assert store.command(mutation)["result"] == {"enabled": False}


def test_active_reads_are_expired_then_retained_for_browser_recovery(store):
    identity = store.enqueue(
        "studio", "workspace", {"action": "collection_read", "payload": {"collection": "sessions"}}
    )
    store.sync({})
    store.now += 121
    assert store.command(identity)["state"] == "expired"
    store.now += READ_RESULT_RETENTION + 1
    assert store.command(identity) is None


def test_bridge_must_be_online_and_targets_must_be_published(store):
    for kind, target, payload in [
        ("fix", "OW-9999", {}),
        ("services", "missing", {"enabled": True}),
        ("projects", "missing", {"enabled": True}),
    ]:
        with pytest.raises(RelayError) as error:
            store.enqueue(kind, target, payload)
        assert error.value.status == 404
    store.now += 61
    with pytest.raises(RelayError) as error:
        store.enqueue("fix", "OW-0001", {})
    assert error.value.status == 503


def test_fix_requires_available_capability_and_selection_requires_editable_snapshot(store):
    data = copy.deepcopy(SNAPSHOT)
    data["incidents"][0]["repair"]["available"] = False
    data["selection_editable"] = False
    store.sync({"snapshot": data})
    for kind, target, payload in [("fix", "OW-0001", {}), ("services", "cache", {"enabled": True})]:
        with pytest.raises(RelayError) as error:
            store.enqueue(kind, target, payload)
        assert error.value.status == 409


def test_failed_command_preserves_public_error_and_unknown_result_is_not_acknowledged(store):
    command_id = store.enqueue("fix", "OW-0001", {})
    store.sync({})
    reply = store.sync(
        {
            "results": [
                {"id": command_id, "result": None, "error": "Repair unavailable"},
                {"id": str(uuid4()), "result": {}, "error": None},
            ]
        }
    )
    assert reply["acknowledged"] == [command_id]
    assert store.command(command_id)["state"] == "error"
    assert store.command(command_id)["error"] == "Repair unavailable"


@pytest.fixture
def postgres():
    import os

    dsn = os.environ.get("OPSWATCH_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set OPSWATCH_TEST_DATABASE_URL to an isolated test database")
    relay = PostgresRelay(dsn, clock=lambda: 1000.0)
    relay.initialize()
    with relay._connect() as db:
        db.execute("TRUNCATE opswatch_relay_commands, opswatch_relay_icons RESTART IDENTITY")
        db.execute("UPDATE opswatch_relay_state SET snapshot=NULL,last_seen=0,published_at=0")
    relay.sync({"snapshot": copy.deepcopy(SNAPSHOT)})
    return relay


def test_postgres_durability_ordered_leases_expiry_and_idempotent_results(postgres):
    first = postgres.enqueue("fix", "OW-0001", {}, str(uuid4()))
    second = postgres.enqueue("services", "api", {"enabled": False})
    other = PostgresRelay(postgres.dsn, clock=lambda: 1000.0)
    assert other.command(first)["state"] == "pending"
    assert other.sync({})["command"]["id"] == first
    assert postgres.sync({})["command"] is None
    other.clock = lambda: 1030.0
    assert other.sync({})["command"]["id"] == first
    updated = copy.deepcopy(SNAPSHOT)
    updated["services"][0]["status"] = "warning"
    response = other.sync(
        {
            "snapshot": updated,
            "results": [
                {"id": first, "result": {"status": "queued"}, "error": None},
            ],
        }
    )
    assert response["acknowledged"] == [first]
    assert response["command"]["id"] == second
    assert postgres.command(first)["state"] == "done"
    assert postgres.read_snapshot()[0]["services"][0]["status"] == "warning"
    assert other.sync({"results": [{"id": first, "result": {}, "error": None}]})["acknowledged"] == [first]
    assert postgres.command(first)["result"] == {"status": "queued"}
    assert postgres.enqueue("fix", "OW-0001", {}, first) == first
    other.clock = lambda: 1120.0
    assert other.sync({})["command"] is None
    assert other.command(second)["state"] == "expired"


def test_postgres_prunes_a_pending_read_after_a_long_idle_gap(postgres):
    identity = postgres.enqueue(
        "studio", "workspace", {"action": "collection_read", "payload": {"collection": "sessions"}}
    )
    postgres.clock = lambda: 1121 + READ_RESULT_RETENTION
    assert postgres.command(identity) is None
    with postgres._connect() as db:
        assert (
            db.execute(
                "SELECT count(*) AS count FROM opswatch_relay_commands WHERE id=%s", (identity,)
            ).fetchone()["count"]
            == 0
        )


def test_postgres_rolls_back_snapshot_and_command_completion_on_storage_failure(postgres):
    import psycopg

    identity = postgres.enqueue("fix", "OW-0001", {})
    postgres.sync({})
    with pytest.raises(psycopg.errors.NotNullViolation), postgres._transaction() as data:
        data.snapshot["services"][0]["status"] = "warning"
        data.commands[0].update(state="done", result={"updated": True})
        # The icon INSERT happens after the state and command UPDATEs.
        data.icons["bad"] = (b"invalid", None)
    other = PostgresRelay(postgres.dsn, clock=lambda: 1000.0)
    assert other.read_snapshot()[0]["services"][0]["status"] == "healthy"
    assert other.command(identity)["state"] == "running"
    assert other.icon("bad") is None
