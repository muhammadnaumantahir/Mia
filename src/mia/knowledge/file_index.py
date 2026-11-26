"""MIA-LIFE-06: owner-selected file indexing and search with verifiable source locations.

Design rules (each has a test):

* Nothing is indexed until the owner *selects* a folder inside a configured filesystem root.
  Selection paths go through ``FilesystemAdapter._resolve`` (no absolute paths, ``..``, symlinks
  or escapes). Files are read only through the adapter, so its secret/credential blocklist and
  size limits apply, and anything outside a selection is never read.
* Symlinks/junctions, credential-like names, hidden VCS/recovery directories and files that
  contain private-key blocks are skipped and the reason is recorded.
* Indexed text is **untrusted data**. Chunks carry an injection flag (same detector as research)
  and search results say so; nothing here ever follows instructions found in a file.
* Every hit has a source location (root, relative path, line range of the text it came from) plus
  a staleness check, and ``verify`` re-reads the live file to prove the cited text still exists.
* Deletion is real: removing a file, a selection or the whole index deletes rows, uses
  ``secure_delete``, rebuilds FTS and vacuums, so removed text is not left in the database file.
* This is lexical (FTS5 keyword) search. It does not summarise or rank by meaning; a model
  summary, if added later, must go through the Gateway and cite these chunks.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from mia.computer.control import AutomationStopped
from mia.computer.filesystem import (
    RECOVERY_DIR,
    FilesystemAccessError,
    FilesystemAdapter,
    _is_link,
)
from mia.knowledge.document_ingestion import DocumentIngestionError, DocumentIngestor
from mia.life.research import detect_injection

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".log"}
DOC_SUFFIXES = {".pdf", ".docx", ".xlsx", ".pptx"}
SKIP_DIRS = {".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv", RECOVERY_DIR}
_PRIVATE_KEY = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")

MAX_FILES_PER_SELECTION = 2000
MAX_TEXT_BYTES = 2 * 1024 * 1024
CHUNK_CHARS = 1200


class SelectionInput(BaseModel):
    root_id: str
    path: str = "."


class FileIndexError(ValueError):
    """The request is not allowed or is malformed."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _chunk_lines(text: str) -> list[tuple[int, int, str]]:
    """Split into (first_line, last_line, text) windows of about CHUNK_CHARS; 1-based inclusive."""
    chunks: list[tuple[int, int, str]] = []
    buf: list[str] = []
    size = 0
    start = 1
    for number, line in enumerate(text.splitlines(), start=1):
        if not buf:
            start = number
        buf.append(line)
        size += len(line) + 1
        if size >= CHUNK_CHARS:
            chunks.append((start, number, "\n".join(buf)))
            buf, size = [], 0
    if buf:
        chunks.append((start, start + len(buf) - 1, "\n".join(buf)))
    return [c for c in chunks if c[2].strip()]


def _fts_query(raw: str) -> str:
    """Quote every token so user input can never be interpreted as FTS5 syntax."""
    tokens = re.findall(r"\w+", raw, flags=re.UNICODE)[:12]
    if not tokens:
        raise FileIndexError("Search needs at least one word")
    return " ".join('"' + t.replace('"', "") + '"' for t in tokens)


