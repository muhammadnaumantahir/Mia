"""Scoped filesystem access with preview, approval and one-time execution."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .approvals import ApprovalStore


class FilesystemAccessError(PermissionError):
    """The requested path is outside the granted filesystem scope."""


class FileUndoConflict(FilesystemAccessError):
    """The file is no longer in the state Mia left it in, so an undo would destroy newer work."""


_BLOCKED_FILENAMES = {
    ".env", "id_rsa", "id_ed25519", ".npmrc", ".pypirc", ".netrc", "netrc",
    "credentials.json", "secrets.json", "token.json", "service-account.json",
}
_BLOCKED_SUFFIXES = {".pem", ".key", ".pfx", ".p12", ".kdbx"}
_BLOCKED_DIRECTORIES = {".ssh", ".gnupg", ".aws", ".azure", ".git"}
RECOVERY_DIR = ".mia-recovery"


def _is_link(path: Path) -> bool:
    """True for symlinks and, on Windows, directory junctions."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


class FilesystemAdapter:
    MAX_READ_BYTES = 2 * 1024 * 1024
    MAX_WRITE_BYTES = 2 * 1024 * 1024
    MAX_LIST_ENTRIES = 200

    def __init__(
        self,
        roots: list[str | Path],
        approvals: ApprovalStore,
    ) -> None:
        if not roots:
            raise ValueError("At least one filesystem root must be configured")
        self.roots: dict[str, Path] = {}
        for index, raw_root in enumerate(roots, start=1):
            root = Path(raw_root).expanduser().resolve()
            root.mkdir(parents=True, exist_ok=True)
            self.roots[f"root_{index}"] = root
        self.approvals = approvals

    @classmethod
    def from_environment(
        cls,
        approvals: ApprovalStore,
        *,
        workspace: str | Path = "./workspace",
    ) -> FilesystemAdapter:
        configured = os.getenv("MIA_COMPUTER_ROOTS", "").strip()
        if configured:
            roots = [item for item in configured.split(os.pathsep) if item.strip()]
        else:
            roots = [str(workspace)]
        return cls(roots, approvals)

    def list_roots(self) -> list[dict[str, str]]:
        return [
            {"root_id": root_id, "path": str(path)}
            for root_id, path in self.roots.items()
        ]

    def _resolve(self, root_id: str, relative_path: str = ".") -> Path:
        if root_id not in self.roots:
            raise FilesystemAccessError("Unknown filesystem root")
        root = self.roots[root_id]
        requested = Path(relative_path or ".")
        if requested.is_absolute():
            raise FilesystemAccessError("Use a path relative to the selected root")
        if ".." in requested.parts:
            raise FilesystemAccessError("Parent-directory traversal is not allowed")

        candidate = root
        for part in requested.parts:
            if part in {"", "."}:
                continue
            candidate = candidate / part
            if _is_link(candidate):
                raise FilesystemAccessError("Symlink paths are not available to computer tools")

        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise FilesystemAccessError("Path escapes the selected filesystem root") from exc
        return resolved

    def list_directory(
        self,
        root_id: str,
        relative_path: str = ".",
    ) -> list[dict[str, Any]]:
        directory = self._resolve(root_id, relative_path)
        if not directory.exists() or not directory.is_dir():
            raise FileNotFoundError("Directory not found")
        entries = []
        for item in sorted(directory.iterdir(), key=lambda entry: (not entry.is_dir(), entry.name.lower())):
            if item.name == RECOVERY_DIR:
                continue
            entries.append(
                {
                    "name": item.name,
                    "relative_path": str(item.relative_to(self.roots[root_id])),
                    "kind": "directory" if item.is_dir() else "file",
                    "size_bytes": item.stat().st_size if item.is_file() else None,
                }
            )
            if len(entries) >= self.MAX_LIST_ENTRIES:
                break
        return entries

    def read_bytes(self, root_id: str, relative_path: str) -> bytes:
        path = self._resolve(root_id, relative_path)
        self._validate_read_path(path)
        if not path.is_file():
            raise FileNotFoundError("Regular file not found")
        if path.stat().st_size > 25 * 1024 * 1024:
            raise ValueError("File exceeds the 25 MB ingestion limit")
        return path.read_bytes()

    def read_text(self, root_id: str, relative_path: str) -> str:
        path = self._resolve(root_id, relative_path)
        self._validate_read_path(path)
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError("Regular file not found")
        if path.stat().st_size > self.MAX_READ_BYTES:
            raise ValueError("File exceeds the 2 MB text-read limit")
        return path.read_text(encoding="utf-8")

    def search_filenames(
        self,
        root_id: str,
        query: str,
        *,
        limit: int = 100,
    ) -> list[str]:
        if not query.strip():
            raise ValueError("Search query is required")
        root = self.roots.get(root_id)
        if root is None:
            raise FilesystemAccessError("Unknown filesystem root")
        safe_limit = max(1, min(limit, 200))
        matches = []
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [
                name for name in dirs if name != RECOVERY_DIR and not _is_link(Path(current) / name)
            ]
            for filename in files:
                candidate = Path(current) / filename
                try:
                    self._validate_read_path(candidate)
                except FilesystemAccessError:
                    continue
                if query.casefold() in filename.casefold():
                    matches.append(str(candidate.relative_to(root)))
                    if len(matches) >= safe_limit:
                        return matches
        return matches

    def preview_write(
        self,
        root_id: str,
        relative_path: str,
        content: str,
    ) -> dict[str, str | int | None]:
        path = self._resolve(root_id, relative_path)
        self._validate_write_path(path)
        encoded = content.encode("utf-8")
        if len(encoded) > self.MAX_WRITE_BYTES:
            raise ValueError("Text write exceeds the 2 MB limit")
        current_hash = None
        if path.exists():
            if not path.is_file() or path.is_symlink():
                raise ValueError("Target must be a regular file")
            current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        new_hash = hashlib.sha256(encoded).hexdigest()
        payload = {
            "root_id": root_id,
            "relative_path": str(path.relative_to(self.roots[root_id])),
            "content_sha256": new_hash,
            "current_sha256": current_hash,
        }
        approval = self.approvals.request(
            action_id="filesystem.write",
            payload=payload,
            summary=f"Write {payload['relative_path']} ({len(encoded)} bytes)",
        )
        return {
            **approval,
            "root_id": root_id,
            "relative_path": payload["relative_path"],
            "current_sha256": current_hash,
            "new_sha256": new_hash,
            "size_bytes": len(encoded),
        }

    def execute_write(
        self,
        *,
        approval_id: str,
        root_id: str,
        relative_path: str,
        content: str,
    ) -> dict[str, str | int]:
        path = self._resolve(root_id, relative_path)
        self._validate_write_path(path)
        encoded = content.encode("utf-8")
        if len(encoded) > self.MAX_WRITE_BYTES:
            raise ValueError("Text write exceeds the 2 MB limit")
        current_hash = None
        if path.exists():
            if not path.is_file() or path.is_symlink():
                raise ValueError("Target must be a regular file")
            current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        payload = {
            "root_id": root_id,
            "relative_path": str(path.relative_to(self.roots[root_id])),
            "content_sha256": hashlib.sha256(encoded).hexdigest(),
            "current_sha256": current_hash,
        }
        self.approvals.consume(
            approval_id=approval_id,
            action_id="filesystem.write",
            payload=payload,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        backup = self.backup_existing(root_id, path)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=".mia-",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(encoded)
            temp_path = Path(temp_file.name)
        try:
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)
        return {
            "relative_path": payload["relative_path"],
            "sha256": payload["content_sha256"],
            "size_bytes": len(encoded),
            "backup": backup,
        }

    def _recovery_backup(self, root_id: str, backup: str, target_name: str) -> Path:
        requested = Path(backup)
        root = self.roots[root_id]
        if requested.is_absolute() or ".." in requested.parts or requested.parts[:2] != (RECOVERY_DIR, "backups") \
                or len(requested.parts) != 3 or not requested.name.endswith(f"-{target_name}"):
            raise FilesystemAccessError("That is not a recovery copy of this file")
        candidate = root / requested
        if _is_link(candidate) or not candidate.is_file():
            raise FileUndoConflict("The saved earlier version is no longer available, so nothing was undone")
        return candidate

    def undo_write(
        self, *, root_id: str, relative_path: str, expected_sha256: str, backup: str | None = None,
    ) -> dict[str, str | None]:
        """Reverse one approved write, only while the file is still exactly what Mia wrote.

        With a recovery copy the earlier version is restored; without one the file Mia created is
        removed. What is replaced or removed is first copied into the recovery area, so the undo
        itself can be recovered.
        """
        path = self._resolve(root_id, relative_path)
        self._validate_write_path(path)
        if _is_link(path) or not path.is_file():
            raise FileUndoConflict("The file is no longer there, so there is nothing to undo")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
            raise FileUndoConflict("The file has changed since Mia wrote it, so nothing was undone")
        source = self._recovery_backup(root_id, backup, path.name) if backup else None
        safety = self.backup_existing(root_id, path)
        rel = str(path.relative_to(self.roots[root_id]))
        if source is None:
            path.unlink()
            return {"action": "removed_created_file", "relative_path": rel, "safety_backup": safety, "sha256": None}
        data = source.read_bytes()
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".mia-", suffix=".tmp", delete=False) as handle:
            handle.write(data)
            temp_path = Path(handle.name)
        try:
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)
        return {"action": "restored_previous_version", "relative_path": rel, "safety_backup": safety,
                "sha256": hashlib.sha256(data).hexdigest()}

    MAX_BACKUPS = 50

    def backup_existing(self, root_id: str, path: Path) -> str | None:
        """Copy an existing file into the root's recovery area before it is replaced."""
        if not path.is_file():
            return None
        root = self.roots[root_id]
        folder = root / RECOVERY_DIR / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        target = folder / f"{stamp}-{path.name}"
        shutil.copy2(path, target)
        backups = sorted(folder.iterdir(), key=lambda item: item.name)
        for old in backups[: max(0, len(backups) - self.MAX_BACKUPS)]:
            old.unlink(missing_ok=True)
        return str(target.relative_to(root))

    def read_text_auto(self, root_id: str, relative_path: str) -> dict[str, Any]:
        """Read text, detecting UTF-8/UTF-16 (BOM) and falling back to cp1252 explicitly."""
        path = self._resolve(root_id, relative_path)
        self._validate_read_path(path)
        if _is_link(path) or not path.is_file():
            raise FileNotFoundError("Regular file not found")
        if path.stat().st_size > self.MAX_READ_BYTES:
            raise ValueError("File exceeds the 2 MB text-read limit")
        raw = path.read_bytes()
        if b"\x00" in raw and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            raise ValueError("File looks binary, not text")
        for encoding in ("utf-8-sig", "utf-16"):
            if encoding == "utf-16" and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
                continue
            try:
                return {"content": raw.decode(encoding), "encoding": encoding, "fallback": False}
            except UnicodeDecodeError:
                continue
        return {"content": raw.decode("cp1252", errors="replace"), "encoding": "cp1252",
                "fallback": True}

    @staticmethod
    def _validate_read_path(path: Path) -> None:
        filename = path.name.casefold()
        if (
            filename in _BLOCKED_FILENAMES
            or filename.startswith(".env.")
            or any(part.casefold() in _BLOCKED_DIRECTORIES for part in path.parts)
            or path.suffix.casefold() in _BLOCKED_SUFFIXES
        ):
            raise FilesystemAccessError(
                "Reading credential, key and private configuration files is blocked"
            )

    def _validate_write_path(self, path: Path) -> None:
        self._validate_read_path(path)
        filename = path.name.casefold()
        if filename in _BLOCKED_FILENAMES or filename.startswith(".env."):
            raise FilesystemAccessError("Writing credential and environment files is blocked")
        if path.suffix.casefold() in _BLOCKED_SUFFIXES:
            raise FilesystemAccessError("Writing key/certificate files is blocked")
        if any(part.casefold() == ".git" for part in path.parts):
            raise FilesystemAccessError("Direct writes into Git metadata are blocked")
        if path.exists() and path.is_symlink():
            raise FilesystemAccessError("Symlink targets cannot be written")
