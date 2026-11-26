"""Personal knowledge graph across memory records, tasks and project workspaces.

Nodes remain owned by their existing stores. This module stores only typed edges and review
metadata, so renaming or deleting a source node does not duplicate its underlying content.
Evidence-backed/inferred edges are proposals until explicitly confirmed by the owner.
"""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any

from mia.memory.schema import AuditLog
from mia.memory.safety import reject_secret

NODE_TYPES = frozenset({"record", "task", "project"})
RELATIONS = frozenset({
    "related_to", "part_of", "works_on", "involves", "decided_in",
    "depends_on", "blocks", "caused_by", "follows", "owned_by",
    "evidences", "references",
})
PROVENANCE = frozenset({
    "owner_provided", "observed", "source_backed", "inferred", "hypothesis", "validated",
})
EDGE_STATES = frozenset({"confirmed", "inferred", "rejected"})
VISIBILITIES = frozenset({"normal", "private"})
EVIDENCE_REQUIRED = frozenset({"source_backed", "inferred", "hypothesis", "validated"})
MAX_EVIDENCE = 20
MAX_NOTE = 500


class KnowledgeGraphError(ValueError):
    """Invalid node, edge, provenance, scope or lifecycle transition."""


class GraphNodeNotFoundError(KeyError):
    """A referenced record, task or project does not exist or is not graph-visible."""


