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
    monkeypatch.setenv("MIA_COMPUTER_ROOTS", str(tmp_path / "computer-root"))
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
        ("Yes.", False),
        ("Go ahead.", False),
        ("Preview only: add a task for tomorrow.", False),
        ("Don't run the workflow.", False),
        ("Do not add a task.", False),
        ("Don't do it.", False),
        ("I don't want to add a task.", False),
        ("I might run it.", False),
        ("Run the workflow after I approve.", False),
        ("How do I run the workflow?", False),
        ("Could I add a task?", False),
        ("Run me through the workflow.", False),
        ("Explain how to run the workflow.", False),
        ("What does run it mean?", False),
        ("What if I add a task?", False),
        ("Can you run the workflow now?", True),
        ("Run it.", True),
        ("Add a task to my list.", True),
        ("Chala do workflow.", True),
    ],
)
def test_workflow_execution_intent_fails_closed(prompt, expected):
    assert explicit_workflow_execution_requested(prompt) is expected


def test_workflow_computer_handlers_share_the_governed_task_runtime(env):
    client, *_ = env
    items = set(client.get("/api/workflows/capabilities").json()["items"])
    task_handlers = client.app.state.task_engine.handlers
    workflow_handlers = client.app.state.workflow_engine.handlers
    assert {"notify.submit", "personal.add", "personal.complete", "personal.list"} <= items
    assert {"filesystem.write", "filesystem.delete", "terminal.run",
            "apps.launch", "process.terminate"} <= items
    assert set(task_handlers) <= items
    for capability, handler in task_handlers.items():
        assert workflow_handlers[capability] is handler
    assert client.app.state.workflow_engine.permission_policy is client.app.state.permission_policy
    assert client.app.state.workflow_engine.capability_registry is client.app.state.capability_registry

    # Brain may compose only the safe internal subset, never computer actions.
    brain_tool = client.app.state.tool_registry._tools["workflow.preview_internal"]
    allowed = brain_tool.parameters["properties"]["steps"]["items"]["properties"]["capability"]["enum"]
    assert set(allowed) == {"notify.submit", "personal.add", "personal.complete", "personal.list"}


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
    assert all(not p["needs_approval"] and not p["summary"].startswith("PERMISSION DENIED")
               for p in body["preview"])
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
    "computer.approve", "desktop.type_text", "personal.delete", "filesystem.delete_forever",
    "browser.submit_payment", "terminal.root_shell",
])
def test_unregistered_and_unsupported_steps_are_refused_at_plan_time(env, capability):
    client, memory, *_ = env
    r = create(client, [add_step("x"), {"capability": capability, "args": {}}])
    assert r.status_code == 422
    assert "workflows only run internal" in r.json()["detail"]
    assert memory.personal.list(limit=10) == [] and client.get("/api/workflows").json()["items"] == []


def test_computer_action_without_per_action_adapter_needs_an_owner_grant(env):
    client, *_ = env
    response = create(client, [{
        "capability": "browser.fill",
        "args": {"ref": "field-1", "text": "sample"},
    }])
    assert response.status_code == 201, response.text
    preview = response.json()["preview"][0]
    assert preview["allowed"] is False
    assert "owner grant" in preview["summary"]
    assert preview["summary"].startswith("PERMISSION DENIED:")


def test_async_browser_inspector_is_awaited_in_workflow_preview(env):
    client, *_ = env
    response = create(client, [{
        "capability": "browser.navigate",
        "args": {"url": "https://example.com"},
    }])
    assert response.status_code == 201, response.text
    preview = response.json()["preview"][0]
    assert preview["risk"] == "low"
    assert preview["needs_approval"] is False
    assert "arguments" not in preview, "unchanged args are already present on the workflow step"


def test_preview_shows_resolved_arguments_for_owner_approval(env):
    client, _memory, *_ = env
    root_id = client.app.state.computer_files.list_roots()[0]["root_id"]
    response = create(client, [
        add_step("Create reference"),
        {
            "capability": "filesystem.write",
            "args": {
                "root_id": root_id,
                "relative_path": "resolved-reference.txt",
                "content": "Reference: ${steps.1.item_id}",
            },
        },
    ], title="Resolve before approval")
    assert response.status_code == 201, response.text
    wid = response.json()["workflow"]["task_id"]
    waiting = client.post(f"/api/workflows/{wid}/run").json()
    assert waiting["status"] == "awaiting_approval"
    item_id = waiting["steps"][0]["result"]["item_id"]
    preview = client.get(f"/api/workflows/{wid}/preview").json()["preview"]
    assert preview[1]["arguments"]["content"] == f"Reference: {item_id}"
    assert preview[1]["needs_approval"] is True


