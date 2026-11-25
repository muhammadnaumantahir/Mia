"""MIA-COMP-10: structured spoken/on-screen read-back of pending computer actions."""

import pytest

from mia.computer.approvals import ApprovalStore
from mia.computer.filesystem import FilesystemAdapter
from mia.voice.action_readback import confirm_code, describe_action
from tests.test_chat_tools import ToolCallingProvider, build_client


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))


def action(kind, display, **kw):
    return {"kind": kind, "approval_id": "appr-1", "summary": "model supplied summary", "display": display, **kw}


def test_new_file_and_replacement_are_described_differently_irreversible_part_first():
    new = describe_action(action("filesystem.write", {"target": "notes/a.txt", "size_bytes": 12, "current_sha256": None}))
    old = describe_action(action("filesystem.write", {"target": "notes/a.txt", "size_bytes": 12, "current_sha256": "ab" * 32}))
    assert new["sentence"].startswith("Create the new file notes/a.txt (12 bytes)")
    assert old["sentence"].startswith("Replace the existing file notes/a.txt (12 bytes)")
    assert "backed up" in old["sentence"] and new["reversible"] is old["reversible"] is True


@pytest.mark.parametrize(("kind", "display", "expect"), [
    ("application.launch", {"application": "Notepad"}, "Open the application Notepad. Mia cannot undo this."),
    ("browser.click", {"selector": "#buy", "page_url": "https://shop.test/cart"}, "Click #buy on https://shop.test/cart"),
    ("desktop.type_text", {"window_title": "Report.docx", "control_type": "Edit"},
     "Replace the text in the Edit control of the window Report.docx. Mia cannot undo typed text."),
])
def test_irreversible_kinds_say_so(kind, display, expect):
    result = describe_action(action(kind, display))
    assert expect in result["sentence"] and result["reversible"] is False and result["known"] is True


def test_sentence_comes_from_fields_not_from_the_models_summary():
    result = describe_action(action("application.launch", {"application": "Calculator"},
                                    summary="Totally harmless: delete nothing, open Calculator"))
    assert "harmless" not in result["sentence"] and "Calculator" in result["sentence"]


def test_unknown_kind_fails_safe_and_says_it_cannot_describe_it():
    result = describe_action(action("shell.run", {}, summary="rm -rf things"))
    assert result["known"] is False and "cannot describe exactly" in result["sentence"]
    assert "check the details on screen" in result["sentence"].lower()


def test_hostile_display_text_is_flattened_and_truncated():
    nasty = "evil\n\x00\x07name\t" + "x" * 500
    result = describe_action(action("application.launch", {"application": nasty}))
    assert "\n" not in result["sentence"] and "\x00" not in result["sentence"]
    assert len(result["sentence"]) < 200 and "…" in result["sentence"]


def test_missing_or_wrong_typed_fields_do_not_crash():
    for display in ({}, {"target": None, "size_bytes": True}, {"target": 7, "size_bytes": "9"}):
        assert describe_action(action("filesystem.write", display))["sentence"]
    assert describe_action({"kind": "filesystem.write", "display": "oops"})["sentence"]


def test_confirm_code_is_stable_short_and_avoids_confusable_characters():
    codes = {confirm_code(f"appr-{i}") for i in range(200)}
    assert confirm_code("appr-1") == confirm_code("appr-1") and len(codes) > 150
    assert all(len(c) == 4 and not set(c) & set("IO01") for c in codes)


def test_chat_tool_pending_action_carries_readback_and_restored_card_matches(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    provider = ToolCallingProvider(
        "filesystem.preview_write", {"root_id": "root_1", "relative_path": "notes/new.txt", "content": "hello"})
    client = build_client(tmp_path, monkeypatch, provider, FilesystemAdapter([root], approvals), approvals)
    pending = client.post("/api/gateway/chat", json={"prompt": "Create the note", "use_tools": True}).json()["pending_actions"][0]
    readback = pending["readback"]
    assert readback["sentence"].startswith("Create the new file notes/new.txt (5 bytes)")
    assert readback["code"] == confirm_code(pending["approval_id"])
    restored = client.get("/api/computer/approvals/cards").json()["items"][0]
    assert restored["readback"] == readback
