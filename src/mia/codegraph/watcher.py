"""Background watcher that keeps Mia's local Code Graph synchronized."""
from __future__ import annotations

import threading
from pathlib import Path

from .indexer import build_graph, source_snapshot


class CodeGraphWatcher:
    """Poll source metadata and atomically rebuild the graph after repository changes."""

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
        self._snapshot: tuple[tuple[str, int, int], ...] = ()
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
        self._thread = None

    def check_once(self, *, force: bool = False) -> bool:
        """Check for changes and rebuild once; exposed for deterministic tests."""
        snapshot = source_snapshot(self.root)
        if not force and snapshot == self._snapshot and self.output.is_file():
            return False
        try:
            graph = build_graph(self.root, output=self.output)
            self._snapshot = snapshot
            self.last_commit = graph["commit"]
            self.last_error = None
            return True
        except Exception as exc:
            # The indexer uses atomic replacement, so the last good graph remains.
            self.last_error = type(exc).__name__
            return False

    def _run(self) -> None:
        if not self.output.is_file():
            self.check_once(force=True)
        while not self._stop.wait(self.interval_seconds):
            self.check_once()
