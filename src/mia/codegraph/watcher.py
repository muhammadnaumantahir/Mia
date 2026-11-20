"""Background watcher that keeps Mia's local Code Graph synchronized."""
from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from .indexer import build_graph, source_snapshot


class CodeGraphWatcher:
    """Check source content digests and atomically rebuild after repository changes."""

    def __init__(
        self, root: str | Path, output: str | Path, *, interval_seconds: float = 5.0
    ) -> None:
        if not 1.0 <= float(interval_seconds) <= 60.0:
            raise ValueError("Code Graph watcher interval must be between 1 and 60 seconds")
        self.root = Path(root).resolve()
        self.output = Path(output).resolve()
        self.interval_seconds = float(interval_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot: tuple[tuple[str, str], ...] = ()
        self._lock = threading.Lock()
        self.last_error: str | None = None
        self.last_commit: str | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start one daemon watcher; create an initial graph if none exists."""
        with self._lock:
            if self.running:
                return
            self._stop.clear()
            self._snapshot = source_snapshot(self.root)
            self._thread = threading.Thread(
                target=self._run, name="mia-code-graph", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop the watcher cleanly during application shutdown."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(2.0, self.interval_seconds + 1.0))
            if not thread.is_alive():
                self._thread = None
        elif thread is None:
            self._thread = None

    def check_once(self, *, force: bool = False) -> bool:
        """Check for changes and rebuild once; exposed for deterministic tests."""
        snapshot = source_snapshot(self.root)
        if not force and snapshot == self._snapshot and self.output.is_file():
            return False
        try:
            previous: dict[str, object] = {}
            if self.output.is_file():
                try:
                    loaded = json.loads(self.output.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        previous = loaded
                except (OSError, ValueError):
                    previous = {}
            graph = build_graph(self.root, output=self.output)
            if (
                previous.get("source_digest") != graph["source_digest"]
                or previous.get("commit") != graph["commit"]
            ):
                self._append_history(graph)
            self._snapshot = snapshot
            self.last_commit = graph["commit"]
            self.last_error = None
            return True
        except Exception as exc:
            # The indexer uses atomic replacement, so the last good graph remains.
            self.last_error = type(exc).__name__
            return False

    def _append_history(self, graph: dict[str, object]) -> None:
        # Generated history belongs in ignored runtime data, never tracked docs.
        history = self.root / "data" / "code-graph" / "history.jsonl"
        history.parent.mkdir(parents=True, exist_ok=True)
        summary = graph.get("summary")
        if not isinstance(summary, dict):
            summary = {}
        entry = {
            "generated_at": datetime.now(UTC).isoformat(),
            "commit": graph.get("commit", "unknown"),
            "source_digest": graph.get("source_digest", "unknown"),
            "summary": {
                "nodes": summary.get("nodes", 0),
                "edges": summary.get("edges", 0),
                "parse_errors": summary.get("parse_errors", 0),
            },
        }
        with history.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(entry, sort_keys=True) + "\n")

    def _run(self) -> None:
        # Validate the existing artifact on startup as well as watching later edits.
        self.check_once(force=True)
        while not self._stop.wait(self.interval_seconds):
            self.check_once()
