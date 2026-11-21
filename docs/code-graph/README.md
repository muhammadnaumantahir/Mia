# Mia Code Graph

Mia Code Graph is a deterministic, local codebase index. It reads repository files and
Python syntax trees; it never imports or executes indexed application modules. It writes
only the generated graph snapshot and ignored runtime history at `data/code-graph/history.jsonl`.

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
generation time, parse errors and known parser limitations. Generated graph JSON is ignored by Git; append-only history is stored under the ignored `data/` directory so the watcher never dirties tracked documentation.


## Change impact analysis

Use the graph to identify import dependents and likely tests affected by changes since a Git base:

```powershell
Invoke-RestMethod "http://127.0.0.1:8765/api/code-graph/impact?base=main"
```

The local API exposes `GET /api/code-graph/impact?base=main`. It reports changed paths, direct and transitive static import dependents, and tests connected through explicitly inferred `TESTED_BY` edges. Deleted or unsupported files are reported as unindexed rather than guessed. This is conservative static impact analysis: dynamic imports, dependency injection, runtime wiring and missing test-name matches can leave relationships unresolved.

## Freshness and incremental indexing: source-backed gap report (MIA-BASE-01)

Findings below come from reading `src/mia/codegraph/indexer.py`, `watcher.py` and
`scripts/update_code_graph.py`, and from profiling `build_graph` on this repository
(262 indexed files, 207 Python files). Timings are from one Linux sandbox run and are
indicative only.

**What is incremental today (change *detection*):**
- `source_snapshot` hashes every indexed file (SHA-256) plus the Git `HEAD`, so edits,
  including same-size edits with preserved timestamps, are detected. The watcher skips the
  rebuild when the snapshot is unchanged.
- `diff_snapshots` (added in this task) names the added / modified / removed paths between two
  snapshots. The watcher keeps the latest result in `last_changes` and appends a bounded,
  path-only `changes` record plus `rebuild: "full"` and `rebuild_seconds` to the ignored
  `data/code-graph/history.jsonl`.

**What is not incremental (graph *construction*):**
- Any detected change triggers a full `build_graph`: every file is re-read and every Python
  file re-parsed. There is no per-file fact cache, so cost scales with repository size rather
  than with the size of the edit.
- Several graph products are global, not per-file, so a one-file edit can legitimately change
  edges elsewhere: `IMPORTS` resolution depends on the full module set (adding or removing a
  file can change other files' edges), `EXTENDS` and `TESTED_BY` depend on the global symbol
  table, and unreferenced-candidate and circular-import diagnostics are computed over the whole
  graph. A naive "re-parse only the changed file and splice its nodes in" would silently leave
  these stale.
- `scripts/update_code_graph.py` previously built the graph twice on every change (once to
  compare, once to write). It now builds once and persists via `write_graph`.

**Fixed in this task (behaviour-preserving):** `build_graph` walked each AST several times and,
for every function, scanned all graph nodes to find its node ID. It now walks each tree once and
looks functions up by `(path, line)`. Old and new indexers were compared on the same tree and
produced identical graphs (excluding `generated_at`); one run measured about 3.1 s before and
2.2 s after. This is a constant-factor improvement, not true incremental indexing.

**Incremental indexing (MIA-BASE-02, implemented):** `build_graph` is now a per-file extraction
pass plus a global resolution pass.
- `_extract_facts` produces JSON-serialisable facts from one file only (declarations, imports as
  written, routes, literal file accesses, per-function call sites). `FactCache` stores them keyed
  by the file's SHA-256 and is salted with a hash of `indexer.py`, so any extractor change
  invalidates it. It can be persisted at `data/code-graph/facts-cache.json` (git-ignored).
- The global pass (module map, symbol index, `IMPORTS`, `EXTENDS`, `CALLS`, `TESTED_BY`,
  diagnostics, cycles) always re-runs over all facts, so a cross-file edge can never go stale.
  Savings come from skipping read-decode-parse-walk for unchanged Python files, not from
  skipping the global pass.
- Tests assert that cached incremental output equals a clean full rebuild after add, modify,
  rename, remove, syntax-error and syntax-fix sequences; the new indexer was also compared with
  the previous one on this repository (identical graphs). On this repo, one sandbox run: previous
  indexer about 2.5 s, cold cache about 1.7 s, warm cache about 0.5 s.
- Watcher history now records `rebuild` (`full` or `incremental`), `files_reused` and
  `files_reparsed`. Known difference: if a file cannot be read at all, the old indexer could list
  two index errors for it; the new one lists one.

Non-Python files are still indexed only as file nodes; no language-specific symbol parsing exists
for them.

## Freshness, provenance and impact audit (MIA-BASE-04)

Audited on current `main` by reading `impact.py`, `indexer.py`, `watcher.py`,
`scripts/update_code_graph.py` and the `/api/code-graph*` routes.

**Fixed (tests: `tests/test_code_graph_impact_git.py`, real temporary git repositories):**
- Impact analysis only diffed `base...HEAD`, so uncommitted edits, staged files and untracked
  files were invisible even though the graph is built from the working tree. `analyze_impact`
  and `GET /api/code-graph/impact` now include them by default (`uncommitted=false` restores
  committed-only) and report `includes_uncommitted`. Ignored files are excluded.
- Renames previously reported only the new path; rename detection is now off so the old path
  appears too. Paths are read NUL-separated, so names with spaces or non-ASCII characters are
  no longer quoted by git and silently unmatched.
- The graph's `commit` is `HEAD`, which is misleading when the tree is dirty. Each graph now
  carries `tree: {dirty, dirty_paths}` (`null` when git is unavailable) and each CHANGELOG entry
  states whether the graph matched its commit. `dirty` is informational only and is not part of
  staleness comparison, so refreshing the CHANGELOG cannot make the graph look stale.
- The git-diff half of impact analysis had no tests before this task.

**Known limitations (not fixed here):**
- A renamed or deleted file's old path has no node in the current graph, so files that still
  import it are not listed as dependents (they appear as unresolved imports only).
- `TESTED_BY` is a conservative name convention marked `inferred`; it is not coverage.
- `GET /api/code-graph/status` reports `last_commit` but not whether the stored graph is stale;
  staleness is only checked by `scripts/update_code_graph.py --check` (source digest + HEAD).
- Refresh is local and manual (script, watcher or `POST /api/code-graph/refresh`); nothing
  refreshes the graph on commit. `graph.json` is git-ignored, so a checkout has no graph until
  one is generated. Verified on Linux only; Windows is OWNER_VERIFICATION_REQUIRED.

