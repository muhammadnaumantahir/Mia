"""Git-diff impact analysis over the source-evidence Code Graph."""

from __future__ import annotations

import io
import subprocess
import tarfile
import tempfile
from collections import deque
from pathlib import Path
from typing import Any


def _git_paths(root: Path, args: list[str], failure: str) -> list[str]:
    """Run a git command that prints NUL-separated paths; fail closed on any error."""
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, timeout=20, check=False)
    if result.returncode != 0:
        raise ValueError(failure)
    paths = []
    for raw in result.stdout.decode("utf-8", errors="replace").split("\0"):
        candidate = raw.replace("\\", "/").strip()
        if not candidate or candidate.startswith("/") or ".." in Path(candidate).parts:
            continue
        paths.append(candidate)
    return paths


def changed_paths_since(
    root: str | Path, base_ref: str, *, include_working_tree: bool = False
) -> list[str]:
    """Return changed repository-relative paths between a base ref and HEAD.

    With ``include_working_tree`` the result also contains staged and unstaged edits to
    tracked files and untracked, non-ignored files, so impact can be checked before a
    commit. Rename detection is off, so a renamed file reports both its old and new path.
    """
    root = Path(root).resolve()
    ref = base_ref.strip()
    if (
        not ref
        or len(ref) > 200
        or ref.startswith("-")
        or any(character in ref for character in ("\x00", "\n", "\r"))
    ):
        raise ValueError("base must be a valid Git commit or branch reference")
    verified = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if verified.returncode != 0:
        raise ValueError("base Git reference was not found")
    paths = set(_git_paths(
        root,
        ["diff", "-z", "--name-only", "--no-renames", "--diff-filter=ACMRD", f"{ref}...HEAD", "--"],
        "could not calculate Git changes from the requested base",
    ))
    if include_working_tree:
        paths.update(_git_paths(
            root, ["diff", "-z", "--name-only", "--no-renames", "HEAD", "--"],
            "could not calculate uncommitted Git changes"))
        paths.update(_git_paths(
            root, ["ls-files", "-z", "--others", "--exclude-standard"],
            "could not list untracked files"))
    return sorted(paths)


class _GraphIndex:
    """Lookup tables for impact queries over one graph."""

    def __init__(self, graph: dict[str, Any]) -> None:
        self.nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        self.files_by_path = {
            node.get("path"): node
            for node in self.nodes
            if node.get("kind") == "file" and isinstance(node.get("path"), str)
        }
        self.nodes_by_id = {node.get("id"): node for node in self.nodes}
        self.reverse_imports: dict[str, set[str]] = {}
        self.tested_by: dict[str, set[str]] = {}
        for edge in edges:
            relation = edge.get("type")
            if relation == "IMPORTS" and edge.get("confidence") == "confirmed":
                self.reverse_imports.setdefault(edge.get("target"), set()).add(edge.get("source"))
            elif relation == "TESTED_BY":
                self.tested_by.setdefault(edge.get("source"), set()).add(edge.get("target"))

    def impact_of(self, path: str) -> tuple[list[str], list[str], list[str]] | None:
        """(direct dependents, transitive dependents, related tests) or None if not indexed."""
        changed = self.files_by_path.get(path)
        if changed is None:
            return None
        changed_id = changed["id"]
        queue = deque([(changed_id, 0)])
        depths = {changed_id: 0}
        while queue:
            current, depth = queue.popleft()
            for dependent in sorted(self.reverse_imports.get(current, set())):
                if dependent not in depths:
                    depths[dependent] = depth + 1
                    queue.append((dependent, depth + 1))
        direct = sorted(
            self.nodes_by_id[item].get("path", "")
            for item, depth in depths.items()
            if depth == 1 and item in self.nodes_by_id
        )
        transitive = sorted(
            self.nodes_by_id[item].get("path", "")
            for item, depth in depths.items()
            if depth > 1 and item in self.nodes_by_id
        )
        source_paths = {
            self.nodes_by_id[file_id].get("path")
            for file_id in {changed_id, *depths.keys()}
            if file_id in self.nodes_by_id
        }
        source_symbols = {
            node["id"] for node in self.nodes
            if node.get("path") in source_paths and node.get("kind") in {"function", "class"}
        }
        tests = sorted({
            self.nodes_by_id[test_id].get("path", "")
            for symbol_id in source_symbols
            for test_id in self.tested_by.get(symbol_id, set())
            if test_id in self.nodes_by_id
        })
        return direct, transitive, tests