class GraphEdgeNotFoundError(KeyError):
    """No knowledge-graph edge has the supplied ID."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise KnowledgeGraphError("confidence must be a finite number from 0 to 1")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise KnowledgeGraphError("confidence must be a finite number from 0 to 1")
    return result


def _evidence(items: list[dict[str, Any]] | None, provenance: str) -> list[dict[str, str]]:
    if items is None:
        items = []
    if not isinstance(items, list) or len(items) > MAX_EVIDENCE:
        raise KnowledgeGraphError(f"evidence must contain 0-{MAX_EVIDENCE} references")
    cleaned: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {"type", "ref"}:
            raise KnowledgeGraphError("each evidence reference must contain only type and ref")
        kind, ref = item.get("type"), item.get("ref")
        if (
            not isinstance(kind, str) or not 1 <= len(kind.strip()) <= 20
            or not isinstance(ref, str) or not 1 <= len(ref.strip()) <= 500
        ):
            raise KnowledgeGraphError("evidence type and ref must be non-empty and bounded")
        kind, ref = kind.strip(), ref.strip()
        reject_secret(kind, field="evidence type")
        reject_secret(ref, field="evidence reference")
        identity = (kind, ref)
        if identity not in seen:
            seen.add(identity)
            cleaned.append({"type": kind, "ref": ref})
    if provenance in EVIDENCE_REQUIRED and not cleaned:
        raise KnowledgeGraphError(f"{provenance} edges require at least one evidence reference")
    return cleaned


class KnowledgeGraph:
    """Typed, project-scoped links over the existing record, task and project stores."""

    def __init__(
        self, connect: Callable[[], AbstractContextManager[sqlite3.Connection]]
    ) -> None:
        self._connect = connect

    @staticmethod
    def _project_key(name: str | None) -> str | None:
        from mia.memory.store import project_key

        return project_key(name)

    def _node(self, db: sqlite3.Connection, node_type: str, node_id: str) -> dict[str, Any]:
        if node_type not in NODE_TYPES:
            raise KnowledgeGraphError("node_type must be record, task or project")
        if not isinstance(node_id, str) or not node_id.strip() or len(node_id) > 160:
            raise KnowledgeGraphError("node_id must be a non-empty bounded string")

        if node_type == "record":
            row = db.execute(
                "SELECT record_id, record_type, title, status, provenance, confidence, "
                "project, temporary FROM structured_memory WHERE record_id = ?",
                (node_id,),
            ).fetchone()
            if row is None or row["temporary"] or row["status"] not in {"active", "completed"}:
                raise GraphNodeNotFoundError(node_id)
            return {
                "node_type": "record", "node_id": row["record_id"],
                "kind": row["record_type"], "title": row["title"],
                "status": row["status"], "provenance": row["provenance"],
                "confidence": row["confidence"], "project": row["project"],
                "project_key": self._project_key(row["project"]),
            }

        if node_type == "task":
            row = db.execute(
                "SELECT item_id, kind, title, status, provenance, project, private "
                "FROM personal_items WHERE item_id = ? AND kind = 'task'",
                (node_id,),
            ).fetchone()
            if row is None or row["private"]:
                raise GraphNodeNotFoundError(node_id)
            return {
                "node_type": "task", "node_id": row["item_id"],
                "kind": row["kind"], "title": row["title"],
                "status": row["status"], "provenance": row["provenance"],
                "confidence": None, "project": row["project"],
                "project_key": self._project_key(row["project"]),
            }

        row = db.execute(
            "SELECT project_key, name, goal, status FROM projects WHERE project_key = ?",
            (node_id.casefold(),),
        ).fetchone()
        if row is None:
            raise GraphNodeNotFoundError(node_id)
        return {
            "node_type": "project", "node_id": row["project_key"],
            "kind": "project", "title": row["name"], "status": row["status"],
            "provenance": "owner_provided", "confidence": 1.0,
            "project": row["name"], "project_key": row["project_key"],
            "goal": row["goal"],
        }

    def nodes(self, *, project: str | None = None, query: str | None = None,
              limit: int = 200) -> list[dict[str, Any]]:
        scope = self._project_key(project)
        cap = max(1, min(int(limit), 500))
        needle = (query or "").strip().casefold()
        result: list[dict[str, Any]] = []
        with self._connect() as db:
            records = db.execute(
                "SELECT record_id, record_type, title, status, provenance, confidence, project "
                "FROM structured_memory WHERE temporary = 0 AND status IN ('active','completed') "
                "ORDER BY updated_at DESC LIMIT 1000"
            ).fetchall()
            tasks = db.execute(
                "SELECT item_id, kind, title, status, provenance, project "
                "FROM personal_items WHERE kind = 'task' AND private = 0 "
                "ORDER BY updated_at DESC LIMIT 1000"
            ).fetchall()
            projects = db.execute(
                "SELECT project_key, name, goal, status FROM projects ORDER BY name COLLATE NOCASE"
            ).fetchall()

        def include(node: dict[str, Any]) -> None:
            if scope is not None:
                if node["node_type"] == "project" and node["project_key"] != scope:
                    return
                if node["node_type"] != "project" and node["project_key"] not in {None, scope}:
                    return
            if needle and needle not in (
                str(node.get("title", "")) + " " + str(node.get("kind", "")) +
                " " + str(node.get("project", ""))
            ).casefold():
                return
            result.append(node)

        for row in records:
            include({
                "node_type": "record", "node_id": row["record_id"],
                "kind": row["record_type"], "title": row["title"],
                "status": row["status"], "provenance": row["provenance"],
                "confidence": row["confidence"], "project": row["project"],
                "project_key": self._project_key(row["project"]),
            })
        for row in tasks:
            include({
                "node_type": "task", "node_id": row["item_id"], "kind": row["kind"],
                "title": row["title"], "status": row["status"],
                "provenance": row["provenance"], "confidence": None,
                "project": row["project"], "project_key": self._project_key(row["project"]),
            })
        for row in projects:
            include({
                "node_type": "project", "node_id": row["project_key"], "kind": "project",
                "title": row["name"], "status": row["status"],
                "provenance": "owner_provided", "confidence": 1.0,
                "project": row["name"], "project_key": row["project_key"],
                "goal": row["goal"],
            })
        return result[:cap]

    def _snapshot(self, db: sqlite3.Connection, row: sqlite3.Row,
                  *, actor: str, change_type: str) -> None:
        db.execute(
            "INSERT INTO knowledge_graph_edge_history "
            "(edge_id, changed_at, actor, change_type, snapshot_json) VALUES (?,?,?,?,?)",
            (row["edge_id"], _now(), actor, change_type,
             json.dumps(dict(row), ensure_ascii=False, sort_keys=True)),
        )

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        return {
            **dict(row),
            "evidence": json.loads(row["evidence_json"] or "[]"),
        }

    def add(
        self, *, from_type: str, from_id: str, to_type: str, to_id: str,
        relation: str, provenance: str = "owner_provided",
        confidence: float | None = None, evidence: list[dict[str, Any]] | None = None,
        note: str = "", visibility: str = "normal", project: str | None = None,
        actor: str = "owner",
    ) -> tuple[str, bool]:
        if from_type not in NODE_TYPES or to_type not in NODE_TYPES:
            raise KnowledgeGraphError("nodes must be records, tasks or projects")
        if relation not in RELATIONS:
            raise KnowledgeGraphError("relation must be one of " + ", ".join(sorted(RELATIONS)))
        if provenance not in PROVENANCE:
            raise KnowledgeGraphError("unsupported edge provenance")
        if visibility not in VISIBILITIES:
            raise KnowledgeGraphError("visibility must be normal or private")
        if from_type == to_type and from_id == to_id:
            raise KnowledgeGraphError("a node cannot be linked to itself")
        note = (note or "").strip()
        if len(note) > MAX_NOTE:
            raise KnowledgeGraphError(f"note is limited to {MAX_NOTE} characters")
        if note:
            reject_secret(note, field="note")
        confidence_value = _confidence(
            confidence if confidence is not None else (1.0 if provenance == "owner_provided" else 0.5)
        )
        evidence_value = _evidence(evidence, provenance)
        state = "confirmed" if provenance == "owner_provided" else "inferred"

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            source = self._node(db, from_type, from_id)
            target = self._node(db, to_type, to_id)
            scopes = {value for value in (source["project_key"], target["project_key"]) if value}
            if len(scopes) > 1:
                raise KnowledgeGraphError("an edge cannot connect nodes from different projects")
            explicit_scope = self._project_key(project)
            if project is not None and explicit_scope is None:
                raise KnowledgeGraphError("project scope is invalid")
            if explicit_scope and not db.execute(
                "SELECT 1 FROM projects WHERE project_key = ?", (explicit_scope,)
            ).fetchone():
                raise KnowledgeGraphError("project scope must name a registered project")
            if explicit_scope and scopes and explicit_scope not in scopes:
                raise KnowledgeGraphError("edge project does not match its endpoint scope")
            scope = explicit_scope or (next(iter(scopes)) if scopes else None)
            existing = db.execute(
                "SELECT edge_id FROM knowledge_graph_edges WHERE "
                "from_type=? AND from_id=? AND to_type=? AND to_id=? AND relation=?",
                (from_type, from_id, to_type, to_id, relation),
            ).fetchone()
            if existing:
                return str(existing["edge_id"]), False
            edge_id = uuid.uuid4().hex
            now = _now()
            db.execute(
                "INSERT INTO knowledge_graph_edges "
                "(edge_id,from_type,from_id,to_type,to_id,relation,provenance,confidence,"
                "state,visibility,evidence_json,project_key,note,actor,created_at,updated_at,reviewed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (edge_id, from_type, from_id, to_type, to_id, relation, provenance,
                 confidence_value, state, visibility, json.dumps(evidence_value, ensure_ascii=False),
                 scope, note, actor, now, now),
            )
            AuditLog.write(
                db, actor=actor, action="knowledge_edge_created",
                target_type="knowledge_graph_edge", target_id=edge_id,
                detail={
                    "from_type": from_type, "to_type": to_type, "relation": relation,
                    "provenance": provenance, "state": state, "project": scope,
                },
            )
        return edge_id, True

    def _edge(self, db: sqlite3.Connection, edge_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM knowledge_graph_edges WHERE edge_id = ?", (edge_id,)
        ).fetchone()
        if row is None:
            raise GraphEdgeNotFoundError(edge_id)
        return row

    def get(self, edge_id: str, *, include_history: bool = True) -> dict[str, Any]:
        with self._connect() as db:
            row = self._edge(db, edge_id)
            result = self._decode(row)
            result["from_node"] = self._node(db, row["from_type"], row["from_id"])
            result["to_node"] = self._node(db, row["to_type"], row["to_id"])
            if include_history:
                result["history"] = self.history(edge_id)
        return result

    def list_edges(
        self, *, project: str | None = None, include_inferred: bool = True,
        include_rejected: bool = False, include_private: bool = False,
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        scope = self._project_key(project)
        where: list[str] = []
        params: list[Any] = []
        if project is not None:
            where.append("project_key = ?")
            params.append(scope)
        if not include_inferred:
            where.append("state = 'confirmed'")
        if not include_rejected:
            where.append("state != 'rejected'")
        if not include_private:
            where.append("visibility = 'normal'")
        params.append(max(1, min(int(limit), 1000)))
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM knowledge_graph_edges" +
                (" WHERE " + " AND ".join(where) if where else "") +
                " ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
            result = []
            for row in rows:
                try:
                    source = self._node(db, row["from_type"], row["from_id"])
                    target = self._node(db, row["to_type"], row["to_id"])
                except GraphNodeNotFoundError:
                    continue  # deleted/private/retired endpoints never appear as dangling nodes
                endpoint_scopes = {
                    value for value in (source["project_key"], target["project_key"]) if value
                }
                if len(endpoint_scopes) > 1:
                    continue  # a later project reassignment must not leak across workspaces
                if row["project_key"] and endpoint_scopes and row["project_key"] not in endpoint_scopes:
                    continue
                result.append({
                    **self._decode(row), "from_node": source, "to_node": target,
                })
        return result

    def correct(
        self, edge_id: str, *, relation: str | None = None, provenance: str | None = None,
        confidence: float | None = None, evidence: list[dict[str, Any]] | None = None,
        note: str | None = None, visibility: str | None = None, actor: str = "owner",
    ) -> dict[str, Any]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._edge(db, edge_id)
            updates: dict[str, Any] = {}
            if relation is not None:
                if relation not in RELATIONS:
                    raise KnowledgeGraphError("unsupported relationship type")
                updates["relation"] = relation
            new_provenance = provenance if provenance is not None else row["provenance"]
            if new_provenance not in PROVENANCE:
                raise KnowledgeGraphError("unsupported edge provenance")
            if provenance is not None:
                updates["provenance"] = new_provenance
            if confidence is not None:
                updates["confidence"] = _confidence(confidence)
            new_evidence = json.loads(row["evidence_json"] or "[]")
            if evidence is not None:
                new_evidence = _evidence(evidence, new_provenance)
                updates["evidence_json"] = json.dumps(new_evidence, ensure_ascii=False)
            elif new_provenance in EVIDENCE_REQUIRED:
                _evidence(new_evidence, new_provenance)
            if note is not None:
                note = note.strip()
                if len(note) > MAX_NOTE:
                    raise KnowledgeGraphError(f"note is limited to {MAX_NOTE} characters")
                if note:
                    reject_secret(note, field="note")
                updates["note"] = note
            if visibility is not None:
                if visibility not in VISIBILITIES:
                    raise KnowledgeGraphError("visibility must be normal or private")
                updates["visibility"] = visibility
            if new_provenance != "owner_provided" and {
                "relation", "provenance", "confidence", "evidence_json"
            }.intersection(updates):
                # Material changes to a derived claim invalidate its previous review.
                updates["state"] = "inferred"
                updates["reviewed_at"] = None
            elif provenance == "owner_provided":
                updates["state"] = "confirmed"
                updates["reviewed_at"] = _now()
            if not updates:
                raise KnowledgeGraphError("no edge correction was supplied")
            if "relation" in updates:
                duplicate = db.execute(
                    "SELECT 1 FROM knowledge_graph_edges WHERE from_type=? AND from_id=? "
                    "AND to_type=? AND to_id=? AND relation=? AND edge_id != ?",
                    (row["from_type"], row["from_id"], row["to_type"], row["to_id"],
                     updates["relation"], edge_id),
                ).fetchone()
                if duplicate:
                    raise KnowledgeGraphError("this relationship already exists")
            self._snapshot(db, row, actor=actor, change_type="corrected")
            updates["updated_at"] = _now()
            fields = list(updates)
            db.execute(
                f"UPDATE knowledge_graph_edges SET {', '.join(f'{field} = ?' for field in fields)} "
                "WHERE edge_id = ?",
                (*[updates[field] for field in fields], edge_id),
            )
            AuditLog.write(
                db, actor=actor, action="knowledge_edge_corrected",
                target_type="knowledge_graph_edge", target_id=edge_id,
                detail={"fields": sorted(updates)},
            )
        return self.get(edge_id)

    def review(self, edge_id: str, decision: str, *, actor: str = "owner") -> dict[str, Any]:
        if decision not in {"confirm", "reject"}:
            raise KnowledgeGraphError("decision must be confirm or reject")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._edge(db, edge_id)
            if row["state"] != "inferred":
                raise KnowledgeGraphError("only inferred edges can be reviewed")
            self._snapshot(db, row, actor=actor, change_type=decision)
            state = "confirmed" if decision == "confirm" else "rejected"
            now = _now()
            db.execute(
                "UPDATE knowledge_graph_edges SET state=?, reviewed_at=?, updated_at=? "
                "WHERE edge_id=?",
                (state, now, now, edge_id),
            )
            AuditLog.write(
                db, actor=actor, action=f"knowledge_edge_{decision}ed",
                target_type="knowledge_graph_edge", target_id=edge_id,
                detail={"state": state},
            )
        return self.get(edge_id)

    def history(self, edge_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        cap = max(1, min(int(limit), 200))
        with self._connect() as db:
            rows = db.execute(
                "SELECT history_id, changed_at, actor, change_type, snapshot_json "
                "FROM knowledge_graph_edge_history WHERE edge_id=? ORDER BY history_id DESC LIMIT ?",
                (edge_id, cap),
            ).fetchall()
        return [{
            "history_id": row["history_id"], "changed_at": row["changed_at"],
            "actor": row["actor"], "change_type": row["change_type"],
            "snapshot": json.loads(row["snapshot_json"]),
        } for row in rows]

    def remove(self, edge_id: str, *, actor: str = "owner") -> bool:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM knowledge_graph_edges WHERE edge_id = ?", (edge_id,)
            ).fetchone()
            if row is None:
                return False
            self._snapshot(db, row, actor=actor, change_type="deleted")
            db.execute("DELETE FROM knowledge_graph_edges WHERE edge_id = ?", (edge_id,))
            AuditLog.write(
                db, actor=actor, action="knowledge_edge_deleted",
                target_type="knowledge_graph_edge", target_id=edge_id,
                detail={"from_type": row["from_type"], "to_type": row["to_type"],
                        "relation": row["relation"]},
            )
        return True
