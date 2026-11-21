"""Git-diff impact analysis over the source-evidence Code Graph."""

from __future__ import annotations

import subprocess
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


def analyze_graph_impact(graph: dict[str, Any], changed_paths: list[str]) -> dict[str, Any]:
    """Find direct/transitive import dependents and source-linked tests."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    files_by_path = {
        node.get("path"): node
        for node in nodes
        if node.get("kind") == "file" and isinstance(node.get("path"), str)
    }
    nodes_by_id = {node.get("id"): node for node in nodes}
    reverse_imports: dict[str, set[str]] = {}
    tested_by: dict[str, set[str]] = {}
    for edge in edges:
        relation = edge.get("type")
        if relation == "IMPORTS" and edge.get("confidence") == "confirmed":
            reverse_imports.setdefault(edge.get("target"), set()).add(edge.get("source"))
        elif relation == "TESTED_BY":
            tested_by.setdefault(edge.get("source"), set()).add(edge.get("target"))

    results = []
    all_dependents: set[str] = set()
    all_tests: set[str] = set()
    for changed_path in sorted(set(changed_paths)):
        changed = files_by_path.get(changed_path)
        if changed is None:
            results.append({
                "path": changed_path,
                "indexed": False,
                "direct_dependents": [],
                "transitive_dependents": [],
                "related_tests": [],
            })
            continue
        changed_id = changed["id"]
        queue = deque([(changed_id, 0)])
        depths = {changed_id: 0}
        while queue:
            current, depth = queue.popleft()
            for dependent in sorted(reverse_imports.get(current, set())):
                if dependent not in depths:
                    depths[dependent] = depth + 1
                    queue.append((dependent, depth + 1))
        direct = sorted(
            nodes_by_id[item].get("path", "")
            for item, depth in depths.items()
            if depth == 1 and item in nodes_by_id
        )
        transitive = sorted(
            nodes_by_id[item].get("path", "")
            for item, depth in depths.items()
            if depth > 1 and item in nodes_by_id
        )
        source_files = {changed_id, *depths.keys()}
        source_symbols = {
            node["id"]
            for node in nodes
            if node.get("path") in {
                nodes_by_id[file_id].get("path")
                for file_id in source_files
                if file_id in nodes_by_id
            }
            and node.get("kind") in {"function", "class"}
        }
        test_paths = {
            nodes_by_id[test_id].get("path", "")
            for symbol_id in source_symbols
            for test_id in tested_by.get(symbol_id, set())
            if test_id in nodes_by_id
        }
        all_dependents.update(direct)
        all_dependents.update(transitive)
        all_tests.update(test_paths)
        results.append({
            "path": changed_path,
            "indexed": True,
            "direct_dependents": direct,
            "transitive_dependents": transitive,
            "related_tests": sorted(test_paths),
        })
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
    result = analyze_graph_impact(graph or build_graph(root), changed)
    result["base_ref"] = base_ref
    result["includes_uncommitted"] = include_working_tree
    return result
