# Mia Code Graph

Mia Code Graph is a deterministic, local codebase index. It reads repository files and
Python syntax trees; it never imports or executes indexed application modules. It writes
only `docs/code-graph/graph.json` and the changelog artifact.

## Run

```powershell
python scripts/update_code_graph.py
```

The production FastAPI app starts a lightweight background watcher that polls source-file metadata and atomically rebuilds the index after changes. The graph page also validates/rebuilds the source digest on access; the refresh button forces an update. Set `MIA_CODE_GRAPH_WATCH_ENABLED=false` to disable background watching. The interval is configurable with `MIA_CODE_GRAPH_WATCH_INTERVAL_SECONDS` (1–60 seconds).
The API compares a SHA-256 digest of supported source/document files and rewrites the
graph atomically only when the digest changes. The command can also be used after a
source change or commit:

```powershell
python scripts/update_code_graph.py --check
```

`--check` exits non-zero when the stored graph is missing or stale. The graph is local
repository metadata; do not commit private documents, environment files, secrets, model
weights or runtime databases into it.

## Evidence model

- `IMPORTS`, `DECLARES`, `CALLS`, and FastAPI `EXPOSES_API` edges are emitted from
  Python AST evidence when statically resolvable.
- `BELONGS_TO_LAYER` and `TESTED_BY` are heuristics and are explicitly labelled
  `inferred`.
- A missing edge is not proof that no runtime relationship exists. Reflection, dynamic
  imports, dependency injection and runtime-generated calls are not fully resolvable.
- Unsupported non-Python files appear as file nodes; they are not falsely presented as
  parsed symbols.

## Interface

Open `http://127.0.0.1:8765/code-graph`. Search nodes, filter by node type or edge type,
select a node, and inspect its location, relationships and evidence. The interface uses
the same local-only API boundary as the rest of Mia. It does not expose file contents.

## Stability and recovery

Node IDs are stable hashes of their kind and logical key. The index is sorted
deterministically. Writes use a temporary file and atomic replacement. If indexing fails,
the previous graph is preserved and the API reports an error rather than returning a
fabricated empty graph. Every graph includes the source digest, current Git commit,
generation time, parse errors and known parser limitations.
