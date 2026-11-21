"""Regenerate Mia's Code Graph from the current checkout."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mia.codegraph.indexer import FactCache, build_graph, write_graph  # noqa: E402

OUTPUT = ROOT / "docs" / "code-graph" / "graph.json"
HISTORY = ROOT / "docs" / "code-graph" / "CHANGELOG.md"


def _tree_line(graph: dict) -> str:
    tree = graph.get("tree") or {}
    if tree.get("dirty") is None:
        return "unknown (git state unavailable)"
    if tree["dirty"]:
        return (f"dirty ({tree['dirty_paths']} uncommitted paths); the commit above is the parent "
                "of content that is not committed yet")
    return "clean (graph matches the commit above)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the stored graph is stale")
    args = parser.parse_args()
    cache = FactCache()
    cache_path = ROOT / "data" / "code-graph" / "facts-cache.json"
    cache.load(cache_path)
    graph = build_graph(ROOT, cache=cache)
    try:
        cache.save(cache_path)
    except OSError:
        pass
    previous = None
    if OUTPUT.is_file():
        try:
            previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
    current_digest = graph["source_digest"]
    if args.check:
        if (not previous or previous.get("source_digest") != current_digest
                or previous.get("commit") != graph["commit"]):
            print("Code Graph is missing or stale; run python scripts/update_code_graph.py")
            return 1
        print(f"Code Graph current: {graph['summary']['nodes']} nodes, {graph['summary']['edges']} edges")
        return 0
    if (previous and previous.get("source_digest") == current_digest
            and previous.get("commit") == graph["commit"]):
        print(f"Code Graph unchanged at {graph['commit']}")
        return 0
    write_graph(graph, OUTPUT)
    stamp = datetime.now(UTC).isoformat()
    entry = (f"\n## {stamp}\n\n- Commit: `{graph['commit']}`\n"
             f"- Working tree: {_tree_line(graph)}\n"
             f"- Source digest: `{current_digest}`\n"
             f"- Nodes: {graph['summary']['nodes']}\n"
             f"- Edges: {graph['summary']['edges']}\n"
             f"- Parse errors: {graph['summary']['parse_errors']}\n")
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(entry)
    print(f"Updated Code Graph: {graph['summary']['nodes']} nodes, {graph['summary']['edges']} edges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
