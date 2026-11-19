"""Governed file operations built on :class:`FilesystemAdapter`.

Read-only helpers (metadata, content search, compare, duplicates, project inspection) run
immediately. Mutations (mkdir, rename, copy, move, delete, restore) follow
preview -> owner approval -> execute, with the approval bound to the exact source state.

Safety properties:
* Destinations are never silently overwritten.
* Delete moves the item into ``.mia-recovery/`` inside the same root and can be restored.
* Every mutation is verified by inspecting the filesystem afterwards; results carry ``verified``.
* Sources and destinations go through the adapter's path, symlink/junction and credential checks.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mia.memory.safety import find_secret

from .control import AutomationControl, activity_or_null
from .filesystem import RECOVERY_DIR, FilesystemAccessError, FilesystemAdapter, _is_link

MUTATING_OPS = {"mkdir", "rename", "copy", "move", "delete", "restore"}


class FileOperationError(RuntimeError):
    """A file operation is invalid or could not be verified."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FileOperations:
    MAX_COPY_BYTES = 100 * 1024 * 1024
    MAX_TREE_ENTRIES = 5000
    MAX_DUPLICATE_FILES = 5000
    MAX_COMPARE_BYTES = 512 * 1024

    def __init__(self, fs: FilesystemAdapter, control: AutomationControl | None = None) -> None:
        self.fs = fs
        self.control = control

    # ---- helpers ------------------------------------------------------------------------
    def _rel(self, root_id: str, path: Path) -> str:
        return str(path.relative_to(self.fs.roots[root_id]))

    def _reject_recovery(self, rel: str) -> None:
        if Path(rel).parts[:1] == (RECOVERY_DIR,):
            raise FilesystemAccessError("The recovery area is managed by Mia and cannot be targeted")

    def _resolve(self, root_id: str, rel: str, *, write: bool) -> Path:
        self._reject_recovery(rel)
        path = self.fs._resolve(root_id, rel)
        if path == self.fs.roots[root_id] and write:
            raise FilesystemAccessError("The filesystem root itself cannot be modified")
        if write:
            self.fs._validate_write_path(path)
        else:
            self.fs._validate_read_path(path)
        return path

    def _fingerprint(self, path: Path) -> dict[str, Any]:
        """Content identity of a file or directory tree; refuses links and secrets."""
        if _is_link(path):
            raise FilesystemAccessError("Links are not available to computer tools")
        if path.is_file():
            return {"kind": "file", "entries": 1, "bytes": path.stat().st_size, "sha256": _sha256(path)}
        if not path.is_dir():
            raise FileNotFoundError("Source not found")
        lines: list[str] = []
        total = 0
        for current, dirs, files in os.walk(path, followlinks=False):
            for name in list(dirs):
                if _is_link(Path(current) / name):
                    raise FilesystemAccessError("Directory contains links; operation refused")
            for name in files:
                item = Path(current) / name
                if _is_link(item):
                    raise FilesystemAccessError("Directory contains links; operation refused")
                self.fs._validate_read_path(item)
                size = item.stat().st_size
                total += size
                lines.append(f"{item.relative_to(path).as_posix()}|{size}|{_sha256(item)}")
                if len(lines) > self.MAX_TREE_ENTRIES:
                    raise ValueError(f"Directory has more than {self.MAX_TREE_ENTRIES} files")
        digest = hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()
        return {"kind": "directory", "entries": len(lines), "bytes": total, "sha256": digest}

    # ---- read-only ------------------------------------------------------------------------
    def metadata(self, root_id: str, relative_path: str) -> dict[str, Any]:
        path = self._resolve(root_id, relative_path, write=False)
        if not path.exists():
            raise FileNotFoundError("Path not found")
        stat = path.stat()
        return {
            "relative_path": self._rel(root_id, path) if path != self.fs.roots[root_id] else ".",
            "kind": "directory" if path.is_dir() else "file",
            "size_bytes": stat.st_size if path.is_file() else None,
            "modified": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            "extension": path.suffix.lower() or None,
            "sha256": _sha256(path) if path.is_file() and stat.st_size <= self.MAX_COPY_BYTES else None,
        }

    def _walk_files(self, root_id: str, relative_path: str):
        base = self._resolve(root_id, relative_path, write=False)
        if not base.is_dir():
            raise FileNotFoundError("Directory not found")
        root = self.fs.roots[root_id]
        for current, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(
                n for n in dirs if n != RECOVERY_DIR and not _is_link(Path(current) / n)
                and n.casefold() not in {".git", ".ssh", ".gnupg", ".aws", ".azure"}
            )
            for name in sorted(files):
                item = Path(current) / name
                if _is_link(item):
                    continue
                try:
                    self.fs._validate_read_path(item)
                except FilesystemAccessError:
                    continue
                yield root, item

    def search_content(
        self, root_id: str, query: str, relative_path: str = ".", *, limit: int = 50
    ) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("Search query is required")
        needle = query.casefold()
        hits: list[dict[str, Any]] = []
        scanned = 0
        for root, item in self._walk_files(root_id, relative_path):
            scanned += 1
            if scanned > 2000:
                break
            try:
                if item.stat().st_size > 1024 * 1024:
                    continue
                raw = item.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw:
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if needle in line.casefold():
                    snippet = "[redacted: possible secret]" if find_secret(line) else line.strip()[:200]
                    hits.append({"relative_path": str(item.relative_to(root)), "line": number,
                                 "text": snippet})
                    if len(hits) >= max(1, min(limit, 200)):
                        return hits
        return hits

    def compare_files(self, root_id: str, path_a: str, path_b: str) -> dict[str, Any]:
        texts = []
        for rel in (path_a, path_b):
            path = self._resolve(root_id, rel, write=False)
            if not path.is_file():
                raise FileNotFoundError("Regular file not found")
            if path.stat().st_size > self.MAX_COMPARE_BYTES:
                raise ValueError("File exceeds the 512 KB compare limit")
            texts.append(self.fs.read_text_auto(root_id, rel)["content"])
        diff = list(difflib.unified_diff(
            texts[0].splitlines(), texts[1].splitlines(), path_a, path_b, lineterm="", n=2))
        redacted = ["[redacted: possible secret]" if find_secret(ln) else ln for ln in diff[:400]]
        return {"identical": texts[0] == texts[1], "diff": redacted, "truncated": len(diff) > 400}

    def find_duplicates(self, root_id: str, relative_path: str = ".", *, min_bytes: int = 1) -> dict[str, Any]:
        by_size: dict[int, list[Path]] = {}
        count = 0
        for _root, item in self._walk_files(root_id, relative_path):
            count += 1
            if count > self.MAX_DUPLICATE_FILES:
                raise ValueError(f"More than {self.MAX_DUPLICATE_FILES} files; narrow the folder")
            size = item.stat().st_size
            if size >= max(1, min_bytes):
                by_size.setdefault(size, []).append(item)
        groups = []
        for size, items in by_size.items():
            if len(items) < 2:
                continue
            by_hash: dict[str, list[Path]] = {}
            for item in items:
                by_hash.setdefault(_sha256(item), []).append(item)
            for digest, same in by_hash.items():
                if len(same) > 1:
                    groups.append({
                        "sha256": digest, "size_bytes": size,
                        "files": sorted(self._rel(root_id, p) for p in same),
                    })
        groups.sort(key=lambda g: -g["size_bytes"] * (len(g["files"]) - 1))
        wasted = sum(g["size_bytes"] * (len(g["files"]) - 1) for g in groups)
        return {"files_scanned": count, "groups": groups, "reclaimable_bytes": wasted}

    _PROJECT_MARKERS = {
        "pyproject.toml": "python", "setup.py": "python", "requirements.txt": "python",
        "package.json": "node", "Cargo.toml": "rust", "go.mod": "go", "pom.xml": "java",
        "build.gradle": "java", "pubspec.yaml": "flutter", "CMakeLists.txt": "c/c++",
    }

    def inspect_project(self, root_id: str, relative_path: str = ".") -> dict[str, Any]:
        base = self._resolve(root_id, relative_path, write=False)
        if not base.is_dir():
            raise FileNotFoundError("Directory not found")
        languages = sorted({v for k, v in self._PROJECT_MARKERS.items() if (base / k).exists()})
        extensions: dict[str, int] = {}
        total = 0
        for _root, item in self._walk_files(root_id, relative_path):
            total += 1
            if total > 20000:
                break
            extensions[item.suffix.lower() or "(none)"] = extensions.get(item.suffix.lower() or "(none)", 0) + 1
        top = []
        for entry in sorted(base.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            if entry.name in {RECOVERY_DIR, ".git"} or _is_link(entry):
                continue
            top.append({"name": entry.name, "kind": "directory" if entry.is_dir() else "file"})
            if len(top) >= 60:
                break
        readme = None
        for name in ("README.md", "README.rst", "README.txt", "README"):
            if (base / name).is_file():
                try:
                    readme = {"file": name, "excerpt": self._safe_excerpt(root_id, base / name)}
                except (OSError, ValueError, FilesystemAccessError):
                    pass
                break
        docs = sorted(self._rel(root_id, p) for p in (base / "docs").glob("*.md"))[:20] \
            if (base / "docs").is_dir() and not _is_link(base / "docs") else []
        return {
            "project_types": languages, "file_count": total,
            "files_by_extension": dict(sorted(extensions.items(), key=lambda kv: -kv[1])[:15]),
            "top_level": top, "readme": readme, "docs": docs,
            "has_git": (base / ".git").exists(), "has_tests": any(
                (base / n).exists() for n in ("tests", "test", "__tests__", "spec")),
        }

    def _safe_excerpt(self, root_id: str, path: Path) -> str:
        text = self.fs.read_text_auto(root_id, self._rel(root_id, path))["content"][:1500]
        return "\n".join("[redacted: possible secret]" if find_secret(ln) else ln
                         for ln in text.splitlines())

    # ---- mutations ------------------------------------------------------------------------
    def _plan(self, op: str, root_id: str, relative_path: str, destination: str | None,
              recovery_id: str | None) -> dict[str, Any]:
        if op not in MUTATING_OPS:
            raise ValueError(f"Unknown file operation: {op}")
        plan: dict[str, Any] = {"op": op, "root_id": root_id}
        if op == "restore":
            if not recovery_id or not recovery_id.isalnum():
                raise ValueError("A valid recovery_id is required")
            manifest = self._read_manifest(root_id, recovery_id)
            dest = self._resolve(root_id, manifest["original_relative_path"], write=True)
            if dest.exists():
                raise FileOperationError("The original location is occupied; restore refused")
            if not dest.parent.is_dir():
                raise FileNotFoundError("The original folder no longer exists; recreate it first")
            plan.update(recovery_id=recovery_id, destination=self._rel(root_id, dest),
                        fingerprint=manifest["fingerprint"]["sha256"])
            return plan
        src = self._resolve(root_id, relative_path, write=True)
        plan["source"] = self._rel(root_id, src) if src != self.fs.roots[root_id] else "."
        if op == "mkdir":
            if src.exists():
                raise FileOperationError("Path already exists")
            return plan
        if not src.exists():
            raise FileNotFoundError("Source not found")
        fp = self._fingerprint(src)
        plan["fingerprint"] = fp["sha256"]
        plan["kind"] = fp["kind"]
        plan["entries"] = fp["entries"]
        plan["bytes"] = fp["bytes"]
        if op in {"copy", "move", "rename"}:
            if not destination:
                raise ValueError("destination_path is required")
            if op == "rename" and Path(destination).parent != Path(relative_path).parent:
                raise ValueError("Rename keeps the item in its folder; use move to change folders")
            dst = self._resolve(root_id, destination, write=True)
            if dst.exists():
                raise FileOperationError("Destination exists; overwriting is not allowed")
            if not dst.parent.is_dir():
                raise FileNotFoundError("Destination folder does not exist")
            if fp["kind"] == "directory" and (dst == src or src in dst.parents):
                raise FileOperationError("Cannot copy or move a folder into itself")
            if op == "copy" and fp["bytes"] > self.MAX_COPY_BYTES:
                raise ValueError("Copy exceeds the 100 MB limit")
            plan["destination"] = self._rel(root_id, dst)
        return plan

    def preview(self, op: str, root_id: str, relative_path: str = "", *,
                destination_path: str | None = None, recovery_id: str | None = None) -> dict[str, Any]:
        plan = self._plan(op, root_id, relative_path, destination_path, recovery_id)
        target = plan.get("source", plan.get("recovery_id"))
        summary = f"{op} {target}" + (f" -> {plan['destination']}" if "destination" in plan else "")
        approval = self.fs.approvals.request(
            action_id=f"filesystem.{op}", payload=plan, summary=summary[:300])
        return {**approval, "operation": plan}

    def execute(self, op: str, approval_id: str, root_id: str, relative_path: str = "", *,
                destination_path: str | None = None, recovery_id: str | None = None) -> dict[str, Any]:
        if self.control is not None:
            self.control.assert_active()
        plan = self._plan(op, root_id, relative_path, destination_path, recovery_id)
        self.fs.approvals.consume(approval_id=approval_id, action_id=f"filesystem.{op}", payload=plan)
        with activity_or_null(self.control, f"filesystem.{op}",
                              f"File {op}: {plan.get('source', plan.get('recovery_id', ''))}"[:100]):
            return self._execute_plan(op, root_id, plan)

    def _execute_plan(self, op: str, root_id: str, plan: dict[str, Any]) -> dict[str, Any]:
        root = self.fs.roots[root_id]
        result: dict[str, Any] = {"op": op, "operation": plan}
        if op == "mkdir":
            path = root / plan["source"]
            path.mkdir(parents=True)
            result["verified"] = path.is_dir() and not _is_link(path)
        elif op == "copy":
            src, dst = root / plan["source"], root / plan["destination"]
            shutil.copy2(src, dst) if plan["kind"] == "file" else shutil.copytree(src, dst, symlinks=False)
            result["verified"] = self._fingerprint(dst)["sha256"] == plan["fingerprint"]
        elif op in {"move", "rename"}:
            src, dst = root / plan["source"], root / plan["destination"]
            os.replace(src, dst) if plan["kind"] == "file" else shutil.move(str(src), str(dst))
            result["verified"] = (not src.exists()) and self._fingerprint(dst)["sha256"] == plan["fingerprint"]
        elif op == "delete":
            src = root / plan["source"]
            rid = uuid.uuid4().hex
            folder = root / RECOVERY_DIR / "deleted" / rid
            folder.mkdir(parents=True)
            fp = self._fingerprint(src)
            manifest = {"recovery_id": rid, "original_relative_path": plan["source"],
                        "deleted_at": datetime.now(UTC).isoformat(), "kind": plan["kind"],
                        "fingerprint": fp}
            (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            shutil.move(str(src), str(folder / "item"))
            result["recovery_id"] = rid
            result["verified"] = (not src.exists()) and self._fingerprint(folder / "item")["sha256"] == fp["sha256"]
        elif op == "restore":
            folder = root / RECOVERY_DIR / "deleted" / plan["recovery_id"]
            dst = root / plan["destination"]
            shutil.move(str(folder / "item"), str(dst))
            verified = self._fingerprint(dst)["sha256"] == plan["fingerprint"]
            if verified:
                shutil.rmtree(folder, ignore_errors=True)
            result["verified"] = verified
        if not result["verified"]:
            raise FileOperationError(f"{op} finished but verification failed; inspect the folder")
        return result

    # ---- recovery -------------------------------------------------------------------------
    def _read_manifest(self, root_id: str, recovery_id: str) -> dict[str, Any]:
        folder = self.fs.roots[root_id] / RECOVERY_DIR / "deleted" / recovery_id
        manifest = folder / "manifest.json"
        if _is_link(folder) or not manifest.is_file() or not (folder / "item").exists():
            raise FileNotFoundError("Recovery item not found")
        return json.loads(manifest.read_text(encoding="utf-8"))

    def list_recovery(self, root_id: str) -> list[dict[str, Any]]:
        base = self.fs.roots[root_id] / RECOVERY_DIR / "deleted"
        items = []
        if base.is_dir():
            for folder in sorted(base.iterdir()):
                try:
                    m = self._read_manifest(root_id, folder.name)
                except (FileNotFoundError, json.JSONDecodeError):
                    continue
                items.append({k: m[k] for k in ("recovery_id", "original_relative_path",
                                                 "deleted_at", "kind")})
        return items
