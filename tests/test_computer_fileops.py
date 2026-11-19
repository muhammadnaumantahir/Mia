import pytest
from fastapi.testclient import TestClient

from mia.computer.approvals import ApprovalError, ApprovalStore
from mia.computer.fileops import FileOperationError, FileOperations
from mia.computer.filesystem import FilesystemAccessError, FilesystemAdapter
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


@pytest.fixture
def env(tmp_path):
    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    fs = FilesystemAdapter([tmp_path / "root"], approvals)
    return fs, FileOperations(fs), approvals, tmp_path / "root"


def run(ops, approvals, op, *args, **kw):
    preview = ops.preview(op, *args, **kw)
    approvals.approve(preview["approval_id"])
    return ops.execute(op, preview["approval_id"], *args, **kw)


def test_mkdir_rename_copy_move_verified(env):
    fs, ops, approvals, root = env
    assert run(ops, approvals, "mkdir", "root_1", "proj/sub")["verified"]
    (root / "proj" / "a.txt").write_text("hello", encoding="utf-8")
    assert run(ops, approvals, "copy", "root_1", "proj/a.txt", destination_path="proj/b.txt")["verified"]
    assert run(ops, approvals, "rename", "root_1", "proj/b.txt", destination_path="proj/c.txt")["verified"]
    assert run(ops, approvals, "move", "root_1", "proj/c.txt", destination_path="proj/sub/c.txt")["verified"]
    assert (root / "proj/sub/c.txt").read_text() == "hello" and not (root / "proj/c.txt").exists()
    assert run(ops, approvals, "copy", "root_1", "proj", destination_path="proj2")["verified"]
    assert (root / "proj2/sub/c.txt").exists()


def test_no_silent_overwrite_and_validation(env):
    fs, ops, approvals, root = env
    (root / "a.txt").write_text("a")
    (root / "b.txt").write_text("b")
    with pytest.raises(FileOperationError):
        ops.preview("copy", "root_1", "a.txt", destination_path="b.txt")
    with pytest.raises(ValueError):
        ops.preview("rename", "root_1", "a.txt", destination_path="x/b2.txt")
    with pytest.raises(FileOperationError):
        ops.preview("mkdir", "root_1", "a.txt")
    (root / "d").mkdir()
    with pytest.raises(FileOperationError):
        ops.preview("move", "root_1", "d", destination_path="d/inner")
    with pytest.raises(FileNotFoundError):
        ops.preview("copy", "root_1", "missing.txt", destination_path="z.txt")
    with pytest.raises(ValueError):
        ops.preview("format", "root_1", "a.txt")


def test_path_and_credential_protection(env):
    fs, ops, approvals, root = env
    (root / ".env").write_text("TOKEN=x")
    (root / "ok.txt").write_text("ok")
    for rel in ("../outside.txt", "/abs/path"):
        with pytest.raises(FilesystemAccessError):
            ops.preview("delete", "root_1", rel)
    with pytest.raises(FilesystemAccessError):
        ops.preview("copy", "root_1", ".env", destination_path="env-copy.txt")
    with pytest.raises(FilesystemAccessError):
        ops.preview("copy", "root_1", "ok.txt", destination_path="id_rsa")
    with pytest.raises(FilesystemAccessError):
        ops.preview("delete", "root_1", ".mia-recovery")
    with pytest.raises(FilesystemAccessError):
        ops.preview("delete", "root_1", ".")
    with pytest.raises(FilesystemAccessError):
        ops.preview("mkdir", "root_9", "x")


def test_symlinks_refused(env, tmp_path):
    fs, ops, approvals, root = env
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "s.txt").write_text("secret")
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(FilesystemAccessError):
        ops.preview("copy", "root_1", "link", destination_path="copy")
    (root / "tree").mkdir()
    (root / "tree" / "inner").symlink_to(outside, target_is_directory=True)
    with pytest.raises(FilesystemAccessError):
        ops.preview("copy", "root_1", "tree", destination_path="tree2")


def test_approval_bound_to_state_and_one_time(env):
    fs, ops, approvals, root = env
    (root / "a.txt").write_text("v1")
    preview = ops.preview("delete", "root_1", "a.txt")
    with pytest.raises(ApprovalError):  # not approved yet
        ops.execute("delete", preview["approval_id"], "root_1", "a.txt")
    approvals.approve(preview["approval_id"])
    (root / "a.txt").write_text("v2-changed-after-approval")
    with pytest.raises(ApprovalError):
        ops.execute("delete", preview["approval_id"], "root_1", "a.txt")
    assert (root / "a.txt").exists()
    # an approval for delete cannot authorize a move
    p2 = ops.preview("delete", "root_1", "a.txt")
    approvals.approve(p2["approval_id"])
    with pytest.raises(ApprovalError):
        ops.execute("move", p2["approval_id"], "root_1", "a.txt", destination_path="m.txt")


