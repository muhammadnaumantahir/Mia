"""MIA-LIFE-11: encrypted backup, integrity check, restore preview and recovery.

Real SQLite databases and real encryption (no mocked crypto). Adversarial cases forge
validly-encrypted but hostile payloads through the module's own primitives, so the checks
after decryption are tested too, not just authentication.
"""

import hashlib
import io
import json
import os
import secrets
import sqlite3
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from mia import backup_cli  # noqa: E402
from mia.life import backup as bk  # noqa: E402

PASS = "correct horse battery staple"
MARKER = "TOP-SECRET-MARKER-7f3a9c"
FAST_KDF = (14, 8, 1)  # smallest accepted cost, keeps the suite quick
NOW = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)


def make_db(path: Path, rows=3, *, wal=False, blob=0):
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT, b BLOB)")
    for i in range(rows):
        conn.execute("INSERT INTO t (v, b) VALUES (?, ?)", (f"{MARKER}-{i}", secrets.token_bytes(blob)))
    conn.commit()
    return conn


def values(path: Path):
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT v FROM t ORDER BY id")]
    finally:
        conn.close()


@pytest.fixture
def data(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    for name in ("mia-memory.sqlite3", "mia-life.sqlite3", "mia-usage.sqlite3", "computer-tasks.sqlite3"):
        make_db(folder / name).close()
    return folder


def backup(data, tmp_path, name="b.miabak", **kw):
    return bk.create_backup(data, tmp_path / name, PASS, now=NOW, kdf=FAST_KDF, **kw)


def tree(folder: Path):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir()) if p.is_file()}


