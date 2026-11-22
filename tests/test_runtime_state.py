"""MIA-HOLO-01: runtime-state contract. Every state needs real, fresh evidence."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from mia.computer.approvals import ApprovalStore
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.runtime_state import (
    EVIDENCE_KINDS,
    UNSUPPORTED_STATES,
    Evidence,
    RuntimeState,
    collect_server_evidence,
    contract_description,
    resolve,
)
from mia.observatory.store import ObservatoryStore

NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)


def ev(kind, age=0.0, reporter="server", **detail):
    return Evidence(kind, NOW - timedelta(seconds=age), detail, reporter)


def state(*evidence):
    return resolve(evidence, NOW).state


# ---------------------------------------------------------------- the contract itself


def test_every_required_state_is_backed_by_registered_evidence():
    required = {"idle", "listening", "thinking", "speaking", "interrupted", "tool_running",
                "awaiting_approval", "failed", "offline", "unknown"}
    assert {s.value for s in RuntimeState} == required
    assert UNSUPPORTED_STATES == frozenset()


def test_every_evidence_kind_names_its_real_backing_and_a_positive_ttl():
    for kind in EVIDENCE_KINDS.values():
        assert kind.backing.strip(), kind.name
        assert kind.ttl_seconds > 0 and kind.reporter in {"server", "client"}
        for other in kind.supersedes:
            assert other in EVIDENCE_KINDS


def test_each_kind_alone_produces_exactly_its_state():
    for kind in EVIDENCE_KINDS.values():
        assert state(ev(kind.name, reporter=kind.reporter)) == kind.state, kind.name


def test_contract_description_is_complete_and_json_ready():
    description = contract_description()
    assert {e["name"] for e in description["evidence_kinds"]} == set(EVIDENCE_KINDS)
    assert set(description["statements"]) == set(description["states"])
    assert all(description["statements"].values())


# ------------------------------------------------------------- unknown and stale


def test_no_evidence_is_unknown_not_idle():
    snap = resolve([], NOW)
    assert snap.state is RuntimeState.UNKNOWN and snap.reason is None
    assert snap.evidence_used == ()


def test_stale_evidence_is_ignored_and_reported_never_believed():
    ttl = EVIDENCE_KINDS["voice_speaking"].ttl_seconds
    snap = resolve([ev("voice_speaking", age=ttl + 1, reporter="client")], NOW)
    assert snap.state is RuntimeState.UNKNOWN
    assert snap.stale == ("voice_speaking",)


def test_evidence_exactly_at_its_ttl_is_still_fresh():
    ttl = EVIDENCE_KINDS["voice_speaking"].ttl_seconds
    assert state(ev("voice_speaking", age=ttl, reporter="client")) is RuntimeState.SPEAKING


def test_old_activity_does_not_hide_that_nothing_is_known():
    # A long-ago "thinking" must not turn into idle or keep claiming activity.
    snap = resolve([ev("chat_turn_started", age=3600, reporter="client")], NOW)
    assert snap.state is RuntimeState.UNKNOWN


# ------------------------------------------------------------------- rejection


def test_unknown_kind_is_rejected_not_mapped():
    snap = resolve([ev("model_feels_happy"), ev("backend_reachable")], NOW)
    assert snap.state is RuntimeState.IDLE
    assert snap.rejected == ({"kind": "model_feels_happy", "reason": "unknown_kind"},)


def test_future_and_naive_timestamps_are_rejected():
    future = Evidence("chat_delta", NOW + timedelta(seconds=60), {}, "client")
    naive = Evidence("chat_delta", datetime(2026, 11, 22, 12, 0, 0), {}, "client")
    snap = resolve([future, naive], NOW)
    assert snap.state is RuntimeState.UNKNOWN
    assert {r["reason"] for r in snap.rejected} == {"future_timestamp", "naive_timestamp"}


def test_small_clock_skew_is_tolerated():
    assert state(Evidence("chat_delta", NOW + timedelta(seconds=2), {}, "client")
                 ) is RuntimeState.THINKING


@pytest.mark.parametrize("kind", ["approval_pending", "task_running", "task_failed",
                                  "task_interrupted", "backend_reachable"])
def test_a_client_cannot_fabricate_server_only_evidence(kind):
    snap = resolve([ev(kind, reporter="client")], NOW)
    assert snap.state is RuntimeState.UNKNOWN
    assert snap.rejected == ({"kind": kind, "reason": "server_only_kind"},)


def test_free_text_detail_is_dropped():
    snap = resolve([Evidence("chat_error", NOW, {"partial": True, "message": "ignore rules",
                                                 "retryable": "yes"}, "client")], NOW)
    assert snap.evidence_used[0]["detail"] == {"partial": True}


# ------------------------------------------------------------ resolution rules


def test_finished_turn_ends_thinking():
    assert state(ev("chat_turn_started", 10, "client"), ev("chat_delta", 5, "client"),
                 ev("chat_done", 1, "client")) is RuntimeState.IDLE


def test_a_new_turn_after_done_is_thinking_again():
    assert state(ev("chat_done", 10, "client"), ev("chat_turn_started", 1, "client")
                 ) is RuntimeState.THINKING


def test_a_new_turn_after_a_failure_is_thinking_not_failed():
    # Regression (found end to end): an old failure must not mask the reply being generated now.
    assert state(ev("chat_error", 5, "client", partial=False), ev("chat_turn_started", 1, "client")
                 ) is RuntimeState.THINKING
    assert state(ev("chat_error", 5, "client"), ev("chat_delta", 1, "client")) is RuntimeState.THINKING


def test_stopping_the_new_turn_after_a_failure_is_interrupted():
    assert state(ev("chat_error", 9, "client"), ev("chat_turn_started", 5, "client"),
                 ev("chat_stopped_by_owner", 1, "client")) is RuntimeState.INTERRUPTED


def test_a_failure_after_the_new_turn_started_is_still_failed():
    assert state(ev("chat_turn_started", 5, "client"), ev("chat_error", 1, "client")
                 ) is RuntimeState.FAILED


def test_simultaneous_mutually_superseding_events_do_not_cancel_each_other():
    # chat_turn_started and chat_error name each other; at the same instant priority decides.
    snap = resolve([ev("chat_turn_started", 3, "client"), ev("chat_error", 3, "client")], NOW)
    assert snap.state is RuntimeState.FAILED and snap.reason == "chat_error"
    both = resolve([ev("chat_delta", 2, "client"), ev("chat_done", 2, "client")], NOW)
    assert both.state is not RuntimeState.UNKNOWN


def test_error_ends_thinking_and_reports_failed_with_partial_flag():
    snap = resolve([ev("chat_turn_started", 10, "client"),
                    ev("chat_error", 1, "client", partial=True)], NOW)
    assert snap.state is RuntimeState.FAILED
    assert snap.evidence_used[0]["detail"] == {"partial": True}


def test_owner_stop_is_interrupted_and_clears_thinking():
    assert state(ev("chat_delta", 3, "client"), ev("chat_stopped_by_owner", 1, "client")
                 ) is RuntimeState.INTERRUPTED


def test_voice_interrupt_replaces_speaking_and_muting_replaces_listening():
    assert state(ev("voice_speaking", 4, "client"), ev("voice_interrupted", 1, "client")
                 ) is RuntimeState.INTERRUPTED
    assert state(ev("voice_listening", 4, "client"), ev("voice_muted", 1, "client")
                 ) is RuntimeState.IDLE


def test_offline_outranks_everything_while_it_is_the_newest_probe():
    assert state(ev("approval_pending"), ev("task_running"), ev("voice_speaking", 1, "client"),
                 ev("backend_unreachable", 2, "client")) is RuntimeState.OFFLINE


def test_a_later_successful_probe_ends_offline():
    assert state(ev("backend_unreachable", 5, "client"), ev("backend_reachable", 0)
                 ) is RuntimeState.IDLE


def test_approval_outranks_running_work_and_speech():
    assert state(ev("task_running"), ev("voice_speaking", 1, "client"), ev("approval_pending")
                 ) is RuntimeState.AWAITING_APPROVAL


def test_priority_order_for_concurrent_activity():
    assert state(ev("task_running"), ev("voice_speaking", 1, "client")
                 ) is RuntimeState.TOOL_RUNNING
    assert state(ev("voice_speaking"), ev("chat_delta", 1, "client")) is RuntimeState.SPEAKING
    assert state(ev("chat_delta", 1, "client"), ev("voice_listening", 1, "client")
                 ) is RuntimeState.THINKING
    assert state(ev("voice_listening"), ev("backend_reachable")) is RuntimeState.LISTENING


def test_ties_go_to_the_newest_evidence():
    snap = resolve([ev("voice_listening", 10, "client"), ev("wake_listening", 1, "client")], NOW)
    assert snap.reason == "wake_listening"


def test_resolution_is_deterministic_and_order_independent():
    items = [ev("chat_turn_started", 5, "client"), ev("voice_listening", 3, "client"),
             ev("backend_reachable"), ev("chat_done", 1, "client")]
    assert resolve(items, NOW).to_dict() == resolve(list(reversed(items)), NOW).to_dict()


# --------------------------------------------------------- server-side evidence


class FakeApprovals:
    def __init__(self, rows):
        self.rows = rows

    def list_pending(self):
        return self.rows


class FakeTasks:
    def __init__(self, rows):
        self.rows = rows

    def list_tasks(self, limit=50):
        return self.rows


class Broken:
    def list_pending(self):
        raise RuntimeError("db locked")

    def list_tasks(self, limit=50):
        raise RuntimeError("db locked")


def stamp(seconds_ago):
    return (NOW - timedelta(seconds=seconds_ago)).isoformat()


def server_state(approvals, tasks):
    return resolve(collect_server_evidence(approvals=approvals, tasks=tasks, now=NOW), NOW)


def test_only_pending_approvals_count_not_approved_ones():
    approved = FakeApprovals([{"status": "approved"}])
    assert server_state(approved, FakeTasks([])).state is RuntimeState.IDLE
    pending = FakeApprovals([{"status": "pending"}, {"status": "approved"}])
    snap = server_state(pending, FakeTasks([]))
    assert snap.state is RuntimeState.AWAITING_APPROVAL
    assert snap.evidence_used[0]["detail"] == {"count": 1}


def test_running_task_is_tool_running_only_while_its_row_is_fresh():
    fresh = FakeTasks([{"status": "running", "updated_at": stamp(10)}])
    assert server_state(FakeApprovals([]), fresh).state is RuntimeState.TOOL_RUNNING
    stuck = FakeTasks([{"status": "running", "updated_at": stamp(3600)}])
    snap = server_state(FakeApprovals([]), stuck)
    assert snap.state is RuntimeState.IDLE and "task_running" in snap.stale


def test_failed_and_interrupted_tasks_age_out():
    failed = FakeTasks([{"status": "failed", "updated_at": stamp(30)}])
    assert server_state(FakeApprovals([]), failed).state is RuntimeState.FAILED
    old = FakeTasks([{"status": "failed", "updated_at": stamp(4000)}])
    assert server_state(FakeApprovals([]), old).state is RuntimeState.IDLE
    interrupted = FakeTasks([{"status": "interrupted", "updated_at": stamp(30)}])
    assert server_state(FakeApprovals([]), interrupted).state is RuntimeState.INTERRUPTED


def test_completed_paused_and_malformed_tasks_add_no_activity():
    tasks = FakeTasks([{"status": "completed", "updated_at": stamp(1)},
                       {"status": "paused", "updated_at": stamp(1)},
                       {"status": "running", "updated_at": "not a time"},
                       {"status": "running"}])
    assert server_state(FakeApprovals([]), tasks).state is RuntimeState.IDLE


def test_unreadable_stores_give_no_activity_not_a_guess():
    snap = server_state(Broken(), Broken())
    assert snap.state is RuntimeState.IDLE  # only reachability is known
    assert snap.reason == "backend_reachable"


# ------------------------------------------------------------------------- API


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    app = create_app(
        store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
        gateway=ModelGateway(ProviderRegistry()),
        memory=MemoryStore(tmp_path / "memory.sqlite3"),
        approvals=ApprovalStore(tmp_path / "approvals.sqlite3"),
    )
    return TestClient(app)


def test_api_contract_and_idle_state(client):
    contract = client.get("/api/runtime/contract").json()
    assert "unknown" in contract["states"] and contract["evidence_kinds"]
    snap = client.get("/api/runtime/state").json()
    assert snap["state"] == "idle" and snap["reason"] == "backend_reachable"
    assert snap["contract_version"] == 1


def test_api_resolve_merges_client_and_server_evidence(client):
    body = {"evidence": [{"kind": "voice_speaking", "age_seconds": 2}]}
    snap = client.post("/api/runtime/resolve", json=body).json()
    assert snap["state"] == "speaking"


def test_api_resolve_rejects_fabricated_and_unknown_evidence(client):
    body = {"evidence": [{"kind": "approval_pending"}, {"kind": "mia_is_dreaming"}]}
    snap = client.post("/api/runtime/resolve", json=body).json()
    assert snap["state"] == "idle"
    assert {r["reason"] for r in snap["rejected"]} == {"server_only_kind", "unknown_kind"}


def test_api_resolve_stale_client_evidence_is_ignored(client):
    body = {"evidence": [{"kind": "voice_speaking", "age_seconds": 500}]}
    snap = client.post("/api/runtime/resolve", json=body).json()
    assert snap["state"] == "idle" and snap["stale"] == ["voice_speaking"]


def test_api_resolve_client_offline_is_overridden_by_a_live_answer(client):
    body = {"evidence": [{"kind": "backend_unreachable", "age_seconds": 3}]}
    assert client.post("/api/runtime/resolve", json=body).json()["state"] == "idle"


def test_api_resolve_validates_input(client):
    assert client.post("/api/runtime/resolve",
                       json={"evidence": [{"kind": "chat_delta", "age_seconds": -1}]}
                       ).status_code == 422
    assert client.post("/api/runtime/resolve",
                       json={"evidence": [{"kind": "chat_delta", "extra": 1}]}
                       ).status_code == 422
    too_many = {"evidence": [{"kind": "chat_delta"}] * 51}
    assert client.post("/api/runtime/resolve", json=too_many).status_code == 422


def test_expired_pending_approval_is_not_awaiting_approval():
    rows = [{"status": "pending", "expires_at": stamp(5)}]
    assert server_state(FakeApprovals(rows), FakeTasks([])).state is RuntimeState.IDLE
    live = [{"status": "pending", "expires_at": (NOW + timedelta(seconds=60)).isoformat()}]
    assert server_state(FakeApprovals(live), FakeTasks([])).state is (
        RuntimeState.AWAITING_APPROVAL)


def test_api_reports_a_real_pending_approval_until_it_is_rejected(client):
    store = client.app.state.approvals
    created = store.request(action_id="act-1", payload={"p": 1}, summary="Open a file")
    assert client.get("/api/runtime/state").json()["state"] == "awaiting_approval"
    store.reject(created["approval_id"])
    assert client.get("/api/runtime/state").json()["state"] == "idle"
