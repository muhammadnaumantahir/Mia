# Personal knowledge graph (MIA-LIFE-10)

**Status:** implementation integrated on `main`; regression tests authored, execution deferred for owner verification.

## Purpose and storage boundary

The graph connects three existing owner-data node types without copying their content:

- `record`: structured memory records, including people/entities, topics, facts, decisions, goals and research-backed findings.
- `task`: owner personal tasks from the existing task store. Private tasks are not graph-visible.
- `project`: registered project workspaces.

Graph edges live in the version-9 additive memory migration. Existing `memory_relationships` remain unchanged and continue serving the earlier record-to-record memory-retrieval behavior. Graph nodes continue to use the source stores as their authority; deleting a source record/task removes edges that would otherwise point to a missing node. Deleting a project removes edges that use it as an endpoint. Renaming a project migrates project-node IDs and edge scope in the same SQLite transaction.

## Edge contract

Each edge contains typed endpoints, a relation, provenance, confidence, review state, visibility, optional note, optional evidence references, project scope and timestamps. The supported relation vocabulary includes related-to, part-of, works-on, involves, decided-in, depends-on, blocks, caused-by, follows, owned-by, evidences and references.

Provenance values are `owner_provided`, `observed`, `source_backed`, `inferred`, `hypothesis` and `validated`. Confidence is a finite number from 0 through 1. Source-backed, inferred, hypothesis and validated edges require at least one evidence pointer. An owner-provided edge is immediately `confirmed`; other provenance starts in `inferred` state.

Only an inferred edge can be confirmed or rejected. A material correction to a derived edge resets it to inferred and clears its review timestamp. Corrections and deletion preserve snapshots in the edge-history table. A rejected edge is not traversed as an active connection.

Evidence pointers are structured references (for example a URL, message ID, file reference or research claim). The graph stores these references; it does not fetch them or claim that the graph layer itself has verified the source. The caller must preserve the correct provenance. Confidence is an explicit owner/agent-supplied estimate, not a calibrated probability.

## Project isolation and privacy

An edge may connect only nodes whose non-empty project scopes agree. A registered project may also explicitly attach an unscoped node to that project. An edge cannot bridge two different project scopes. A project-filtered graph query returns only edges scoped to that project; unknown project filters return no node choices. Global graph view is an owner view across all workspaces.

Private personal tasks and temporary, retired or non-retrievable structured records are not exposed as graph nodes. Private edges are excluded unless the owner explicitly requests them. If a node is later moved to a different project or becomes private, the edge is omitted from graph results when its endpoints no longer satisfy the scope/visibility contract.

## API and interface

- `GET /api/memory/graph/nodes` — available record/task/project nodes, optionally project- and query-filtered.
- `GET /api/memory/graph` — nodes and edges with inferred/rejected/private filters.
- `POST /api/memory/graph/edges` — create a typed edge.
- `GET /api/memory/graph/edges/{edge_id}` — inspect an edge and its correction/review history.
- `PATCH /api/memory/graph/edges/{edge_id}` — correct relation, provenance, confidence, evidence, note or visibility.
- `POST /api/memory/graph/edges/{edge_id}/review` — confirm or reject an inferred edge.
- `DELETE /api/memory/graph/edges/{edge_id}` — delete an edge while retaining its history snapshot.
- `/knowledge-graph` — owner-facing connection explorer and editor.

## Current limits

The first implementation is owner-directed and manual-first. It does not autonomously extract entities, infer new relationships from all conversation history, or claim that a source pointer is verified merely because it was attached. Evidence and confidence are visible for owner review, and unconfirmed derived edges remain clearly separate from confirmed links. The UI is a structured graph explorer rather than an interactive force-directed canvas. Those are future enhancements, not implied current behavior.

Tests in `tests/test_knowledge_graph.py` cover mixed node types, edge provenance/confidence, required evidence, review and correction history, project isolation, private-task refusal, deletion cascades, project rename/delete behavior, API lifecycle, page serving and additive upgrade from schema version 8.