@pytest.fixture(autouse=True)
def private_tmp(tmp_path, monkeypatch):
    """Route temp dirs under tmp_path so leaks of plaintext staging dirs are detectable."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(scratch))
    yield scratch
    assert [p.name for p in scratch.iterdir()] == [], "temporary plaintext staging was left behind"


# ---- round trip, scope, secrecy -----------------------------------------------------------

def test_round_trip_restores_identical_data_into_a_new_folder(data, tmp_path):
    result = backup(data, tmp_path)
    assert result.archive_bytes == result.path.stat().st_size and len(result.archive_sha256) == 64
    target = tmp_path / "restored"
    restored = bk.restore_backup(result.path, PASS, target)
    assert sorted(restored.restored) == sorted(p.name for p in data.iterdir())
    for name in restored.restored:
        assert values(target / name) == values(data / name)
        assert sqlite3.connect(target / name).execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert not [p for p in target.iterdir() if p.name.startswith(".restore-")]


def test_wal_mode_uncheckpointed_commits_are_captured(tmp_path):
    folder = tmp_path / "live"
    folder.mkdir()
    conn = make_db(folder / "mia-memory.sqlite3", rows=5, wal=True)  # connection stays open
    assert (folder / "mia-memory.sqlite3-wal").exists()
    result = bk.create_backup(folder, tmp_path / "w.miabak", PASS, kdf=FAST_KDF)
    bk.restore_backup(result.path, PASS, tmp_path / "out")
    assert len(values(tmp_path / "out" / "mia-memory.sqlite3")) == 5
    conn.close()


def test_scope_excludes_grants_symlinks_and_unrelated_files(data, tmp_path):
    make_db(data / "computer-permissions.sqlite3").close()
    make_db(data / "computer-approvals.sqlite3").close()
    (data / "notes.txt").write_text("not a database")
    outside = tmp_path / "elsewhere.sqlite3"
    make_db(outside).close()
    try:
        (data / "mia-link.sqlite3").symlink_to(outside)
    except OSError:
        pass
    result = backup(data, tmp_path)
    names = {e["name"] for e in result.files}
    assert names == {"mia-memory.sqlite3", "mia-life.sqlite3", "mia-usage.sqlite3", "computer-tasks.sqlite3"}
    assert result.excluded_by_policy == ["computer-approvals.sqlite3", "computer-permissions.sqlite3"]
    summary = bk.verify_backup(result.path, PASS)
    assert {e["name"] for e in summary.files} == names and set(summary.integrity.values()) == {"ok"}


def test_archive_is_encrypted_randomised_and_private(data, tmp_path):
    a, b = backup(data, tmp_path, "a.miabak"), backup(data, tmp_path, "b2.miabak")
    raw = a.path.read_bytes()
    assert raw.startswith(bk.MAGIC)
    for needle in (MARKER.encode(), PASS.encode(), b"SQLite format", b"manifest", b"mia-memory"):
        assert needle not in raw
    assert raw != b.path.read_bytes()  # fresh salt and nonce every time
    if os.name == "posix":
        assert a.path.stat().st_mode & 0o777 == 0o600


def test_passphrase_rules_and_errors_never_leak_it(data, tmp_path):
    with pytest.raises(bk.BackupError, match="at least"):
        bk.create_backup(data, tmp_path / "x.miabak", "short", kdf=FAST_KDF)
    assert not (tmp_path / "x.miabak").exists()
    path = backup(data, tmp_path).path
    wrong = "a completely wrong passphrase"
    with pytest.raises(bk.BackupAuthenticationError) as info:
        bk.verify_backup(path, wrong)
    assert wrong not in str(info.value) and PASS not in str(info.value)


# ---- tampering fails closed ---------------------------------------------------------------

@pytest.fixture
def big_archive(tmp_path):
    folder = tmp_path / "bigdata"
    folder.mkdir()
    make_db(folder / "mia-memory.sqlite3", rows=40, blob=20_000).close()  # incompressible, several chunks
    result = bk.create_backup(folder, tmp_path / "big.miabak", PASS, kdf=FAST_KDF)
    assert result.archive_bytes > 3 * bk.CHUNK_SIZE
    return result.path


def _expect_rejected(path, tmp_path):
    target = tmp_path / "never"
    with pytest.raises(bk.BackupError):
        bk.restore_backup(path, PASS, target)
    assert not target.exists()


@pytest.mark.parametrize("where", ["salt", "nonce_prefix", "first_chunk", "middle", "last_byte"])
def test_any_flipped_byte_is_rejected(big_archive, tmp_path, where):
    raw = bytearray(big_archive.read_bytes())
    offset = {"salt": len(bk.MAGIC) + 3 + 2, "nonce_prefix": len(bk.MAGIC) + 3 + bk.SALT_SIZE + 1,
              "first_chunk": bk.HEADER_SIZE + 5, "middle": len(raw) // 2, "last_byte": len(raw) - 1}[where]
    raw[offset] ^= 0x01
    big_archive.write_bytes(bytes(raw))
    _expect_rejected(big_archive, tmp_path)


def test_truncation_appending_and_chunk_reordering_are_rejected(big_archive, tmp_path):
    raw = big_archive.read_bytes()
    size = bk.CHUNK_SIZE + bk.TAG_SIZE
    cases = {
        "cut_at_chunk_boundary": raw[: bk.HEADER_SIZE + 2 * size],
        "cut_midway": raw[: len(raw) - 7],
        "header_only": raw[: bk.HEADER_SIZE],
        "appended": raw + b"extra",
        "appended_chunk_sized": raw + secrets.token_bytes(size),
        "swapped_chunks": raw[: bk.HEADER_SIZE] + raw[bk.HEADER_SIZE + size: bk.HEADER_SIZE + 2 * size]
        + raw[bk.HEADER_SIZE: bk.HEADER_SIZE + size] + raw[bk.HEADER_SIZE + 2 * size:],
        "dropped_first_chunk": raw[: bk.HEADER_SIZE] + raw[bk.HEADER_SIZE + size:],
    }
    for name, mutated in cases.items():
        broken = tmp_path / f"{name}.miabak"
        broken.write_bytes(mutated)
        with pytest.raises(bk.BackupError):
            bk.verify_backup(broken, PASS)


def test_hostile_or_foreign_files_are_rejected_cheaply(tmp_path, data):
    good = backup(data, tmp_path).path.read_bytes()
    log2_n_at = len(bk.MAGIC)
    huge = bytearray(good)
    huge[log2_n_at] = 30  # N = 2**30 would need ~128 GiB: must be refused before any KDF work
    cases = {"empty": b"", "text": b"hello world, not a backup", "wrong_magic": b"X" + good[1:],
             "huge_kdf": bytes(huge), "bad_chunk_size": good[: bk.HEADER_SIZE - 4] + (7).to_bytes(4, "big") + good[bk.HEADER_SIZE:]}
    for name, blob in cases.items():
        path = tmp_path / f"{name}.bin"
        path.write_bytes(blob)
        with pytest.raises(bk.BackupFormatError):
            bk.verify_backup(path, PASS)
    with pytest.raises(bk.BackupError):
        bk.verify_backup(tmp_path / "missing.miabak", PASS)


# ---- hostile but correctly encrypted payloads ---------------------------------------------

def forge(tmp_path, members, manifest, name="forged.miabak"):
    """Encrypt an arbitrary tar with the real primitives: authentication passes, content is hostile."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        def add(tar_name, payload, kind=tarfile.REGTYPE, link=""):
            info = tarfile.TarInfo(tar_name)
            info.type, info.linkname, info.size = kind, link, len(payload) if kind == tarfile.REGTYPE else 0
            tar.addfile(info, io.BytesIO(payload) if kind == tarfile.REGTYPE else None)
        if manifest is not None:
            add("manifest.json", manifest if isinstance(manifest, bytes) else json.dumps(manifest).encode())
        for member in members:
            add(*member)
    salt, prefix = secrets.token_bytes(bk.SALT_SIZE), secrets.token_bytes(bk.NONCE_PREFIX_SIZE)
    header = bk._pack_header(*FAST_KDF, salt, prefix)
    key = bk._derive_key(PASS.encode(), salt, *FAST_KDF)
    out = io.BytesIO()
    out.write(header)
    bk._encrypt_stream(io.BytesIO(buffer.getvalue()), out, key, header, prefix)
    path = tmp_path / name
    path.write_bytes(out.getvalue())
    return path


