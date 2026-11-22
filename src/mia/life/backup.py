"""Encrypted backup and restore of Mia's local data (MIA-LIFE-11).

Design (see docs/architecture/BACKUP_RESTORE.md):

* **Scope.** Top-level ``mia-*.sqlite3`` stores and ``computer-tasks.sqlite3`` in the data
  directory, each captured with SQLite's online backup API (a consistent snapshot even while
  Mia is running) and checked with ``PRAGMA integrity_check``. Provider API keys live in the OS
  credential store, not the data directory, so they are never in a backup. The computer
  permission and approval stores are excluded on purpose: restoring an old copy could silently
  re-grant permissions the owner has since revoked.
* **Key management.** The key is derived from an owner passphrase with scrypt. The passphrase
  and the key are never stored, logged or put in error text; the archive holds only the salt and
  KDF parameters. Losing the passphrase means the backup cannot be opened.
* **Format.** ``header | AES-256-GCM chunks`` where each chunk's authenticated data includes the
  header, its index and a final-chunk flag, so truncation, reordering, splicing and appended data
  all fail authentication. The plaintext is a gzip tar holding ``manifest.json`` (names, sizes,
  SHA-256) and the database snapshots.
* **Fail closed.** Verification and preview decrypt and check everything into a private temporary
  directory and write nothing else. Restore verifies the whole archive before touching the target,
  then places files atomically with a rollback if anything fails part-way.
* **Rollback-sensitive data.** ``mia-usage.sqlite3`` (the spend ledger) is never overwritten in
  place: an older copy would lower recorded spend. It is always restored side by side.

Restoring over a data directory that a running Mia has open is not safe; stop Mia first or
restore to a new directory.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

MAGIC = b"MIABAK01"
FORMAT_VERSION = 1
CHUNK_SIZE = 64 * 1024
TAG_SIZE = 16
SALT_SIZE = 16
NONCE_PREFIX_SIZE = 8
HEADER_SIZE = len(MAGIC) + 3 + SALT_SIZE + NONCE_PREFIX_SIZE + 4  # magic, n/r/p, salt, prefix, chunk
DEFAULT_KDF = (15, 8, 1)  # scrypt N=2**15, r=8, p=1 (about 32 MiB, ~0.1 s)
MAX_KDF_MEMORY = 512 * 1024 * 1024
MIN_PASSPHRASE_CHARS = 12
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024**3
MAX_FILES = 64

INCLUDE_PATTERN = re.compile(r"^(?:mia-[a-z0-9-]+|computer-tasks)\.sqlite3$")
EXCLUDED_BY_POLICY = frozenset({"computer-approvals.sqlite3", "computer-permissions.sqlite3"})
NEVER_OVERWRITE = frozenset({"mia-usage.sqlite3"})
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
POLICIES = ("abort", "overwrite", "side_by_side")


class BackupError(Exception):
    """Base class. Messages are safe to show: they never contain secrets or file contents."""


class BackupUnavailableError(BackupError):
    """The optional ``cryptography`` package is not installed."""


class BackupAuthenticationError(BackupError):
    """Wrong passphrase, or the archive was modified, truncated or damaged."""


class BackupFormatError(BackupError):
    """The file is not a valid Mia backup (or is an unsupported or hostile one)."""


class RestoreConflictError(BackupError):
    """The restore would replace existing data and the policy forbids it."""

    def __init__(self, names: list[str]) -> None:
        super().__init__(
            "Restore stopped: these files already exist with different content: "
            + ", ".join(names)
            + ". Nothing was changed. Use a new folder, or choose the overwrite or "
            "side_by_side policy."
        )
        self.names = names


class RestoreError(BackupError):
    """Placing the restored files failed; the target was rolled back."""


@dataclass(frozen=True)
class BackupResult:
    path: Path
    archive_bytes: int
    archive_sha256: str
    created_at: str
    files: list[dict[str, Any]]
    excluded_by_policy: list[str]


@dataclass(frozen=True)
class BackupSummary:
    created_at: str
    files: list[dict[str, Any]]
    excluded_by_policy: list[str]
    integrity: dict[str, str]


@dataclass
class RestorePreview:
    policy: str
    verified: bool
    created_at: str
    items: list[dict[str, Any]]
    would_succeed: bool
    warnings: list[str] = field(default_factory=list)


@dataclass
class RestoreResult:
    policy: str
    restored: list[str]
    skipped_identical: list[str]
    side_by_side: dict[str, str]
    undo_dir: str | None
    warnings: list[str]


# ---- crypto ------------------------------------------------------------------------------

def _aesgcm(key: bytes):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - exercised by a test that hides the module
        raise BackupUnavailableError(
            'Install Mia with the "backup" extra (pip install "mia[backup]") to use backups.'
        ) from exc
    return AESGCM(key)


def _check_passphrase(passphrase: str) -> bytes:
    if not isinstance(passphrase, str) or len(passphrase) < MIN_PASSPHRASE_CHARS:
        raise BackupError(f"The passphrase must be at least {MIN_PASSPHRASE_CHARS} characters.")
    return passphrase.encode("utf-8")


def _derive_key(passphrase: bytes, salt: bytes, log2_n: int, r: int, p: int) -> bytes:
    n = 1 << log2_n
    return hashlib.scrypt(
        passphrase, salt=salt, n=n, r=r, p=p, dklen=32, maxmem=MAX_KDF_MEMORY + 64 * 1024 * 1024
    )


def _pack_header(log2_n: int, r: int, p: int, salt: bytes, prefix: bytes) -> bytes:
    return (MAGIC + bytes([log2_n, r, p]) + salt + prefix + CHUNK_SIZE.to_bytes(4, "big"))


def _parse_header(raw: bytes) -> tuple[int, int, int, bytes, bytes, int]:
    if len(raw) != HEADER_SIZE or raw[: len(MAGIC)] != MAGIC:
        raise BackupFormatError("This file is not a Mia backup.")
    offset = len(MAGIC)
    log2_n, r, p = raw[offset], raw[offset + 1], raw[offset + 2]
    offset += 3
    salt = raw[offset: offset + SALT_SIZE]
    offset += SALT_SIZE
    prefix = raw[offset: offset + NONCE_PREFIX_SIZE]
    chunk = int.from_bytes(raw[offset + NONCE_PREFIX_SIZE:], "big")
    # Bound everything read from an untrusted header before it drives any work or memory.
    if not (14 <= log2_n <= 20 and 1 <= r <= 16 and 1 <= p <= 4):
        raise BackupFormatError("This backup uses unsupported key-derivation settings.")
    if 128 * (1 << log2_n) * r > MAX_KDF_MEMORY:
        raise BackupFormatError("This backup uses unsupported key-derivation settings.")
    if chunk != CHUNK_SIZE:
        raise BackupFormatError("This backup uses an unsupported chunk size.")
    return log2_n, r, p, salt, prefix, chunk


def _nonce(prefix: bytes, index: int) -> bytes:
    if index >= 1 << 32:
        raise BackupFormatError("The backup is too large.")
    return prefix + index.to_bytes(4, "big")


def _aad(header: bytes, index: int, final: bool) -> bytes:
    return header + index.to_bytes(8, "big") + (b"\x01" if final else b"\x00")


def _encrypt_stream(src: BinaryIO, dst: BinaryIO, key: bytes, header: bytes, prefix: bytes) -> int:
    aes = _aesgcm(key)
    written = 0
    index = 0
    chunk = src.read(CHUNK_SIZE)
    while True:
        nxt = src.read(CHUNK_SIZE) if len(chunk) == CHUNK_SIZE else b""
        final = not nxt
        blob = aes.encrypt(_nonce(prefix, index), chunk, _aad(header, index, final))
        dst.write(blob)
        written += len(blob)
        if final:
            return written
        chunk, index = nxt, index + 1


def _decrypt_stream(src: BinaryIO, dst: BinaryIO, key: bytes, header: bytes, prefix: bytes) -> int:
    aes = _aesgcm(key)
    from cryptography.exceptions import InvalidTag

    total = 0
    index = 0
    size = CHUNK_SIZE + TAG_SIZE
    blob = src.read(size)
    while True:
        nxt = src.read(size) if len(blob) == size else b""
        final = not nxt
        if len(blob) < TAG_SIZE:
            raise BackupAuthenticationError(_AUTH_MESSAGE)
        try:
            plain = aes.decrypt(_nonce(prefix, index), blob, _aad(header, index, final))
        except InvalidTag:
            raise BackupAuthenticationError(_AUTH_MESSAGE) from None
        total += len(plain)
        if total > MAX_TOTAL_BYTES + MAX_MANIFEST_BYTES + (1 << 20):
            raise BackupFormatError("The backup is larger than Mia supports.")
        dst.write(plain)
        if final:
            return total
        blob, index = nxt, index + 1


_AUTH_MESSAGE = (
    "The backup could not be opened: the passphrase is wrong, or the file was changed, "
    "cut short or damaged."
)


# ---- helpers -----------------------------------------------------------------------------

def _utc(now: datetime | None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(UTC)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def discover(data_dir: Path) -> tuple[list[Path], list[str]]:
    """Return (databases to back up, names deliberately excluded). Symlinks are ignored."""
    if not data_dir.is_dir():
        raise BackupError("The Mia data folder does not exist.")
    included: list[Path] = []
    excluded: list[str] = []
    for entry in sorted(data_dir.iterdir(), key=lambda item: item.name):
        if entry.is_symlink() or not entry.is_file():
            continue
        if entry.name in EXCLUDED_BY_POLICY:
            excluded.append(entry.name)
        elif INCLUDE_PATTERN.match(entry.name):
            included.append(entry)
    return included, excluded


def _integrity(path: Path) -> str:
    try:
        # Always a private temp copy: immutable=1 reads it without creating WAL/SHM files.
        connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        try:
            rows = connection.execute("PRAGMA integrity_check").fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return "unreadable"
    return "ok" if rows == [("ok",)] else "failed"


def _snapshot(source: Path, target: Path) -> None:
    try:
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except sqlite3.Error:
        raise BackupError(f"{source.name} could not be read; no backup was written.") from None


@contextmanager
def _private_dir() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="mia-backup-"))  # mkdtemp creates it owner-only (0700)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


# ---- create ------------------------------------------------------------------------------

def create_backup(
    data_dir: Path | str,
    destination: Path | str,
    passphrase: str,
    *,
    now: datetime | None = None,
    kdf: tuple[int, int, int] = DEFAULT_KDF,
) -> BackupResult:
    """Write an encrypted backup of the data directory to ``destination`` (must not exist)."""
    key_material = _check_passphrase(passphrase)
    data_dir, destination = Path(data_dir), Path(destination)
    if destination.exists() or destination.is_symlink():
        raise BackupError("The backup file already exists; choose a new name.")
    databases, excluded = discover(data_dir)
    if not databases:
        raise BackupError("There is no Mia data to back up in that folder.")
    if len(databases) > MAX_FILES:
        raise BackupError("There are more data files than Mia supports backing up.")
    created_at = _utc(now).isoformat()

    log2_n, r, p = kdf
    salt, prefix = secrets.token_bytes(SALT_SIZE), secrets.token_bytes(NONCE_PREFIX_SIZE)
    header = _pack_header(log2_n, r, p, salt, prefix)
    key = _derive_key(key_material, salt, log2_n, r, p)

    with _private_dir() as work:
        entries: list[dict[str, Any]] = []
        for database in databases:
            snapshot = work / database.name
            _snapshot(database, snapshot)
            state = _integrity(snapshot)
            if state != "ok":
                raise BackupError(
                    f"{database.name} failed its integrity check ({state}); no backup was written."
                )
            entries.append({"name": database.name, "size": snapshot.stat().st_size,
                            "sha256": _sha256_file(snapshot)})
        manifest = {"format": FORMAT_VERSION, "app": "mia", "created_at": created_at,
                    "files": entries, "excluded_by_policy": excluded}
        manifest_bytes = json.dumps(manifest, sort_keys=True, indent=1).encode("utf-8")

        payload = work / "payload.tar.gz"
        with tarfile.open(payload, "w:gz") as archive:
            info = tarfile.TarInfo("manifest.json")
            info.size, info.mode = len(manifest_bytes), 0o600
            archive.addfile(info, io.BytesIO(manifest_bytes))
            for entry in entries:
                info = tarfile.TarInfo(entry["name"])
                info.size, info.mode = entry["size"], 0o600
                with (work / entry["name"]).open("rb") as handle:
                    archive.addfile(info, handle)

        partial = destination.with_name(destination.name + ".partial")
        partial.unlink(missing_ok=True)  # a leftover from an interrupted earlier run
        digest = hashlib.sha256()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(partial, flags, 0o600)
            with os.fdopen(descriptor, "wb") as out, payload.open("rb") as src:
                out.write(header)
                digest.update(header)

                class _Hashing:
                    def write(self, data: bytes) -> None:
                        digest.update(data)
                        out.write(data)

                _encrypt_stream(src, _Hashing(), key, header, prefix)  # type: ignore[arg-type]
                out.flush()
                os.fsync(out.fileno())
            os.replace(partial, destination)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    return BackupResult(destination, destination.stat().st_size, digest.hexdigest(), created_at,
                        entries, excluded)


# ---- open / verify -----------------------------------------------------------------------

@contextmanager
def _open_verified(archive_path: Path | str, passphrase: str) -> Iterator[tuple[dict[str, Any], Path]]:
    """Decrypt and fully verify an archive into a private temp dir; yield (manifest, dir)."""
    key_material = _check_passphrase(passphrase)
    archive_path = Path(archive_path)
    try:
        handle = archive_path.open("rb")
    except OSError:
        raise BackupError("The backup file could not be opened.") from None
    with handle, _private_dir() as work:
        header = handle.read(HEADER_SIZE)
        log2_n, r, p, salt, prefix, _ = _parse_header(header)
        key = _derive_key(key_material, salt, log2_n, r, p)
        payload = work / "payload.tar.gz"
        with payload.open("wb") as plain:
            _decrypt_stream(handle, plain, key, header, prefix)
        staging = work / "files"
        staging.mkdir(mode=0o700)
        manifest = _extract(payload, staging)
        for entry in manifest["files"]:
            if _integrity(staging / entry["name"]) != "ok":
                raise BackupFormatError(f"{entry['name']} in the backup failed its integrity check.")
        yield manifest, staging


def _extract(payload: Path, staging: Path) -> dict[str, Any]:
    try:
        with tarfile.open(payload, "r:gz") as archive:
            members = iter(archive)
            first = next(members, None)
            if first is None or first.name != "manifest.json" or not first.isreg():
                raise BackupFormatError("The backup has no manifest.")
            if first.size > MAX_MANIFEST_BYTES:
                raise BackupFormatError("The backup manifest is too large.")
            stream = archive.extractfile(first)
            manifest = _validate_manifest(json.loads(stream.read(MAX_MANIFEST_BYTES + 1)))  # type: ignore[union-attr]
            declared = {entry["name"]: entry for entry in manifest["files"]}
            seen: set[str] = set()
            for member in members:
                entry = declared.get(member.name)
                if entry is None or member.name in seen or not member.isreg():
                    raise BackupFormatError("The backup contains an unexpected entry.")
                if member.size != entry["size"]:
                    raise BackupFormatError("A file in the backup has the wrong size.")
                seen.add(member.name)
                digest = hashlib.sha256()
                source = archive.extractfile(member)
                with (staging / member.name).open("wb") as out:
                    remaining = member.size
                    while remaining:
                        block = source.read(min(1 << 20, remaining))  # type: ignore[union-attr]
                        if not block:
                            raise BackupFormatError("A file in the backup is incomplete.")
                        digest.update(block)
                        out.write(block)
                        remaining -= len(block)
                if digest.hexdigest() != entry["sha256"]:
                    raise BackupFormatError(f"{member.name} does not match its recorded checksum.")
            if seen != set(declared):
                raise BackupFormatError("The backup is missing files listed in its manifest.")
    except (tarfile.TarError, EOFError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise BackupFormatError("The backup contents are damaged or not valid.") from None
    return manifest


def _validate_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT_VERSION \
            or manifest.get("app") != "mia":
        raise BackupFormatError("This backup has an unsupported format version.")
    files = manifest.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise BackupFormatError("The backup manifest is not valid.")
    names: set[str] = set()
    total = 0
    for entry in files:
        if not isinstance(entry, dict):
            raise BackupFormatError("The backup manifest is not valid.")
        name, size, sha = entry.get("name"), entry.get("size"), entry.get("sha256")
        if (not isinstance(name, str) or not INCLUDE_PATTERN.match(name)
                or name in EXCLUDED_BY_POLICY or name in names
                or not isinstance(size, int) or isinstance(size, bool) or size < 0
                or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha)):
            raise BackupFormatError("The backup manifest is not valid.")
        names.add(name)
        total += size
    if total > MAX_TOTAL_BYTES:
        raise BackupFormatError("The backup is larger than Mia supports.")
    if not isinstance(manifest.get("created_at"), str):
        raise BackupFormatError("The backup manifest is not valid.")
    manifest.setdefault("excluded_by_policy", [])
    return manifest


def verify_backup(archive_path: Path | str, passphrase: str) -> BackupSummary:
    """Decrypt and check everything (authentication, checksums, SQLite integrity); write nothing."""
    with _open_verified(archive_path, passphrase) as (manifest, staging):
        return BackupSummary(
            created_at=manifest["created_at"],
            files=list(manifest["files"]),
            excluded_by_policy=[str(name) for name in manifest["excluded_by_policy"]],
            integrity={entry["name"]: _integrity(staging / entry["name"])
                       for entry in manifest["files"]},
        )


# ---- preview / restore -------------------------------------------------------------------

def _plan(manifest: dict[str, Any], target: Path, policy: str) -> tuple[list[dict[str, Any]], list[str]]:
    if policy not in POLICIES:
        raise BackupError("Unknown restore policy. Use abort, overwrite or side_by_side.")
    items, warnings = [], []
    for entry in manifest["files"]:
        name = entry["name"]
        existing = target / name
        sensitive = name in NEVER_OVERWRITE
        item: dict[str, Any] = {"name": name, "backup_bytes": entry["size"],
                                "rollback_sensitive": sensitive}
        if existing.is_symlink() or (existing.exists() and not existing.is_file()):
            item.update(state="blocked", action="blocked", existing_bytes=None)
        elif not existing.exists():
            item.update(state="new", action="create", existing_bytes=None)
        elif _sha256_file(existing) == entry["sha256"]:
            item.update(state="identical", action="skip", existing_bytes=entry["size"])
        else:
            item.update(state="differs", existing_bytes=existing.stat().st_size)
            if policy == "abort":
                item["action"] = "blocked"
            elif policy == "side_by_side" or sensitive:
                item["action"] = "restore_as_copy"
            else:
                item["action"] = "replace_keeping_undo_copy"
            if sensitive and policy != "abort":
                warnings.append(
                    f"{name} is the spend ledger; it is never overwritten (an older copy would "
                    "lower recorded spend), so it is restored next to the current one."
                )
        items.append(item)
    return items, warnings


def preview_restore(
    archive_path: Path | str, passphrase: str, target_dir: Path | str, *, policy: str = "abort"
) -> RestorePreview:
    """Fully verify the archive and report what a restore would do. Writes nothing."""
    target = Path(target_dir)
    with _open_verified(archive_path, passphrase) as (manifest, _staging):
        items, warnings = _plan(manifest, target, policy)
        return RestorePreview(
            policy=policy, verified=True, created_at=manifest["created_at"], items=items,
            would_succeed=all(item["action"] != "blocked" for item in items), warnings=warnings,
        )


def restore_backup(
    archive_path: Path | str,
    passphrase: str,
    target_dir: Path | str,
    *,
    policy: str = "abort",
    now: datetime | None = None,
) -> RestoreResult:
    """Verify the archive, then place its files in ``target_dir`` atomically with rollback."""
    target = Path(target_dir)
    stamp = _utc(now).strftime("%Y%m%dT%H%M%SZ")
    with _open_verified(archive_path, passphrase) as (manifest, staging):
        items, warnings = _plan(manifest, target, policy)
        unusable = [item["name"] for item in items if item["state"] == "blocked"]
        if unusable:
            raise BackupError("Restore stopped: a target path is not a regular file: "
                              + ", ".join(unusable) + ". Nothing was changed.")
        conflicts = [item["name"] for item in items if item["action"] == "blocked"]
        if conflicts:
            raise RestoreConflictError(conflicts)
        target.mkdir(parents=True, exist_ok=True)
        undo_dir = target / f"restore-undo-{stamp}"
        placed: list[Path] = []
        moved: list[tuple[Path, Path]] = []
        restored, skipped, copies = [], [], {}
        try:
            for item in items:
                name, action = item["name"], item["action"]
                if action == "skip":
                    skipped.append(name)
                    continue
                if action == "restore_as_copy":
                    destination = target / f"{Path(name).stem}.restored-{stamp}.sqlite3"
                    copies[name] = destination.name
                else:
                    destination = target / name
                    for sidecar in [destination, *(destination.with_name(name + s)
                                                   for s in SIDECAR_SUFFIXES)]:
                        if sidecar.exists():
                            undo_dir.mkdir(exist_ok=True)
                            kept = undo_dir / sidecar.name
                            os.replace(sidecar, kept)
                            moved.append((sidecar, kept))
                    restored.append(name)
                if destination.exists():
                    raise RestoreError(f"{destination.name} already exists; nothing was overwritten.")
                temporary = target / f".restore-{secrets.token_hex(6)}.tmp"
                try:
                    shutil.copyfile(staging / name, temporary)
                    os.chmod(temporary, 0o600)
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
                placed.append(destination)
        except BaseException as exc:
            for path in placed:
                path.unlink(missing_ok=True)
            for original, kept in reversed(moved):
                os.replace(kept, original)
            if undo_dir.exists() and not any(undo_dir.iterdir()):
                undo_dir.rmdir()
            if isinstance(exc, BackupError):
                raise
            if isinstance(exc, Exception):
                raise RestoreError("Placing the restored files failed; the folder was rolled back.") from None
            raise
    return RestoreResult(
        policy=policy, restored=restored, skipped_identical=skipped, side_by_side=copies,
        undo_dir=str(undo_dir) if moved else None, warnings=warnings,
    )
