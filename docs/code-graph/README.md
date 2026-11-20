# Mia Code Graph

Mia Code Graph is a deterministic, local codebase index. It reads repository files and
Python syntax trees; it never imports or executes indexed application modules. It writes
only `docs/code-graph/graph.json` and the changelog artifact.

## Run

```powershell
python scripts/update_code_graph.py
```

The production FastAPI app starts a lightweight background watcher that checks source-file content digests and atomically rebuilds the index after changes. Graph data APIs are loopback-only by default. Remote access requires `MIA_CODE_GRAPH_ALLOW_REMOTE=true` and a strong `MIA_CODE_GRAPH_TOKEN` (at least 24 characters); enter it in the page's password field. Forwarded/reverse-proxy requests also require the token. The UI keeps the token only in page memory and never saves it. The graph page also validates/rebuilds the source digest on access; the refresh button forces an update. Set `MIA_CODE_GRAPH_WATCH_ENABLED=false` to disable background watching. The interval is configurable with `MIA_CODE_GRAPH_WATCH_INTERVAL_SECONDS` (1–60 seconds).
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

- `IMPORTS`, `DECLARES`, same-file unique `CALLS`, FastAPI `EXPOSES_API`, and exact
  literal config-file `USES`/`READS_FROM`/`WRITES_TO` edges are emitted from Python AST evidence.
- `BELONGS_TO_LAYER` and `TESTED_BY` are heuristics and are explicitly labelled
  `inferred`.
- A missing edge is not proof that no runtime relationship exists. Reflection, dynamic
  imports, dependency injection and runtime-generated calls are not fully resolvable.
- JSON/YAML/TOML/HTML/JS/TS/SQL and workflow/config files appear as file nodes; they are not falsely presented as parsed symbols. Sensitive environment and credential files/directories are excluded.

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


## Change impact analysis

Use the graph to identify import dependents and likely tests affected by changes since a Git base:

```powershell
Invoke-RestMethod "http://127.0.0.1:8765/api/code-graph/impact?base=main"
```

The local API exposes `GET /api/code-graph/impact?base=main`. It reports changed paths, direct and transitive static import dependents, and tests connected through explicitly inferred `TESTED_BY` edges. Deleted or unsupported files are reported as unindexed rather than guessed. This is conservative static impact analysis: dynamic imports, dependency injection, runtime wiring and missing test-name matches can leave relationships unresolved.


## Automatic refresh during development

When the Mia server starts, its Code Graph watcher checks source-file content digests and rebuilds the full graph after changes. The interval is configurable with `MIA_CODE_GRAPH_WATCH_INTERVAL_SECONDS`; set `MIA_CODE_GRAPH_WATCH_ENABLED=false` to disable it. The watcher shuts down with the application.

Every GitHub Actions CI run also regenerates the graph from the checked-out commit and uploads the JSON snapshot as a short-lived `mia-code-graph-<commit>` artifact. This makes the graph available even when Mia is not running locally. The artifact is retained for seven days.

The watcher currently performs full graph rebuilds rather than incremental file-level parsing. The Git-diff impact endpoint is a conservative static-import analysis, not a complete runtime dependency map.