def test_computer_write_workflow_waits_for_exact_owner_approval(env):
    client, _memory, *_ = env
    root_id = client.app.state.computer_files.list_roots()[0]["root_id"]
    target = client.app.state.computer_files.roots[root_id] / "workflow-note.txt"
    response = create(client, [{
        "capability": "filesystem.write",
        "args": {"root_id": root_id, "relative_path": "workflow-note.txt", "content": "approved text"},
    }], title="Write a note")
    assert response.status_code == 201, response.text
    wid = response.json()["workflow"]["task_id"]
    preview = response.json()["preview"][0]
    assert preview["needs_approval"] is True
    assert not target.exists(), "planning and preview must not write"

    waiting = client.post(f"/api/workflows/{wid}/run").json()
    assert waiting["status"] == "awaiting_approval"
    step = waiting["steps"][0]
    assert step["status"] == "awaiting_approval" and step["approval_id"]
    assert not target.exists(), "a pending approval must have no side effect"

    client.app.state.approvals.approve(step["approval_id"])
    approved = client.get(f"/api/workflows/{wid}").json()
    assert approved["steps"][0]["approval_status"] == "approved"
    completed = client.post(f"/api/workflows/{wid}/run").json()
    assert completed["status"] == "completed", completed
    assert target.read_text(encoding="utf-8") == "approved text"
    assert completed["steps"][0]["status"] == "succeeded"


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
    assert r.json()["preview"][1]["validation_error"] is True
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


def test_approved_workflow_step_survives_stop_all_and_runs_after_resume(env):
    client, _memory, *_ = env
    root_id = client.app.state.computer_files.list_roots()[0]["root_id"]
    target = client.app.state.computer_files.roots[root_id] / "resume-approved.txt"
    wid = create(client, [{
        "capability": "filesystem.write",
        "args": {"root_id": root_id, "relative_path": "resume-approved.txt", "content": "resume safely"},
    }]).json()["workflow"]["task_id"]
    waiting = client.post(f"/api/workflows/{wid}/run").json()
    approval_id = waiting["steps"][0]["approval_id"]
    client.app.state.approvals.approve(approval_id)
    client.post("/api/computer/stop-all")
    paused = client.post(f"/api/workflows/{wid}/run").json()
    assert paused["status"] == "paused"
    current = client.get(f"/api/workflows/{wid}").json()
    assert current["steps"][0]["approval_status"] == "approved"
    assert not target.exists()

    client.post("/api/computer/resume")
    completed = client.post(f"/api/workflows/{wid}/run").json()
    assert completed["status"] == "completed"
    assert target.read_text(encoding="utf-8") == "resume safely"


def test_pending_workflow_approval_survives_app_restart(env):
    client, _memory, store, tmp_path = env
    root_id = client.app.state.computer_files.list_roots()[0]["root_id"]
    target = client.app.state.computer_files.roots[root_id] / "restart-pending.txt"
    wid = create(client, [{
        "capability": "filesystem.write",
        "args": {"root_id": root_id, "relative_path": "restart-pending.txt", "content": "after restart"},
    }]).json()["workflow"]["task_id"]
    waiting = client.post(f"/api/workflows/{wid}/run").json()
    approval_id = waiting["steps"][0]["approval_id"]
    assert waiting["status"] == "awaiting_approval" and not target.exists()

    fresh = TestClient(create_app(
        store=ObservatoryStore(tmp_path / "obs.sqlite3"),
        memory=MemoryStore(tmp_path / "memory.sqlite3"),
    ))
    restored = fresh.get(f"/api/workflows/{wid}").json()
    assert restored["status"] == "awaiting_approval"
    assert restored["steps"][0]["approval_status"] == "pending"
    assert restored["steps"][0]["approval_id"] == approval_id
    fresh.app.state.approvals.approve(approval_id)
    completed = fresh.post(f"/api/workflows/{wid}/run").json()
    assert completed["status"] == "completed"
    assert target.read_text(encoding="utf-8") == "after restart"


