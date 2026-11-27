import pytest

from mia.computer.approvals import ApprovalError, ApprovalStore


def test_approval_can_be_rejected_once_and_cannot_be_reused(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.sqlite3")
    approval = store.request(
        action_id="filesystem.write",
        payload={"path": "notes.txt", "content": "hello"},
        summary="Write notes.txt",
    )
    rejected = store.reject(approval["approval_id"])
    assert rejected["status"] == "rejected"
    with pytest.raises(ApprovalError):
        store.approve(approval["approval_id"])


def test_approved_action_can_be_revoked_before_consumption(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.sqlite3")
    payload = {"app_id": "notepad"}
    approval = store.request(
        action_id="process.launch",
        payload=payload,
        summary="Launch Notepad",
    )
    store.approve(approval["approval_id"])
    revoked = store.reject(approval["approval_id"])
    assert revoked["status"] == "rejected"
    assert store.status(approval["approval_id"]) == "rejected"
    with pytest.raises(ApprovalError):
        store.consume(
            approval_id=approval["approval_id"],
            action_id="process.launch",
            payload=payload,
        )


def test_consumed_action_cannot_be_revoked_or_reused(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.sqlite3")
    payload = {"app_id": "notepad"}
    approval = store.request(
        action_id="process.launch",
        payload=payload,
        summary="Launch Notepad",
    )
    store.approve(approval["approval_id"])
    store.consume(
        approval_id=approval["approval_id"],
        action_id="process.launch",
        payload=payload,
    )
    with pytest.raises(ApprovalError):
        store.reject(approval["approval_id"])