def db_bytes(tmp_path, name="seed.sqlite3"):
    path = tmp_path / name
    make_db(path).close()
    return path.read_bytes()


def manifest_for(files, **extra):
    entries = [{"name": n, "size": len(b), "sha256": hashlib.sha256(b).hexdigest()} for n, b in files]
    return {"format": 1, "app": "mia", "created_at": NOW.isoformat(), "files": entries, "excluded_by_policy": [], **extra}


def test_a_well_formed_forged_archive_is_accepted_as_a_control(tmp_path):
    seed = db_bytes(tmp_path)
    path = forge(tmp_path, [("mia-memory.sqlite3", seed)], manifest_for([("mia-memory.sqlite3", seed)]))
    assert bk.verify_backup(path, PASS).integrity == {"mia-memory.sqlite3": "ok"}


@pytest.mark.parametrize("case", [
    "path_traversal_name", "absolute_name", "unlisted_member", "duplicate_member", "symlink_member",
    "missing_file", "wrong_size", "wrong_checksum", "excluded_permissions_store", "no_manifest",
    "bad_manifest_json", "wrong_format_version", "not_a_database", "corrupt_database",
    "bad_checksum_shape", "negative_size", "too_many_files", "oversized_claim",
])
def test_hostile_payloads_are_rejected_and_write_nothing(tmp_path, case):
    seed = db_bytes(tmp_path)
    good = ("mia-memory.sqlite3", seed)
    m = manifest_for([good])
    members, manifest = [good], m
    if case == "path_traversal_name":
        members = [("../evil.sqlite3", seed)]
        manifest = manifest_for([("../evil.sqlite3", seed)])
    elif case == "absolute_name":
        members, manifest = [("/tmp/evil.sqlite3", seed)], manifest_for([("/tmp/evil.sqlite3", seed)])
    elif case == "unlisted_member":
        members = [good, ("mia-extra.sqlite3", seed)]
    elif case == "duplicate_member":
        members = [good, good]
    elif case == "symlink_member":
        members = [("mia-memory.sqlite3", b"", tarfile.SYMTYPE, "/etc/passwd")]
    elif case == "missing_file":
        members = []
    elif case == "wrong_size":
        manifest = dict(m, files=[dict(m["files"][0], size=len(seed) + 1)])
    elif case == "wrong_checksum":
        manifest = dict(m, files=[dict(m["files"][0], sha256="0" * 64)])
    elif case == "excluded_permissions_store":
        members, manifest = [("computer-permissions.sqlite3", seed)], manifest_for([("computer-permissions.sqlite3", seed)])
    elif case == "no_manifest":
        manifest = None
    elif case == "bad_manifest_json":
        manifest = b"{not json"
    elif case == "wrong_format_version":
        manifest = dict(m, format=99)
    elif case == "not_a_database":
        members, manifest = [("mia-memory.sqlite3", b"plain text")], manifest_for([("mia-memory.sqlite3", b"plain text")])
    elif case == "corrupt_database":
        damaged = bytearray(seed)
        damaged[4096 + 20: 4096 + 40] = b"\xff" * 20 if len(damaged) > 4200 else b""
        damaged = bytes(damaged[:100]) + secrets.token_bytes(len(damaged) - 100)
        members, manifest = [("mia-memory.sqlite3", damaged)], manifest_for([("mia-memory.sqlite3", damaged)])
    elif case == "bad_checksum_shape":
        manifest = dict(m, files=[dict(m["files"][0], sha256="XYZ")])
    elif case == "negative_size":
        manifest = dict(m, files=[dict(m["files"][0], size=-5)])
    elif case == "too_many_files":
        manifest = dict(m, files=[dict(m["files"][0], name=f"mia-f{i}.sqlite3") for i in range(bk.MAX_FILES + 1)])
        members = []
    elif case == "oversized_claim":
        manifest = dict(m, files=[dict(m["files"][0], size=bk.MAX_TOTAL_BYTES + 1)])
    path = forge(tmp_path, members, manifest)
    target = tmp_path / "target"
    with pytest.raises(bk.BackupFormatError):
        bk.restore_backup(path, PASS, target)
    assert not target.exists()


