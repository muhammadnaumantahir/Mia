"""MIA-LIFE-08: workflow composer and checkpoint runtime (real app, real SQLite stores)."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from mia.computer.approvals import ApprovalStore
from mia.life.workflows import explicit_workflow_execution_requested, make_workflow_engine
from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    store = ObservatoryStore(tmp_path / "obs.sqlite3")
    client = TestClient(create_app(store=store, memory=memory))
    return client, memory, store, tmp_path


def create(client, steps, title="Plan my week"):
    r = client.post("/api/workflows", json={"title": title, "steps": steps})
    return r


def add_step(title, **extra):
    return {"capability": "personal.add", "args": {"kind": "task", "title": title, **extra}}


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Plan a workflow to add a task.", False),
        ("Preview only: add a task for tomorrow.", False),
        ("Don't run the workflow.", False),
        ("Do not add a task.", False),
        ("Don't do it.", False),
        ("Run the workflow after I approve.", False),
        ("How do I run the workflow?", False),
        ("Could I add a task?", False),
        ("Run me through the workflow.", False),
        ("What if I add a task?", False),
        ("Can you run the workflow now?", True),
        ("Run it.", True),
        ("Add a task to my list.", True),
        ("Chala do workflow.", True),
    ],
)
def test_workflow_execution_intent_fails_closed(prompt, expected):
    assert explicit_workflow_execution_requested(prompt) is expected


def test_capabilities_are_only_the_internal_ones(env):
    client, *_ = env
    items = client.get("/api/workflows/capabilities").json()["items"]
    assert items == ["notify.submit", "personal.add", "personal.complete", "personal.list"]


def test_preview_changes_nothing_and_run_executes_in_order_with_step_references(env):
    client, memory, store, _ = env
    r = create(client, [
        add_step("Draft agenda"),
        {"capability": "personal.complete", "args": {"item_id": "${steps.1.item_id}"}, "description": "tick it off"},
        {"capability": "personal.list", "args": {"status": "done"}},
    ])
    assert r.status_code == 201, r.text
    body = r.json()
    wid = body["workflow"]["task_id"]
    assert [p["capability"] for p in body["preview"]] == ["personal.add", "personal.complete", "personal.list"]
    assert memory.personal.list(limit=50) == [], "creating and previewing must not act"
    assert client.get(f"/api/workflows/{wid}/preview").status_code == 200
    done = client.post(f"/api/workflows/{wid}/run").json()
    assert done["status"] == "completed", done
    items = memory.personal.list(status="done", limit=10)
    assert [i["title"] for i in items] == ["Draft agenda"]
    report = client.get(f"/api/workflows/{wid}/report").json()
    assert report["status"] == "completed"
    events = client.get(f"/api/workflows/{wid}/events").json()["items"]
    assert any(e["kind"] == "step_done" for e in events)
    assert any(e["category"] == "workflow" for e in store.list_events())


@pytest.mark.parametrize("capability", [
    "terminal.run", "filesystem.write", "filesystem.delete", "browser.fill", "desktop.type_text",
    "personal.delete", "apps.launch", "process.terminate",
])
def test_external_and_destructive_steps_are_refused_at_plan_time(env, capability):
    client, memory, *_ = env
    r = create(client, [add_step("x"), {"capability": capability, "args": {}}])
    assert r.status_code == 422
    assert "workflows only run internal" in r.json()["detail"]
    assert memory.personal.list(limit=10) == [] and client.get("/api/workflows").json()["items"] == []


def test_unknown_capability_extra_fields_and_forward_references_are_rejected(env):
    client, *_ = env
    assert create(client, [{"capability": "made.up", "args": {}}]).status_code == 422
    assert client.post("/api/workflows", json={"title": "t", "steps": [{"capability": "personal.list", "approve": True}]}).status_code == 422
    assert create(client, [{"capability": "personal.complete", "args": {"item_id": "${steps.2.item_id}"}}, add_step("a")]).status_code == 422
    assert create(client, []).status_code == 422
    assert create(client, [{"capability": "personal.list", "args": {}}] * 21).status_code == 422


def test_workflow_cannot_approve_or_grant(env):
    client, memory, *_ = env
    assert create(client, [{"capability": "computer.approve", "args": {}}]).status_code == 422
    assert client.get("/api/computer/permissions").json().get("items", []) == []


def test_bad_step_arguments_fail_the_step_and_stop_the_workflow(env):
    client, memory, *_ = env
    r = create(client, [add_step("First"), {"capability": "personal.complete", "args": {"item_id": "nope"}}, add_step("Never")])
    wid = r.json()["workflow"]["task_id"]
    final = client.post(f"/api/workflows/{wid}/run").json()
    assert final["status"] == "failed"
    assert [i["title"] for i in memory.personal.list(limit=10)] == ["First"], "steps after a failure never run"
    assert [s["status"] for s in final["steps"]] == ["succeeded", "failed", "skipped"]


def test_duplicate_add_is_reported_not_silently_repeated(env):
    client, memory, *_ = env
    memory.personal.add(kind="task", title="Buy milk")
    wid = create(client, [add_step("Buy milk")]).json()["workflow"]["task_id"]
    final = client.post(f"/api/workflows/{wid}/run").json()
    assert final["status"] == "failed" and len(memory.personal.list(limit=10)) == 1


def test_pause_cancel_and_retry_controls(env):
    client, memory, *_ = env
    wid = create(client, [add_step("A"), add_step("B")]).json()["workflow"]["task_id"]
    assert client.post(f"/api/workflows/{wid}/cancel").json()["status"] == "cancel_requested"
    run = client.post(f"/api/workflows/{wid}/run").json()
    assert run["status"] == "cancelled" and memory.personal.list(limit=10) == []
    assert client.post("/api/workflows/nope/cancel").status_code == 404
    assert client.post("/api/workflows/nope/run").status_code == 404


def test_stop_all_pauses_a_workflow_and_nothing_runs_until_resume(env):
    client, memory, *_ = env
    wid = create(client, [add_step("Held")]).json()["workflow"]["task_id"]
    assert client.post("/api/computer/stop-all").status_code == 200
    held = client.post(f"/api/workflows/{wid}/run").json()
    assert held["status"] == "paused" and memory.personal.list(limit=10) == []
    client.post("/api/computer/resume")
    assert client.post(f"/api/workflows/{wid}/run").json()["status"] == "completed"


def test_progress_survives_restart_and_interrupted_step_is_not_silently_rerun(env):
    client, memory, store, tmp_path = env
    wid = create(client, [add_step("One"), add_step("Two")]).json()["workflow"]["task_id"]
    engine = client.app.state.workflow_engine
    engine._set_step(wid, 1, status="running")  # simulate a crash mid-step
    engine._set_task(wid, status="running")
    fresh = make_workflow_engine(engine.database_path, ApprovalStore(tmp_path / "x.sqlite3"), personal=memory.personal)
    assert fresh.get_task(wid)["steps"][0]["status"] == "interrupted"
    task = asyncio.run(fresh.advance(wid))
    assert memory.personal.list(limit=10) == [], "unknown outcome must wait for an explicit retry"
    assert task["steps"][0]["status"] == "interrupted"
    fresh.retry_step(wid, 1)
    assert asyncio.run(fresh.advance(wid))["status"] == "completed"


def test_slow_side_effect_waits_for_worker_then_reconciles_without_duplicate(tmp_path):
    memory = MemoryStore(tmp_path / "m.sqlite3")
    real_add = memory.personal.add
    calls = []

    def slow_add(**kw):
        calls.append("started")
        time.sleep(0.25)
        return real_add(**kw)

    memory.personal.add = slow_add
    engine = make_workflow_engine(
        tmp_path / "w.sqlite3", ApprovalStore(tmp_path / "a.sqlite3"),
        personal=memory.personal, step_timeout=0.05,
    )
    task = engine.create_task("slow add", [add_step("Only once")])
    # Keep one event loop alive: asyncio.run() waits for its default executor at
    # shutdown, which would hide the still-running worker this test needs to observe.
    loop = asyncio.new_event_loop()
    try:
        timed_out = loop.run_until_complete(engine.advance(task["task_id"]))
        assert timed_out["status"] == "interrupted"
        assert timed_out["outcome"] == "unknown"
        assert timed_out["steps"][0]["status"] == "interrupted"
        assert memory.personal.list(limit=10) == []

        waiting = engine.reconcile_timeout(task["task_id"], 1)
        assert waiting["status"] == "waiting"
        # A second run must not start another worker while the first is unresolved.
        still_waiting = loop.run_until_complete(engine.advance(task["task_id"]))
        assert still_waiting["status"] == "interrupted"
        loop.run_until_complete(asyncio.sleep(0.3))

        reconciled = engine.reconcile_timeout(task["task_id"], 1)
        assert reconciled["status"] == "paused"
        assert reconciled["steps"][0]["status"] == "succeeded"
        assert len(calls) == 1
        finished = loop.run_until_complete(engine.advance(task["task_id"]))
        assert finished["status"] == "completed"
        assert [item["title"] for item in memory.personal.list(limit=10)] == ["Only once"]
    finally:
        loop.close()


def test_restart_requires_owner_reconciliation_before_retry(tmp_path):
    memory = MemoryStore(tmp_path / "m.sqlite3")
    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    engine = make_workflow_engine(tmp_path / "w.sqlite3", approvals, personal=memory.personal)
    task = engine.create_task("after restart", [add_step("Owner checked")])
    engine._set_step(task["task_id"], 1, status="interrupted",
                     error="Step timed out; outcome is unknown.")
    engine._set_task(task["task_id"], status="interrupted", outcome="unknown")

    fresh = make_workflow_engine(tmp_path / "w.sqlite3", approvals, personal=memory.personal)
    refused = asyncio.run(fresh.advance(task["task_id"]))
    assert refused["status"] == "interrupted"
    assert memory.personal.list(limit=10) == []

    with pytest.raises(ValueError, match="must be reconciled"):
        fresh.retry_step(task["task_id"], 1)
    with pytest.raises(Exception, match="decision must be"):
        fresh.reconcile_timeout(task["task_id"], 1)
    retryable = fresh.reconcile_timeout(task["task_id"], 1, decision="not_applied")
    assert retryable["status"] == "paused"
    assert retryable["steps"][0]["status"] == "pending"
    completed = asyncio.run(fresh.advance(task["task_id"]))
    assert completed["status"] == "completed"
    assert [item["title"] for item in memory.personal.list(limit=10)] == ["Owner checked"]


def test_restart_owner_can_confirm_applied_without_repeating_side_effect(tmp_path):
    memory = MemoryStore(tmp_path / "m.sqlite3")
    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    memory.personal.add(kind="task", title="Already applied", actor="workflow")
    engine = make_workflow_engine(tmp_path / "w.sqlite3", approvals, personal=memory.personal)
    task = engine.create_task("after restart", [add_step("Already applied")])
    engine._set_step(task["task_id"], 1, status="interrupted",
                     error="Step timed out; outcome is unknown.")
    engine._set_task(task["task_id"], status="interrupted", outcome="unknown")

    fresh = make_workflow_engine(tmp_path / "w.sqlite3", approvals, personal=memory.personal)
    reconciled = fresh.reconcile_timeout(task["task_id"], 1, decision="applied")
    assert reconciled["steps"][0]["status"] == "succeeded"
    assert asyncio.run(fresh.advance(task["task_id"]))["status"] == "completed"
    assert len(memory.personal.list(limit=10)) == 1


def test_cancel_waits_for_unknown_step_reconciliation(env):
    client, memory, *_ = env
    wid = create(client, [add_step("Must not run")]).json()["workflow"]["task_id"]
    engine = client.app.state.workflow_engine
    engine._set_step(wid, 1, status="interrupted", error="unknown")
    engine._set_task(wid, status="interrupted", outcome="unknown")

    cancelled = client.post(f"/api/workflows/{wid}/cancel")
    assert cancelled.status_code == 200
    current = client.get(f"/api/workflows/{wid}").json()
    assert current["status"] == "interrupted" and current["cancel_requested"] == 1
    assert memory.personal.list(limit=10) == []

    final = engine.reconcile_timeout(wid, 1, decision="not_applied")
    assert final["status"] == "cancelled"
    assert memory.personal.list(limit=10) == []


def test_reconcile_step_api_requires_explicit_post_restart_decision(env):
    client, *_ = env
    wid = create(client, [add_step("Reconcile me")]).json()["workflow"]["task_id"]
    engine = client.app.state.workflow_engine
    engine._set_step(wid, 1, status="interrupted", error="unknown")
    engine._set_task(wid, status="interrupted", outcome="unknown")
    direct_retry = client.post(f"/api/workflows/{wid}/retry-step", json={"step": 1})
    assert direct_retry.status_code == 422
    missing = client.post(f"/api/workflows/{wid}/reconcile-step", json={"step": 1})
    assert missing.status_code == 422
    applied = client.post(f"/api/workflows/{wid}/reconcile-step",
                          json={"step": 1, "decision": "not_applied"})
    assert applied.status_code == 200
    assert applied.json()["steps"][0]["status"] == "pending"


def test_notify_step_queues_a_notification_with_its_source(env):
    client, memory, *_ = env
    wid = create(client, [{"capability": "notify.submit", "args": {"title": "Review week", "body": "Look at your list"}}]).json()["workflow"]["task_id"]
    assert client.post(f"/api/workflows/{wid}/run").json()["status"] == "completed"
    items = client.get("/api/notifications").json()
    text = str(items)
    assert "Review week" in text and "workflow" in text
