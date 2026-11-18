from mia.computer.approvals import ApprovalError, ApprovalStore
from mia.computer.filesystem import FilesystemAccessError, FilesystemAdapter


def build_fs(tmp_path):
    root = tmp_path / "allowed"
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    return FilesystemAdapter([root], approvals), approvals, root


def test_filesystem_is_scoped_to_configured_root(tmp_path):
    fs, _, root = build_fs(tmp_path)
    (root / "hello.txt").write_text("Hello", encoding="utf-8")
    assert fs.read_text("root_1", "hello.txt") == "Hello"
    try:
        fs.read_text("root_1", "../outside.txt")
    except FilesystemAccessError:
        pass
    else:
        raise AssertionError("Path traversal must be blocked")


def test_file_write_requires_exact_one_time_approval(tmp_path):
    fs, approvals, root = build_fs(tmp_path)
    preview = fs.preview_write("root_1", "notes/hello.txt", "Hello Mia")
    approvals.approve(preview["approval_id"])

    try:
        fs.execute_write(
            approval_id=preview["approval_id"],
            root_id="root_1",
            relative_path="notes/hello.txt",
            content="Different content",
        )
    except ApprovalError as exc:
        assert "changed" in str(exc).lower()
    else:
        raise AssertionError("Changed payload must not use the old approval")

    approved = fs.preview_write("root_1", "notes/hello.txt", "Hello Mia")
    approvals.approve(approved["approval_id"])
    result = fs.execute_write(
        approval_id=approved["approval_id"],
        root_id="root_1",
        relative_path="notes/hello.txt",
        content="Hello Mia",
    )
    assert result["size_bytes"] == 9
    assert (root / "notes" / "hello.txt").read_text(encoding="utf-8") == "Hello Mia"
    try:
        fs.execute_write(
            approval_id=approved["approval_id"],
            root_id="root_1",
            relative_path="notes/hello.txt",
            content="Hello Mia",
        )
    except ApprovalError as exc:
        assert "consumed" in str(exc).lower()
    else:
        raise AssertionError("Approval must be one-time use")


def test_credential_files_and_git_metadata_are_blocked(tmp_path):
    fs, _, _ = build_fs(tmp_path)
    for name in (".env", "private.pem", ".git/config"):
        try:
            fs.preview_write("root_1", name, "secret")
        except FilesystemAccessError:
            continue
        raise AssertionError(f"{name} should be blocked")


def test_filesystem_read_blocks_credentials_and_private_key_files(tmp_path):
    fs, _, root = build_fs(tmp_path)
    (root / ".env").write_text("API_KEY=secret", encoding="utf-8")
    (root / ".env.production").write_text("TOKEN=secret", encoding="utf-8")
    (root / "private.pem").write_text("private-key", encoding="utf-8")
    (root / ".ssh").mkdir()
    (root / ".ssh" / "id_ed25519").write_text("private-key", encoding="utf-8")

    for name in (".env", ".env.production", "private.pem", ".ssh/id_ed25519"):
        try:
            fs.read_text("root_1", name)
        except FilesystemAccessError:
            continue
        raise AssertionError(f"{name} should be blocked")