# ---- create failures ----------------------------------------------------------------------

def test_corrupt_source_database_aborts_without_writing_a_backup(data, tmp_path):
    (data / "mia-brain.sqlite3").write_bytes(b"this is not a sqlite database" * 100)
    out = tmp_path / "bad.miabak"
    with pytest.raises(bk.BackupError, match="mia-brain.sqlite3"):
        bk.create_backup(data, out, PASS, kdf=FAST_KDF)
    assert not out.exists() and not out.with_name("bad.miabak.partial").exists()


def test_create_refuses_existing_destination_and_empty_folders(data, tmp_path):
    existing = tmp_path / "taken.miabak"
    existing.write_bytes(b"precious")
    with pytest.raises(bk.BackupError, match="already exists"):
        bk.create_backup(data, existing, PASS, kdf=FAST_KDF)
    assert existing.read_bytes() == b"precious"
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(bk.BackupError, match="no Mia data"):
        bk.create_backup(empty, tmp_path / "e.miabak", PASS, kdf=FAST_KDF)
    with pytest.raises(bk.BackupError, match="does not exist"):
        bk.create_backup(tmp_path / "nope", tmp_path / "n.miabak", PASS, kdf=FAST_KDF)


def test_failure_while_writing_leaves_no_partial_or_final_file(data, tmp_path, monkeypatch):
    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(bk, "_encrypt_stream", explode)
    out = tmp_path / "x.miabak"
    with pytest.raises(OSError):
        bk.create_backup(data, out, PASS, kdf=FAST_KDF)
    assert not out.exists() and not (tmp_path / "x.miabak.partial").exists()


