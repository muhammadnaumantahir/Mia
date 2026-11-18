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


def test_approval_reject_cannot_reject_an_already_approved_action(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.sqlite3")
    approval = store.request(
        action_id="process.launch",
        payload={"app_id": "notepad"},
        summary="Launch Notepad",
    )
    store.approve(approval["approval_id"])
    with pytest.raises(ApprovalError):
        store.reject(approval["approval_id"])
