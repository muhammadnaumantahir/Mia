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


def test_runtime_load_rejects_non_finite_or_out_of_range_values():
    import math
    import pytest

    with pytest.raises(ValueError, match="cpu_percent"):
        RuntimeLoad(math.nan, 20)
    with pytest.raises(ValueError, match="memory_percent"):
        RuntimeLoad(20, 101)


def test_autonomy_policy_rejects_unsafe_thresholds():
    import pytest

    with pytest.raises(ValueError, match="max_cpu_percent"):
        AutonomyPolicy(enabled=True, max_cpu_percent=0)
    with pytest.raises(ValueError, match="max_tasks_per_cycle"):
        AutonomyPolicy(enabled=True, max_tasks_per_cycle=11)



def test_dispatcher_preserves_owner_cancellation_during_execution(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task_id = store.create_task(
        title="Cancelable bounded task",
        goal="Simulate a task that is cancelled while its executor is running.",
        source="internal_test",
    )
    store.transition(task_id, "approved", reason="Owner approved the bounded task")
    coordinator = TaskCoordinator(store, AutonomyPolicy(enabled=True))

    def cancel_during_execution(task):
        store.transition(
            task["task_id"],
            "cancelled",
            reason="Owner cancelled while execution was in progress",
            actor="local-user",
        )
        return {"ok": True}

    result = coordinator.dispatch_one(
        load=RuntimeLoad(10, 20),
        executor=cancel_during_execution,
    )

    assert result["status"] == "cancelled"
    assert result["task"]["status"] == "cancelled"
    assert result["reason"] == "task_state_changed_during_execution"
    assert [event["to_status"] for event in store.list_events(task_id)] == [
        "proposed",
        "approved",
        "running",
        "cancelled",
    ]


def test_dispatcher_does_not_mark_cancelled_task_failed_after_executor_error(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task_id = store.create_task(
        title="Cancelled task with late exception",
        goal="Simulate an executor that errors after owner cancellation.",
        source="internal_test",
    )
    store.transition(task_id, "approved", reason="Owner approved the bounded task")
    coordinator = TaskCoordinator(store, AutonomyPolicy(enabled=True))

    def cancel_then_raise(task):
        store.transition(
            task["task_id"],
            "cancelled",
            reason="Owner cancelled while execution was in progress",
            actor="local-user",
        )
        raise RuntimeError("executor returned after cancellation")

    result = coordinator.dispatch_one(
        load=RuntimeLoad(10, 20),
        executor=cancel_then_raise,
    )

    assert result["status"] == "cancelled"
    assert result["task"]["status"] == "cancelled"
    assert result["error_type"] == "RuntimeError"
    assert [event["to_status"] for event in store.list_events(task_id)] == [
        "proposed",
        "approved",
        "running",
        "cancelled",
    ]


def test_claim_next_skips_approved_tasks_outside_validator_scope(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    unsupported = store.create_task(
        title="Unsupported automatic task",
        goal="This task has no automatic execution contract.",
        source="interactive",
        priority=90,
    )
    supported = store.create_task(
        title="Approved system inspection",
        goal="Read local CPU and memory usage.",
        source="interactive",
        priority=50,
        metadata={
            "execution": {
                "tool_name": "system.read_resources",
                "arguments": {},
            }
        },
    )
    store.transition(unsupported, "approved", reason="Owner approved manually")
    store.transition(supported, "approved", reason="Owner approved system inspection")

    claimed = store.claim_next(
        validator=lambda task: isinstance(task["metadata"].get("execution"), dict)
        and task["metadata"]["execution"].get("tool_name") == "system.read_resources"
    )

    assert claimed is not None
    assert claimed["task_id"] == supported
    assert claimed["status"] == "running"
    assert store.get_task(unsupported)["status"] == "approved"