def test_delete_goes_to_recovery_and_restore(env):
    fs, ops, approvals, root = env
    (root / "dir").mkdir()
    (root / "dir" / "f.txt").write_text("data")
    result = run(ops, approvals, "delete", "root_1", "dir")
    rid = result["recovery_id"]
    assert result["verified"] and not (root / "dir").exists()
    assert [i["original_relative_path"] for i in ops.list_recovery("root_1")] == ["dir"]
    assert ".mia-recovery" not in [e["name"] for e in fs.list_directory("root_1")]
    assert fs.search_filenames("root_1", "f.txt") == []  # recovery area hidden from search
    (root / "dir").mkdir()
    with pytest.raises(FileOperationError):
        ops.preview("restore", "root_1", recovery_id=rid)
    (root / "dir").rmdir()
    assert run(ops, approvals, "restore", "root_1", recovery_id=rid)["verified"]
    assert (root / "dir/f.txt").read_text() == "data" and ops.list_recovery("root_1") == []
    with pytest.raises(FileNotFoundError):
        ops.preview("restore", "root_1", recovery_id="deadbeef")


def test_write_creates_backup_before_overwrite(env):
    fs, ops, approvals, root = env
    p = fs.preview_write("root_1", "n.txt", "one")
    approvals.approve(p["approval_id"])
    first = fs.execute_write(approval_id=p["approval_id"], root_id="root_1",
                             relative_path="n.txt", content="one")
    assert first["backup"] is None
    p = fs.preview_write("root_1", "n.txt", "two")
    approvals.approve(p["approval_id"])
    second = fs.execute_write(approval_id=p["approval_id"], root_id="root_1",
                              relative_path="n.txt", content="two")
    assert (root / second["backup"]).read_text() == "one"
    assert (root / "n.txt").read_text() == "two"


def test_read_only_tools(env):
    fs, ops, approvals, root = env
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\nprint('needle here')\n")
    (root / "src" / "b.py").write_text("x = 1\nprint('needle there')\n")
    (root / "dup1.bin").write_bytes(b"same-bytes")
    (root / "dup2.bin").write_bytes(b"same-bytes")
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    (root / "README.md").write_text("# Title\nkey = AKIAABCDEFGHIJKLMNOP\n")
    (root / ".env").write_text("needle=secret")
    (root / "tests").mkdir()

    meta = ops.metadata("root_1", "src/a.py")
    assert meta["kind"] == "file" and meta["extension"] == ".py" and len(meta["sha256"]) == 64
    hits = ops.search_content("root_1", "needle")
    assert {h["relative_path"] for h in hits} == {"src/a.py", "src/b.py"}  # .env excluded
    cmp = ops.compare_files("root_1", "src/a.py", "src/b.py")
    assert not cmp["identical"] and any("needle there" in line for line in cmp["diff"])
    assert ops.compare_files("root_1", "dup1.bin", "dup2.bin")["identical"]
    dups = ops.find_duplicates("root_1")
    assert dups["groups"][0]["files"] == ["dup1.bin", "dup2.bin"]
    assert dups["reclaimable_bytes"] == len(b"same-bytes")
    proj = ops.inspect_project("root_1")
    assert proj["project_types"] == ["python"] and proj["has_tests"]
    assert proj["readme"]["file"] == "README.md"
    assert "AKIAABCDEFGHIJKLMNOP" not in proj["readme"]["excerpt"]


def test_encoding_detection(env):
    fs, ops, approvals, root = env
    (root / "u8.txt").write_bytes("héllo".encode("utf-8-sig"))
    (root / "u16.txt").write_bytes("héllo".encode("utf-16"))
    (root / "cp.txt").write_bytes("café".encode("cp1252"))
    (root / "bin.dat").write_bytes(b"\x00\x01\x02binary")
    assert fs.read_text_auto("root_1", "u8.txt") == {
        "content": "héllo", "encoding": "utf-8-sig", "fallback": False}
    assert fs.read_text_auto("root_1", "u16.txt")["content"] == "héllo"
    cp = fs.read_text_auto("root_1", "cp.txt")
    assert cp["content"] == "café" and cp["fallback"] is True
    with pytest.raises(ValueError):
        fs.read_text_auto("root_1", "bin.dat")


def test_file_operation_api(tmp_path):
    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    fs = FilesystemAdapter([tmp_path / "root"], approvals)
    (tmp_path / "root" / "a.txt").write_text("hi")
    client = TestClient(create_app(
        store=ObservatoryStore(tmp_path / "o.sqlite3"), filesystem=fs, approvals=approvals))
    body = {"op": "delete", "root_id": "root_1", "relative_path": "a.txt"}
    preview = client.post("/api/computer/files/ops/preview", json=body)
    assert preview.status_code == 201
    aid = preview.json()["approval_id"]
    blocked = client.post("/api/computer/files/ops/execute", json={**body, "approval_id": aid})
    assert blocked.status_code == 409 and (tmp_path / "root" / "a.txt").exists()
    assert client.post(f"/api/computer/approvals/{aid}/approve").status_code == 200
    done = client.post("/api/computer/files/ops/execute", json={**body, "approval_id": aid})
    assert done.status_code == 200 and done.json()["verified"] is True
    assert not (tmp_path / "root" / "a.txt").exists()
    rec = client.get("/api/computer/files/recovery", params={"root_id": "root_1"}).json()["items"]
    assert rec[0]["original_relative_path"] == "a.txt"
    assert client.post("/api/computer/files/ops/preview",
                       json={**body, "relative_path": "../x"}).status_code == 403
    assert client.post("/api/computer/files/ops/preview",
                       json={**body, "op": "shred"}).status_code == 422
    assert client.get("/api/computer/files/metadata",
                      params={"root_id": "root_1", "relative_path": "zzz"}).status_code == 404
    assert client.get("/api/computer/files/duplicates",
                      params={"root_id": "root_1"}).status_code == 200
