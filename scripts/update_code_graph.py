"""Regenerate Mia's Code Graph from the current checkout."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mia.codegraph.indexer import build_graph  # noqa: E402

OUTPUT = ROOT / "docs" / "code-graph" / "graph.json"
HISTORY = ROOT / "docs" / "code-graph" / "CHANGELOG.md"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the stored graph is stale")
    args = parser.parse_args()
    graph = build_graph(ROOT)
    previous = None
    if OUTPUT.is_file():
        try:
            previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
    current_digest = graph["source_digest"]
    if args.check:
        if not previous or previous.get("source_digest") != current_digest:
            print("Code Graph is missing or stale; run python scripts/update_code_graph.py")
            return 1
        print(f"Code Graph current: {graph['summary']['nodes']} nodes, {graph['summary']['edges']} edges")
        return 0
    if previous and previous.get("source_digest") == current_digest:
        print(f"Code Graph unchanged at {graph['commit']}")
        return 0
    build_graph(ROOT, output=OUTPUT)
    stamp = datetime.now(UTC).isoformat()
    entry = (f"\n## {stamp}\n\n- Commit: `{graph['commit']}`\n"
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