def analyze_graph_impact(
    graph: dict[str, Any],
    changed_paths: list[str],
    *,
    base_graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Find direct/transitive import dependents and source-linked tests.

    ``base_graph`` (the graph of the base commit) lets a path that no longer exists, such as a
    deleted or renamed file, still report the files that imported it, limited to files that
    still exist in ``graph``.
    """
    current = _GraphIndex(graph)
    base = _GraphIndex(base_graph) if base_graph is not None else None
    results = []
    all_dependents: set[str] = set()
    all_tests: set[str] = set()
    for changed_path in sorted(set(changed_paths)):
        found = current.impact_of(changed_path)
        if found is not None:
            direct, transitive, tests = found
            all_dependents.update(direct)
            all_dependents.update(transitive)
            all_tests.update(tests)
            results.append({
                "path": changed_path, "indexed": True, "direct_dependents": direct,
                "transitive_dependents": transitive, "related_tests": tests,
            })
            continue
        item: dict[str, Any] = {
            "path": changed_path, "indexed": False, "direct_dependents": [],
            "transitive_dependents": [], "related_tests": [],
        }
        previous = base.impact_of(changed_path) if base is not None else None
        if previous is not None:
            alive = set(current.files_by_path)
            direct = [p for p in previous[0] if p in alive]
            transitive = [p for p in previous[1] if p in alive]
            tests = [t for t in previous[2] if t in alive]
            item.update(removed=True, direct_dependents=direct,
                        transitive_dependents=transitive, related_tests=tests)
            all_dependents.update(direct)
            all_dependents.update(transitive)
            all_tests.update(tests)
        results.append(item)
    return {
        "base_ref": None,
        "changed_files": len(results),
        "indexed_changed_files": sum(1 for item in results if item["indexed"]),
        "dependent_files": sorted(all_dependents),
        "related_tests": sorted(all_tests),
        "items": results,
        "limitations": [
            "Impact is based on confirmed static imports and explicitly inferred TESTED_BY links.",
            "Dynamic imports, dependency injection, runtime routing and generated calls may be missing.",
            "A suggested test is not proof of complete test coverage.",
        ],
    }


_MAX_ARCHIVE_BYTES = 200 * 1024 * 1024


def _graph_at_commit(root: Path, commit: str) -> dict[str, Any] | None:
    """Build the Code Graph of ``commit`` from a safe, temporary extraction of its tree."""
    from .indexer import build_graph

    try:
        archived = subprocess.run(
            ["git", "archive", "--format=tar", commit], cwd=root, capture_output=True,
            timeout=60, check=False)
        if archived.returncode != 0 or len(archived.stdout) > _MAX_ARCHIVE_BYTES:
            return None
        with tempfile.TemporaryDirectory(prefix="mia-graph-base-") as scratch:
            with tarfile.open(fileobj=io.BytesIO(archived.stdout)) as archive:
                archive.extractall(scratch, filter="data")  # rejects traversal, links, devices
            return build_graph(scratch)
    except (OSError, subprocess.SubprocessError, tarfile.TarError, ValueError):
        return None


def analyze_impact(
    root: str | Path,
    base_ref: str = "HEAD~1",
    *,
    graph: dict[str, Any] | None = None,
    include_working_tree: bool = True,
) -> dict[str, Any]:
    """Calculate changed files and their graph impact relative to a Git base ref.

    Uncommitted changes are included by default because the graph is built from the working
    tree; pass ``include_working_tree=False`` for committed changes only.
    """
    from .indexer import build_graph

    root = Path(root).resolve()
    changed = changed_paths_since(root, base_ref, include_working_tree=include_working_tree)
    current_graph = graph or build_graph(root)
    present = {n.get("path") for n in current_graph.get("nodes", []) if n.get("kind") == "file"}
    missing = [path for path in changed if path not in present]
    base_graph = None
    base_commit = None
    if missing:
        # Only now is history needed: a missing path may be a deleted or renamed file whose
        # remaining importers exist only in the base commit's graph.
        merge_base = subprocess.run(
            ["git", "merge-base", base_ref.strip(), "HEAD"], cwd=root, capture_output=True,
            text=True, timeout=10, check=False)
        base_commit = merge_base.stdout.strip() if merge_base.returncode == 0 else None
        if base_commit:
            base_graph = _graph_at_commit(root, base_commit)
    result = analyze_graph_impact(current_graph, changed, base_graph=base_graph)
    result["base_ref"] = base_ref
    result["includes_uncommitted"] = include_working_tree
    if missing:
        result["base_graph_commit"] = base_commit if base_graph is not None else None
        if base_graph is None:
            result["limitations"].append(
                "Some changed paths are not in the current graph and the base commit's graph "
                "could not be built, so dependents of deleted or renamed files are not listed.")
    return result