def test_stale_partial_from_an_interrupted_run_does_not_block_a_new_backup(data, tmp_path):
    (tmp_path / "again.miabak.partial").write_bytes(b"junk")
    assert backup(data, tmp_path, "again.miabak").path.exists()


def test_missing_cryptography_gives_a_clear_error(data, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives.ciphers.aead", None)
    with pytest.raises(bk.BackupUnavailableError, match="backup"):
        backup(data, tmp_path)


# ---- preview and restore policies ---------------------------------------------------------

def test_preview_reports_states_and_writes_nothing(data, tmp_path):
    path = backup(data, tmp_path).path
    target = tmp_path / "existing"
    target.mkdir()
    bk.restore_backup(path, PASS, target)  # now identical
    make_db(target / "mia-memory.sqlite3", rows=9).close()  # diverges
    (target / "mia-life.sqlite3").unlink()  # missing -> would be created
    before = tree(target)
    preview = bk.preview_restore(path, PASS, target)
    by_name = {i["name"]: i for i in preview.items}
    assert preview.verified and by_name["mia-life.sqlite3"]["state"] == "new"
    assert by_name["mia-memory.sqlite3"]["state"] == "differs" and by_name["mia-memory.sqlite3"]["action"] == "blocked"
    assert by_name["computer-tasks.sqlite3"]["state"] == "identical"
    assert preview.would_succeed is False
    assert tree(target) == before  # nothing written
    absent = tmp_path / "absent"
    bk.preview_restore(path, PASS, absent)
    assert not absent.exists()
    assert bk.preview_restore(path, PASS, absent, policy="abort").would_succeed is True


def test_abort_policy_stops_on_conflict_and_changes_nothing(data, tmp_path):
    path = backup(data, tmp_path).path
    target = tmp_path / "t"
    target.mkdir()
    make_db(target / "mia-memory.sqlite3", rows=9).close()
    before = tree(target)
    with pytest.raises(bk.RestoreConflictError) as info:
        bk.restore_backup(path, PASS, target)
    assert info.value.names == ["mia-memory.sqlite3"]
    assert tree(target) == before


def test_overwrite_keeps_an_undo_copy_and_clears_stale_sidecars(data, tmp_path):
    path = backup(data, tmp_path).path
    target = tmp_path / "t"
    target.mkdir()
    make_db(target / "mia-memory.sqlite3", rows=9).close()
    original = (target / "mia-memory.sqlite3").read_bytes()
    (target / "mia-memory.sqlite3-wal").write_bytes(b"stale wal that would corrupt the restored file")
    (target / "mia-memory.sqlite3-shm").write_bytes(b"stale shm")
    result = bk.restore_backup(path, PASS, target, policy="overwrite", now=NOW)
    assert values(target / "mia-memory.sqlite3") == values(data / "mia-memory.sqlite3")
    assert not (target / "mia-memory.sqlite3-wal").exists() and not (target / "mia-memory.sqlite3-shm").exists()
    undo = Path(result.undo_dir)
    assert (undo / "mia-memory.sqlite3").read_bytes() == original
    assert (undo / "mia-memory.sqlite3-wal").exists()


def test_side_by_side_never_touches_existing_files(data, tmp_path):
    path = backup(data, tmp_path).path
    target = tmp_path / "t"
    target.mkdir()
    make_db(target / "mia-memory.sqlite3", rows=9).close()
    before = (target / "mia-memory.sqlite3").read_bytes()
    result = bk.restore_backup(path, PASS, target, policy="side_by_side", now=NOW)
    copy = target / result.side_by_side["mia-memory.sqlite3"]
    assert (target / "mia-memory.sqlite3").read_bytes() == before
    assert values(copy) == values(data / "mia-memory.sqlite3")
    assert not bk.INCLUDE_PATTERN.match(copy.name)  # a copy is never mistaken for a live store


def test_spend_ledger_is_never_overwritten_even_with_overwrite_policy(data, tmp_path):
    path = backup(data, tmp_path).path
    target = tmp_path / "t"
    target.mkdir()
    ledger = target / "mia-usage.sqlite3"
    make_db(ledger, rows=50).close()  # "newer" ledger with more recorded spend
    before = ledger.read_bytes()
    result = bk.restore_backup(path, PASS, target, policy="overwrite", now=NOW)
    assert ledger.read_bytes() == before
    assert "mia-usage.sqlite3" in result.side_by_side and "mia-usage.sqlite3" not in result.restored
    assert any("spend ledger" in w for w in result.warnings)
    preview = bk.preview_restore(path, PASS, target, policy="overwrite")
    assert [i["action"] for i in preview.items if i["name"] == "mia-usage.sqlite3"] == ["restore_as_copy"]


def test_restore_is_idempotent_and_skips_identical_files(data, tmp_path):
    path = backup(data, tmp_path).path
    target = tmp_path / "t"
    first = bk.restore_backup(path, PASS, target)
    second = bk.restore_backup(path, PASS, target)
    assert second.restored == [] and sorted(second.skipped_identical) == sorted(first.restored)


def test_failure_mid_restore_rolls_everything_back(data, tmp_path, monkeypatch):
    path = backup(data, tmp_path).path
    target = tmp_path / "t"
    target.mkdir()
    for name in ("computer-tasks.sqlite3", "mia-life.sqlite3", "mia-memory.sqlite3"):
        make_db(target / name, rows=7).close()
    (target / "mia-life.sqlite3-wal").write_bytes(b"wal")
    before = tree(target)
    real_copy, calls = bk.shutil.copyfile, {"n": 0}

    def flaky(src, dst, *a, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk full")
        return real_copy(src, dst, *a, **k)

    monkeypatch.setattr(bk.shutil, "copyfile", flaky)
    with pytest.raises(bk.RestoreError):
        bk.restore_backup(path, PASS, target, policy="overwrite", now=NOW)
    assert tree(target) == before  # originals (and the sidecar) are back, nothing extra remains
    assert not any(p.name.startswith("restore-undo") for p in target.iterdir())


def test_unknown_policy_and_non_regular_targets_are_refused(data, tmp_path):
    path = backup(data, tmp_path).path
    with pytest.raises(bk.BackupError, match="policy"):
        bk.restore_backup(path, PASS, tmp_path / "t", policy="yolo")
    target = tmp_path / "t2"
    (target / "mia-memory.sqlite3").mkdir(parents=True)
    with pytest.raises(bk.BackupError, match="not a regular file"):
        bk.restore_backup(path, PASS, target)
    assert [p.name for p in target.iterdir()] == ["mia-memory.sqlite3"]


def test_restore_into_a_symlinked_target_file_is_refused(data, tmp_path):
    path = backup(data, tmp_path).path
    outside = tmp_path / "outside.sqlite3"
    make_db(outside).close()
    target = tmp_path / "t3"
    target.mkdir()
    try:
        (target / "mia-memory.sqlite3").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    before = outside.read_bytes()
    with pytest.raises(bk.BackupError):
        bk.restore_backup(path, PASS, target, policy="overwrite")
    assert outside.read_bytes() == before


# ---- CLI ----------------------------------------------------------------------------------

def test_cli_round_trip_uses_env_passphrase_and_never_argv(data, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MIA_BACKUP_PASSPHRASE", PASS)
    out = tmp_path / "cli.miabak"
    assert backup_cli.main(["create", "--data-dir", str(data), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "not in this backup" in printed and PASS not in printed
    assert backup_cli.main(["verify", str(out)]) == 0
    assert backup_cli.main(["preview", str(out), "--to", str(tmp_path / "new")]) == 0
    assert backup_cli.main(["restore", str(out), "--to", str(tmp_path / "new")]) == 0
    assert values(tmp_path / "new" / "mia-memory.sqlite3") == values(data / "mia-memory.sqlite3")
    with pytest.raises(SystemExit):
        backup_cli.main(["verify", str(out), "--passphrase", PASS])  # no such option


def test_cli_wrong_passphrase_and_missing_passphrase_fail_cleanly(data, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MIA_BACKUP_PASSPHRASE", PASS)
    out = tmp_path / "cli.miabak"
    backup_cli.main(["create", "--data-dir", str(data), "--out", str(out)])
    capsys.readouterr()
    monkeypatch.setenv("MIA_BACKUP_PASSPHRASE", "not the right passphrase")
    assert backup_cli.main(["verify", str(out)]) == 1
    err = capsys.readouterr().err
    assert "not the right passphrase" not in err and "Traceback" not in err
    monkeypatch.delenv("MIA_BACKUP_PASSPHRASE")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert backup_cli.main(["verify", str(out)]) == 1
    assert "MIA_BACKUP_PASSPHRASE" in capsys.readouterr().err


def test_cli_preview_exit_code_signals_a_conflict(data, tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_BACKUP_PASSPHRASE", PASS)
    out = tmp_path / "cli.miabak"
    backup_cli.main(["create", "--data-dir", str(data), "--out", str(out)])
    target = tmp_path / "t"
    target.mkdir()
    make_db(target / "mia-memory.sqlite3", rows=9).close()
    assert backup_cli.main(["preview", str(out), "--to", str(target)]) == 2


# ---- the encryption layer on its own (later layers must not be what saves us) --------------

def _seal(plain: bytes):
    salt, prefix = secrets.token_bytes(bk.SALT_SIZE), secrets.token_bytes(bk.NONCE_PREFIX_SIZE)
    header = bk._pack_header(*FAST_KDF, salt, prefix)
    key = bk._derive_key(PASS.encode(), salt, *FAST_KDF)
    sealed = io.BytesIO()
    bk._encrypt_stream(io.BytesIO(plain), sealed, key, header, prefix)
    return sealed.getvalue(), key, header, prefix


def _open(blob: bytes, key, header, prefix) -> bytes:
    out = io.BytesIO()
    bk._decrypt_stream(io.BytesIO(blob), out, key, header, prefix)
    return out.getvalue()


@pytest.mark.parametrize("size", [0, 1, bk.CHUNK_SIZE - 1, bk.CHUNK_SIZE, bk.CHUNK_SIZE + 1, 3 * bk.CHUNK_SIZE + 17])
def test_crypto_layer_round_trips_every_boundary_size(size):
    plain = secrets.token_bytes(size)
    blob, key, header, prefix = _seal(plain)
    assert _open(blob, key, header, prefix) == plain


def test_crypto_layer_itself_rejects_truncation_extension_and_reordering():
    plain = secrets.token_bytes(3 * bk.CHUNK_SIZE + 100)
    blob, key, header, prefix = _seal(plain)
    size = bk.CHUNK_SIZE + bk.TAG_SIZE
    mutated = {
        "cut_at_chunk_boundary": blob[:size],
        "cut_two_chunks": blob[: 2 * size],
        "cut_inside_last_chunk": blob[:-1],
        "appended_chunk": blob + secrets.token_bytes(size),
        "appended_byte": blob + b"x",
        "swapped": blob[size: 2 * size] + blob[:size] + blob[2 * size:],
        "dropped_first": blob[size:],
        "duplicated_first": blob[:size] + blob,
    }
    for name, candidate in mutated.items():
        with pytest.raises(bk.BackupAuthenticationError):
            _open(candidate, key, header, prefix)
    with pytest.raises(bk.BackupAuthenticationError):  # same key, different header: bound to it
        _open(blob, key, bk._pack_header(*FAST_KDF, secrets.token_bytes(16), prefix), prefix)
