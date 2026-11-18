from mia.agent.tasks import (
    AutonomyPolicy,
    RuntimeLoad,
    TaskCoordinator,
    TaskStore,
    TaskTransitionError,
)


def test_task_lifecycle_and_append_only_trace(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task_id = store.create_task(
        title="Inspect research",
        goal="Review the local thesis notes for missing citations.",
        source="interactive",
    )
    assert store.get_task(task_id)["status"] == "proposed"

    store.transition(task_id, "approved", reason="Owner approved task")
    claimed = store.claim_next()
    assert claimed["task_id"] == task_id
    assert claimed["status"] == "running"

    completed = store.transition(
        task_id,
        "completed",
        reason="Inspection finished",
        payload={"findings": 2},
    )
    assert completed["status"] == "completed"
    assert [e["to_status"] for e in store.list_events(task_id)] == [
        "proposed",
        "approved",
        "running",
        "completed",
    ]

    try:
        store.transition(task_id, "running", reason="Invalid restart")
    except TaskTransitionError:
        pass
    else:
        raise AssertionError("Completed tasks must be terminal")


def test_dispatcher_is_opt_in_and_resource_aware(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task_id = store.create_task(
        title="Run internal test",
        goal="Run a non-mutating self-check.",
        source="internal_test",
    )
    store.transition(task_id, "approved", reason="Approved internal self-check")

    disabled = TaskCoordinator(store, AutonomyPolicy(enabled=False))
    result = disabled.dispatch_one(load=RuntimeLoad(10, 20), executor=lambda task: {})
    assert result == {"status": "paused", "reason": "autonomy_disabled"}
    assert store.get_task(task_id)["status"] == "approved"

    enabled = TaskCoordinator(
        store,
        AutonomyPolicy(enabled=True, max_cpu_percent=70, max_memory_percent=80),
    )
    hot = enabled.dispatch_one(
        load=RuntimeLoad(85, 40),
        executor=lambda task: {"ok": True},
    )
    assert hot["reason"] == "resource_limit"
    assert store.get_task(task_id)["status"] == "approved"

    result = enabled.dispatch_one(
        load=RuntimeLoad(30, 40),
        executor=lambda task: {"check": "passed"},
    )
    assert result["status"] == "completed"
    assert result["task"]["task_id"] == task_id


def test_dispatcher_preserves_waiting_for_approval_state(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task_id = store.create_task(
        title="Send a prepared message",
        goal="Prepare the message and wait for explicit owner approval before sending.",
        source="interactive",
    )
    store.transition(task_id, "approved", reason="Owner approved preparation only")
    coordinator = TaskCoordinator(store, AutonomyPolicy(enabled=True))

    result = coordinator.dispatch_one(
        load=RuntimeLoad(10, 20),
        executor=lambda task: {
            "status": "waiting",
            "reason": "External message send requires separate approval",
            "approval_id": "approval-123",
        },
    )

    assert result["status"] == "waiting"
    assert result["task"]["status"] == "waiting"
    assert result["outcome"]["approval_id"] == "approval-123"
    assert [event["to_status"] for event in store.list_events(task_id)] == [
        "proposed",
        "approved",
        "running",
        "waiting",
    ]


def test_dispatcher_rejects_non_object_executor_result(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task_id = store.create_task(
        title="Run bounded check",
        goal="Run a bounded internal check.",
        source="internal_test",
    )
    store.transition(task_id, "approved", reason="Approved check")
    coordinator = TaskCoordinator(store, AutonomyPolicy(enabled=True))

    result = coordinator.dispatch_one(
        load=RuntimeLoad(10, 20),
        executor=lambda task: "completed",
    )

    assert result["status"] == "failed"
    assert store.get_task(task_id)["status"] == "failed"