def test_expired_workflow_approval_is_replaced_before_execution(env):
    client, _memory, *_ = env
    root_id = client.app.state.computer_files.list_roots()[0]["root_id"]
    target = client.app.state.computer_files.roots[root_id] / "expired-approval.txt"
    wid = create(client, [{
        "capability": "filesystem.write",
        "args": {"root_id": root_id, "relative_path": "expired-approval.txt", "content": "fresh approval"},
    }]).json()["workflow"]["task_id"]
    waiting = client.post(f"/api/workflows/{wid}/run").json()
    old_id = waiting["steps"][0]["approval_id"]
    with client.app.state.approvals._connection() as db:
        db.execute(
            "UPDATE action_approvals SET expires_at=? WHERE approval_id=?",
            ("2000-01-01T00:00:00+00:00", old_id),
        )
    assert client.get(f"/api/workflows/{wid}").json()["steps"][0]["approval_status"] == "expired"

    refreshed = client.post(f"/api/workflows/{wid}/run").json()
    new_id = refreshed["steps"][0]["approval_id"]
    assert refreshed["status"] == "awaiting_approval"
    assert new_id != old_id
    assert client.app.state.approvals.status(new_id) == "pending"
    assert not target.exists()


def test_rejecting_already_approved_workflow_step_revokes_before_execution(env):
    client, _memory, *_ = env
    root_id = client.app.state.computer_files.list_roots()[0]["root_id"]
    target = client.app.state.computer_files.roots[root_id] / "revoke-approved.txt"
    wid = create(client, [{
        "capability": "filesystem.write",
        "args": {"root_id": root_id, "relative_path": "revoke-approved.txt", "content": "must not run"},
    }]).json()["workflow"]["task_id"]
    waiting = client.post(f"/api/workflows/{wid}/run").json()
    approval_id = waiting["steps"][0]["approval_id"]
    client.app.state.approvals.approve(approval_id)

    rejected = client.post(f"/api/workflows/{wid}/reject-step", json={"step": 1})
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "failed"
    assert client.app.state.approvals.status(approval_id) == "rejected"
    assert not target.exists()


@pytest.mark.parametrize("approve_first", [False, True])
def test_workflow_cancel_revokes_pending_or_approved_action_tokens(env, approve_first):
    client, _memory, *_ = env
    root_id = client.app.state.computer_files.list_roots()[0]["root_id"]
    target = client.app.state.computer_files.roots[root_id] / f"cancel-{approve_first}.txt"
    wid = create(client, [{
        "capability": "filesystem.write",
        "args": {"root_id": root_id, "relative_path": target.name, "content": "must not run"},
    }]).json()["workflow"]["task_id"]
    waiting = client.post(f"/api/workflows/{wid}/run").json()
    approval_id = waiting["steps"][0]["approval_id"]
    if approve_first:
        client.app.state.approvals.approve(approval_id)

    cancelled = client.post(f"/api/workflows/{wid}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert client.app.state.approvals.status(approval_id) == "rejected"
    final = client.post(f"/api/workflows/{wid}/run").json()
    assert final["status"] == "cancelled"
    assert not target.exists()


def test_rejecting_pending_computer_approval_fails_workflow_without_side_effect(env):
    client, _memory, *_ = env
    root_id = client.app.state.computer_files.list_roots()[0]["root_id"]
    target = client.app.state.computer_files.roots[root_id] / "rejected.txt"
    wid = create(client, [{
        "capability": "filesystem.write",
        "args": {"root_id": root_id, "relative_path": "rejected.txt", "content": "must not appear"},
    }]).json()["workflow"]["task_id"]
    waiting = client.post(f"/api/workflows/{wid}/run").json()
    assert waiting["status"] == "awaiting_approval"
    assert not target.exists()

    rejected = client.post(f"/api/workflows/{wid}/reject-step", json={"step": 1})
    assert rejected.status_code == 200, rejected.text
    task = rejected.json()
    assert task["status"] == "failed" and task["outcome"] == "owner_rejected"
    assert task["steps"][0]["status"] == "failed"
    assert client.app.state.approvals.status(waiting["steps"][0]["approval_id"]) == "rejected"
    assert not target.exists()


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
