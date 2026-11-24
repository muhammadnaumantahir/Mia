"""MIA-LIFE-08: workflow composer and checkpoint runtime (real app, real SQLite stores)."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from mia.computer.approvals import ApprovalStore
from mia.life.workflows import make_workflow_engine
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


def test_slow_step_times_out_as_failed_with_unknown_outcome(tmp_path):
    memory = MemoryStore(tmp_path / "m.sqlite3")
    real_list = memory.personal.list

    def slow(**kw):
        time.sleep(0.6)
        return real_list(**kw)

    memory.personal.list = slow
    engine = make_workflow_engine(tmp_path / "w.sqlite3", ApprovalStore(tmp_path / "a.sqlite3"), personal=memory.personal, step_timeout=0.1)
    task = engine.create_task("slow", [{"capability": "personal.list", "args": {}}])
    final = asyncio.run(engine.advance(task["task_id"]))
    assert final["status"] == "failed"
    assert "unknown" in final["steps"][0]["error"]


def test_notify_step_queues_a_notification_with_its_source(env):
    client, memory, *_ = env
    wid = create(client, [{"capability": "notify.submit", "args": {"title": "Review week", "body": "Look at your list"}}]).json()["workflow"]["task_id"]
    assert client.post(f"/api/workflows/{wid}/run").json()["status"] == "completed"
    items = client.get("/api/notifications").json()
    text = str(items)
    assert "Review week" in text and "workflow" in text
