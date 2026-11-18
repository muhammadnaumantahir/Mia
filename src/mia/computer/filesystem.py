"""Scoped filesystem access with preview, approval and one-time execution."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from .approvals import ApprovalStore


class FilesystemAccessError(PermissionError):
    """The requested path is outside the granted filesystem scope."""


_BLOCKED_FILENAMES = {
    ".env",
    ".env.local",
    "id_rsa",
    "id_ed25519",
}
_BLOCKED_SUFFIXES = {".pem", ".key", ".pfx", ".p12", ".kdbx"}


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
            if candidate.is_symlink():
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
        if not path.is_file():
            raise FileNotFoundError("Regular file not found")
        if path.stat().st_size > 25 * 1024 * 1024:
            raise ValueError("File exceeds the 25 MB ingestion limit")
        return path.read_bytes()

    def read_text(self, root_id: str, relative_path: str) -> str:
        path = self._resolve(root_id, relative_path)
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
            dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
            for filename in files:
                if query.casefold() in filename.casefold():
                    matches.append(str((Path(current) / filename).relative_to(root)))
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
        }

    def _validate_write_path(self, path: Path) -> None:
        filename = path.name.casefold()
        if filename in _BLOCKED_FILENAMES or filename.startswith(".env."):
            raise FilesystemAccessError("Writing credential and environment files is blocked")
        if path.suffix.casefold() in _BLOCKED_SUFFIXES:
            raise FilesystemAccessError("Writing key/certificate files is blocked")
        if any(part.casefold() == ".git" for part in path.parts):
            raise FilesystemAccessError("Direct writes into Git metadata are blocked")
        if path.exists() and path.is_symlink():
            raise FilesystemAccessError("Symlink targets cannot be written")