class FileIndex:
    def __init__(
        self,
        db_path: str | Path,
        filesystem: FilesystemAdapter,
        ingestor: DocumentIngestor,
        *,
        should_stop: Callable[[], bool] = lambda: False,
    ) -> None:
        self.filesystem = filesystem
        self.ingestor = ingestor
        self.should_stop = should_stop
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS selections(
              id TEXT PRIMARY KEY, root_id TEXT NOT NULL, rel_path TEXT NOT NULL,
              created_at TEXT NOT NULL, last_indexed_at TEXT, UNIQUE(root_id, rel_path));
            CREATE TABLE IF NOT EXISTS files(
              id TEXT PRIMARY KEY, selection_id TEXT NOT NULL, root_id TEXT NOT NULL,
              rel_path TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
              sha256 TEXT NOT NULL, location_kind TEXT NOT NULL, indexed_at TEXT NOT NULL,
              UNIQUE(root_id, rel_path));
            CREATE TABLE IF NOT EXISTS chunks(
              id INTEGER PRIMARY KEY AUTOINCREMENT, file_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
              line_start INTEGER NOT NULL, line_end INTEGER NOT NULL, text TEXT NOT NULL,
              injection INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS skipped(
              selection_id TEXT NOT NULL, rel_path TEXT NOT NULL, reason TEXT NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id');
            """
        )
        self.db.commit()

    # ------------------------------------------------------------------ selections
    def add_selection(self, root_id: str, rel_path: str = ".") -> dict[str, Any]:
        try:
            base = self.filesystem._resolve(root_id, rel_path)
            self.filesystem._validate_read_path(base)
        except FilesystemAccessError as exc:
            raise FileIndexError(str(exc)) from exc
        if not base.is_dir():
            raise FileIndexError("Select a folder, not a file")
        rel = str(base.relative_to(self.filesystem.roots[root_id])) if base != self.filesystem.roots[root_id] else "."
        with self._lock:
            row = self.db.execute("SELECT id FROM selections WHERE root_id=? AND rel_path=?", (root_id, rel)).fetchone()
            if row:
                return self.get_selection(row["id"])
            sid = "sel_" + uuid.uuid4().hex[:12]
            self.db.execute("INSERT INTO selections(id,root_id,rel_path,created_at) VALUES(?,?,?,?)",
                            (sid, root_id, rel, _now()))
            self.db.commit()
        return self.get_selection(sid)

    def get_selection(self, selection_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM selections WHERE id=?", (selection_id,)).fetchone()
        if row is None:
            raise KeyError(selection_id)
        files = self.db.execute("SELECT COUNT(*) c FROM files WHERE selection_id=?", (selection_id,)).fetchone()["c"]
        skipped = self.db.execute("SELECT rel_path, reason FROM skipped WHERE selection_id=? LIMIT 200",
                                  (selection_id,)).fetchall()
        return {**dict(row), "file_count": files, "skipped": [dict(s) for s in skipped]}

    def list_selections(self) -> list[dict[str, Any]]:
        ids = [r["id"] for r in self.db.execute("SELECT id FROM selections ORDER BY created_at")]
        return [self.get_selection(i) for i in ids]

    # ------------------------------------------------------------------ indexing
    def _candidates(self, sel: sqlite3.Row) -> tuple[list[str], list[tuple[str, str]]]:
        root = self.filesystem.roots[sel["root_id"]]
        base = self.filesystem._resolve(sel["root_id"], sel["rel_path"])
        files: list[str] = []
        skipped: list[tuple[str, str]] = []
        stack = [base]
        while stack:
            directory = stack.pop()
            try:
                entries = sorted(os.scandir(directory), key=lambda e: e.name)
            except OSError:
                skipped.append((str(directory.relative_to(root)), "unreadable_directory"))
                continue
            for entry in entries:
                path = Path(entry.path)
                rel = str(path.relative_to(root))
                if _is_link(path):
                    skipped.append((rel, "symlink"))
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in SKIP_DIRS or entry.name.casefold() in {".ssh", ".gnupg", ".aws", ".azure"}:
                        skipped.append((rel, "excluded_directory"))
                    else:
                        stack.append(path)
                    continue
                try:
                    self.filesystem._validate_read_path(path)
                except FilesystemAccessError:
                    skipped.append((rel, "credential_like_name"))
                    continue
                if path.suffix.lower() not in TEXT_SUFFIXES | DOC_SUFFIXES:
                    continue  # unsupported types are simply not indexed; not worth a skip row
                files.append(rel)
                if len(files) >= MAX_FILES_PER_SELECTION:
                    skipped.append((rel, "file_limit_reached"))
                    return files, skipped
        return files, skipped

    def _read(self, root_id: str, rel: str) -> tuple[str, str, bytes]:
        raw = self.filesystem.read_bytes(root_id, rel)  # root, symlink, secret-name and size checks
        suffix = Path(rel).suffix.lower()
        if suffix in TEXT_SUFFIXES and len(raw) > MAX_TEXT_BYTES:
            raise FileIndexError("file_too_large")
        if _PRIVATE_KEY.search(raw):
            raise FileIndexError("contains_private_key")
        text, _parser = self.ingestor._extract(suffix, raw)
        kind = "file_lines" if suffix in TEXT_SUFFIXES else "extracted_text_lines"
        return text, kind, raw

    def reindex(self, selection_id: str) -> dict[str, Any]:
        with self._lock:
            sel = self.db.execute("SELECT * FROM selections WHERE id=?", (selection_id,)).fetchone()
            if sel is None:
                raise KeyError(selection_id)
            files, skipped = self._candidates(sel)
            self.db.execute("DELETE FROM skipped WHERE selection_id=?", (selection_id,))
            stats = {"indexed": 0, "unchanged": 0, "removed": 0, "skipped": 0, "stopped": False}
            seen: set[str] = set()
            try:
                for rel in files:
                    if self.should_stop():
                        raise AutomationStopped("Indexing stopped by the stop-all control")
                    seen.add(rel)
                    outcome = self._index_file(sel, rel)
                    if outcome.startswith("skip:"):
                        skipped.append((rel, outcome[5:]))
                        seen.discard(rel)
                    else:
                        stats[outcome] += 1
            except AutomationStopped:
                stats["stopped"] = True
                self._finish(selection_id, skipped, seen=None)
                self.db.commit()
                raise
            stats["removed"] = self._finish(selection_id, skipped, seen=seen)
            stats["skipped"] = len(skipped)
            self.db.execute("UPDATE selections SET last_indexed_at=? WHERE id=?", (_now(), selection_id))
            self.db.commit()
            # A watch cycle often finds no changes; avoid an expensive FTS optimize/VACUUM
            # on every poll and compact only after an actual index mutation.
            if stats["indexed"] or stats["removed"] or stats["skipped"]:
                self._compact()
        return {"selection_id": selection_id, **stats}

    def _finish(self, selection_id: str, skipped: list[tuple[str, str]], *, seen: set[str] | None) -> int:
        for rel, reason in skipped:
            self.db.execute("INSERT INTO skipped VALUES(?,?,?)", (selection_id, rel, reason))
        if seen is None:
            return 0
        removed = 0
        for row in self.db.execute("SELECT id, rel_path FROM files WHERE selection_id=?", (selection_id,)).fetchall():
            if row["rel_path"] not in seen:
                self._delete_file_rows(row["id"])
                removed += 1
        return removed

    def _index_file(self, sel: sqlite3.Row, rel: str) -> str:
        root_id = sel["root_id"]
        try:
            stat = (self.filesystem.roots[root_id] / rel).stat()
            existing = self.db.execute("SELECT * FROM files WHERE root_id=? AND rel_path=?", (root_id, rel)).fetchone()
            if existing and existing["size"] == stat.st_size and existing["mtime_ns"] == stat.st_mtime_ns:
                return "unchanged"
            text, kind, raw = self._read(root_id, rel)
        except FileIndexError as exc:
            self._drop_path(root_id, rel)
            return f"skip:{exc}"
        except (FilesystemAccessError, DocumentIngestionError, OSError, ValueError) as exc:
            self._drop_path(root_id, rel)
            return f"skip:{type(exc).__name__}"
        chunks = _chunk_lines(text)
        if not chunks:
            self._drop_path(root_id, rel)
            return "skip:no_text"
        self._drop_path(root_id, rel)
        fid = "file_" + uuid.uuid4().hex[:12]
        self.db.execute(
            "INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?)",
            (fid, sel["id"], root_id, rel, stat.st_size, stat.st_mtime_ns,
             hashlib.sha256(raw).hexdigest(), kind, _now()),
        )
        for ordinal, (first, last, body) in enumerate(chunks):
            cur = self.db.execute(
                "INSERT INTO chunks(file_id,ordinal,line_start,line_end,text,injection) VALUES(?,?,?,?,?,?)",
                (fid, ordinal, first, last, body, 1 if detect_injection(body, limit=1) else 0),
            )
            self.db.execute("INSERT INTO chunks_fts(rowid, text) VALUES(?,?)", (cur.lastrowid, body))
        return "indexed"

    # ------------------------------------------------------------------ deletion
    def _delete_file_rows(self, file_id: str) -> None:
        for row in self.db.execute("SELECT id, text FROM chunks WHERE file_id=?", (file_id,)).fetchall():
            self.db.execute("INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', ?, ?)",
                            (row["id"], row["text"]))
        self.db.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
        self.db.execute("DELETE FROM files WHERE id=?", (file_id,))

    def _drop_path(self, root_id: str, rel: str) -> None:
        row = self.db.execute("SELECT id FROM files WHERE root_id=? AND rel_path=?", (root_id, rel)).fetchone()
        if row:
            self._delete_file_rows(row["id"])

    def _compact(self) -> None:
        self.db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('optimize')")
        self.db.commit()
        self.db.execute("VACUUM")

    def delete_file(self, file_id: str) -> None:
        with self._lock:
            if self.db.execute("SELECT 1 FROM files WHERE id=?", (file_id,)).fetchone() is None:
                raise KeyError(file_id)
            self._delete_file_rows(file_id)
            self.db.commit()
            self._compact()

    def remove_selection(self, selection_id: str) -> None:
        with self._lock:
            if self.db.execute("SELECT 1 FROM selections WHERE id=?", (selection_id,)).fetchone() is None:
                raise KeyError(selection_id)
            for row in self.db.execute("SELECT id FROM files WHERE selection_id=?", (selection_id,)).fetchall():
                self._delete_file_rows(row["id"])
            self.db.execute("DELETE FROM skipped WHERE selection_id=?", (selection_id,))
            self.db.execute("DELETE FROM selections WHERE id=?", (selection_id,))
            self.db.commit()
            self._compact()

    def purge_all(self) -> None:
        with self._lock:
            for row in self.db.execute("SELECT id FROM selections").fetchall():
                for f in self.db.execute("SELECT id FROM files WHERE selection_id=?", (row["id"],)).fetchall():
                    self._delete_file_rows(f["id"])
            self.db.execute("DELETE FROM skipped")
            self.db.execute("DELETE FROM selections")
            self.db.commit()
            self._compact()

    # ------------------------------------------------------------------ search / cite
    def _staleness(self, row: sqlite3.Row) -> str:
        try:
            stat = (self.filesystem.roots[row["root_id"]] / row["rel_path"]).stat()
        except OSError:
            return "missing"
        if stat.st_size != row["size"] or stat.st_mtime_ns != row["mtime_ns"]:
            return "changed"
        return "current"

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        match = _fts_query(query)
        limit = max(1, min(limit, 50))
        rows = self.db.execute(
            """SELECT c.id chunk_id, c.ordinal, c.line_start, c.line_end, c.text, c.injection,
                      f.id file_id, f.root_id, f.rel_path, f.size, f.mtime_ns, f.location_kind, f.indexed_at,
                      snippet(chunks_fts, 0, '[', ']', '…', 24) snip
               FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid JOIN files f ON f.id = c.file_id
               WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?""",
            (match, limit),
        ).fetchall()
        return [
            {
                "chunk_id": r["chunk_id"], "file_id": r["file_id"], "root_id": r["root_id"],
                "path": r["rel_path"], "line_start": r["line_start"], "line_end": r["line_end"],
                "location_kind": r["location_kind"], "snippet": r["snip"], "indexed_at": r["indexed_at"],
                "status": self._staleness(r),
                "trust": "untrusted",
                "injection_flagged": bool(r["injection"]),
            }
            for r in rows
        ]

    def search_verified(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return only live-verified chunks with exact source coordinates for Brain synthesis."""
        with self._lock:
            hits = self.search(query, max(1, min(limit, 10)))
            grounded: list[dict[str, Any]] = []
            for hit in hits:
                row = self.db.execute(
                    "SELECT c.text, f.root_id, f.rel_path, f.sha256 "
                    "FROM chunks c JOIN files f ON f.id=c.file_id WHERE c.id=?",
                    (hit["chunk_id"],),
                ).fetchone()
                if row is None or hit["status"] != "current":
                    continue
                verification = self.verify(hit["chunk_id"])
                if not verification.get("verified") or not verification.get("file_unchanged"):
                    continue
                grounded.append({
                    "content": row["text"][:CHUNK_CHARS],
                    "citation": (
                        f"{row['rel_path']}:L{hit['line_start']}-L{hit['line_end']}"
                    ),
                    "source": {
                        "root_id": row["root_id"],
                        "path": row["rel_path"],
                        "line_start": hit["line_start"],
                        "line_end": hit["line_end"],
                        "sha256": row["sha256"],
                    },
                    "verified": True,
                    "trust": "untrusted",
                    "instructions_trusted": False,
                    "injection_flagged": hit["injection_flagged"],
                })
            return grounded

    def verify(self, chunk_id: int) -> dict[str, Any]:
        """Re-read the live file and prove (or disprove) that the cited text is still there."""
        row = self.db.execute(
            "SELECT c.text, c.line_start, c.line_end, f.* FROM chunks c JOIN files f ON f.id=c.file_id WHERE c.id=?",
            (chunk_id,),
        ).fetchone()
        if row is None:
            raise KeyError(chunk_id)
        base = {"chunk_id": chunk_id, "path": row["rel_path"], "line_start": row["line_start"],
                "line_end": row["line_end"]}
        try:
            text, _kind, raw = self._read(row["root_id"], row["rel_path"])
        except FileNotFoundError:
            return {**base, "verified": False, "reason": "file_missing"}
        except (FilesystemAccessError, FileIndexError, DocumentIngestionError, OSError, ValueError) as exc:
            return {**base, "verified": False, "reason": type(exc).__name__}
        lines = text.splitlines()
        live = "\n".join(lines[row["line_start"] - 1:row["line_end"]])
        same_file = hashlib.sha256(raw).hexdigest() == row["sha256"]
        return {**base, "verified": live == row["text"], "file_unchanged": same_file,
                "reason": "ok" if live == row["text"] else "text_changed_at_location"}

    def status(self) -> dict[str, Any]:
        one = lambda q: self.db.execute(q).fetchone()[0]  # noqa: E731
        return {"selections": one("SELECT COUNT(*) FROM selections"), "files": one("SELECT COUNT(*) FROM files"),
                "chunks": one("SELECT COUNT(*) FROM chunks"),
                "search": "lexical (keyword) only; results are untrusted file content with source locations"}

    def list_files(self, limit: int = 200) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(
            "SELECT id, selection_id, root_id, rel_path, size, location_kind, indexed_at FROM files "
            "ORDER BY rel_path LIMIT ?", (max(1, min(limit, 500)),))]


class FileIndexWatcher:
    """Owner-started, bounded refresh loop for explicitly selected folders."""

    def __init__(
        self,
        index: FileIndex,
        *,
        activity: Callable[[str], Any] | None = None,
    ) -> None:
        self.index = index
        self.activity = activity
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._interval_seconds = 60
        self._max_selections_per_cycle = 2
        self._cursor = 0
        self._last_run_at: str | None = None
        self._last_error: str | None = None
        self._cycles = 0
        self._selections_refreshed = 0

    def status(self) -> dict[str, Any]:
        with self._lock:
            active = self._thread is not None and self._thread.is_alive()
            return {
                "enabled": active,
                "state": "running" if active else "stopped",
                "interval_seconds": self._interval_seconds,
                "max_selections_per_cycle": self._max_selections_per_cycle,
                "cycles": self._cycles,
                "selections_refreshed": self._selections_refreshed,
                "last_run_at": self._last_run_at,
                "last_error": self._last_error,
            }

    def start(self, *, interval_seconds: int = 60, max_selections_per_cycle: int = 2) -> dict[str, Any]:
        interval_seconds = max(5, min(int(interval_seconds), 3600))
        max_selections_per_cycle = max(1, min(int(max_selections_per_cycle), 5))
        if not self.index.list_selections():
            raise FileIndexError("Select at least one folder before enabling refresh")
        if self.index.should_stop():
            raise AutomationStopped("Resume automation before enabling file refresh")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.status()
            self._interval_seconds = interval_seconds
            self._max_selections_per_cycle = max_selections_per_cycle
            self._last_error = None
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="mia-file-index-watcher", daemon=True
            )
            self._thread.start()
        return self.status()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
        return self.status()

    def _refresh_once(self) -> int:
        """Refresh one bounded, round-robin batch; exposed internally for deterministic tests."""
        selections = self.index.list_selections()
        if not selections:
            return 0
        start = self._cursor % len(selections)
        ordered = selections[start:] + selections[:start]
        batch = ordered[: self._max_selections_per_cycle]
        for selection in batch:
            if self._stop.is_set() or self.index.should_stop():
                raise AutomationStopped("File refresh stopped by owner")
            context = (
                self.activity("Refreshing an owner-selected folder")
                if self.activity is not None
                else _null_activity()
            )
            with context:
                self.index.reindex(selection["id"])
        self._cursor = (start + len(batch)) % max(1, len(selections))
        with self._lock:
            self._selections_refreshed += len(batch)
        return len(batch)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._refresh_once()
                with self._lock:
                    self._cycles += 1
                    self._last_run_at = _now()
                    self._last_error = None
            except AutomationStopped as exc:
                with self._lock:
                    self._last_error = str(exc)
                break
            except Exception as exc:
                with self._lock:
                    self._last_error = f"{type(exc).__name__}: {exc}"[:300]
            self._stop.wait(self._interval_seconds)
        with self._lock:
            self._thread = None


class FileIndexWatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interval_seconds: int = 60
    max_selections_per_cycle: int = 2


class _null_activity:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_args: Any) -> None:
        return None


def register_file_index_routes(app: Any, index: FileIndex, record: Callable[..., Any],
                               activity: Callable[[str], Any] | None = None) -> None:
    """Routes on the app itself (not include_router) so route enumeration stays flat."""
    from fastapi import HTTPException
    from fastapi.concurrency import run_in_threadpool

    watcher = FileIndexWatcher(index, activity=activity)
    app.state.file_index_watcher = watcher

    def unknown() -> HTTPException:
        return HTTPException(status_code=404, detail="Unknown index item")

    @app.get("/api/file-index/status")
    def fi_status() -> dict[str, Any]:
        return {**index.status(), "watcher": watcher.status()}

    @app.get("/api/file-index/watch")
    def fi_watch_status() -> dict[str, Any]:
        return watcher.status()

    @app.post("/api/file-index/watch/start")
    def fi_watch_start(item: FileIndexWatchInput) -> dict[str, Any]:
        try:
            result = watcher.start(
                interval_seconds=item.interval_seconds,
                max_selections_per_cycle=item.max_selections_per_cycle,
            )
        except FileIndexError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        record(event_type="file_index_watch_started", summary="Owner enabled bounded file refresh",
               outcome="success", payload=result)
        return result

    @app.post("/api/file-index/watch/stop")
    def fi_watch_stop() -> dict[str, Any]:
        result = watcher.stop()
        record(event_type="file_index_watch_stopped", summary="Owner stopped file refresh",
               outcome="success", payload=result)
        return result

    @app.get("/api/file-index/selections")
    def fi_selections() -> dict[str, Any]:
        return {"items": index.list_selections()}

    @app.post("/api/file-index/selections", status_code=201)
    def fi_add(item: SelectionInput) -> dict[str, Any]:
        try:
            sel = index.add_selection(item.root_id, item.path)
        except FileIndexError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        record(event_type="file_index_selected", summary="Owner selected a folder for indexing",
               outcome="success", payload={"root_id": sel["root_id"], "path": sel["rel_path"]})
        return sel

    @app.post("/api/file-index/selections/{selection_id}/reindex")
    async def fi_reindex(selection_id: str) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            if activity is None:
                return index.reindex(selection_id)
            with activity("Indexing an owner-selected folder"):
                return index.reindex(selection_id)
        try:
            result = await run_in_threadpool(work)
        except KeyError as exc:
            raise unknown() from exc
        except AutomationStopped as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        record(event_type="file_index_run", summary=f"Indexed {result['indexed']} file(s)",
               outcome="success", payload=result)
        return result

    @app.delete("/api/file-index/selections/{selection_id}")
    def fi_remove(selection_id: str) -> dict[str, Any]:
        try:
            index.remove_selection(selection_id)
        except KeyError as exc:
            raise unknown() from exc
        record(event_type="file_index_removed", summary="Owner removed a selection and its index",
               outcome="success", payload={"selection_id": selection_id})
        return {"removed": selection_id}

    @app.delete("/api/file-index/files/{file_id}")
    def fi_delete_file(file_id: str) -> dict[str, Any]:
        try:
            index.delete_file(file_id)
        except KeyError as exc:
            raise unknown() from exc
        return {"removed": file_id}

    @app.delete("/api/file-index")
    def fi_purge() -> dict[str, Any]:
        # Do not leave an owner-started refresh worker running after its selections
        # and indexed content have been permanently cleared.
        watcher.stop()
        index.purge_all()
        record(event_type="file_index_purged", summary="Owner deleted the whole file index",
               outcome="success", payload={})
        return {"purged": True, "watcher": watcher.status()}

    @app.get("/api/file-index/files")
    def fi_files(limit: int = 200) -> dict[str, Any]:
        return {"items": index.list_files(limit)}

    @app.get("/api/file-index/search")
    def fi_search(q: str, limit: int = 10) -> dict[str, Any]:
        try:
            return {"query": q, "items": index.search(q, limit)}
        except FileIndexError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/file-index/chunks/{chunk_id}/verify")
    def fi_verify(chunk_id: int) -> dict[str, Any]:
        try:
            return index.verify(chunk_id)
        except KeyError as exc:
            raise unknown() from exc
