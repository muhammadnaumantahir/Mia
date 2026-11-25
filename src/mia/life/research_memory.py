"""Project-scoped bridge from owner-confirmed research claims to structured memory."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from mia.life.research import ResearchService
from mia.memory.store import MemoryStore


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ResearchMemoryError(ValueError):
    """A research finding cannot be imported or changed in project memory."""


class ResearchMemoryBridge:
    """Import reviewed claims into project-filtered structured memory with lifecycle links."""

    def __init__(self, database_path: str | Path, research: ResearchService, memory: MemoryStore):
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.research = research
        self.memory = memory
        self._lock = threading.RLock()
        with self._db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS research_memory_links (
                    claim_id TEXT NOT NULL,
                    project_key TEXT NOT NULL,
                    record_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (claim_id, project_key)
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_research_memory_project "
                "ON research_memory_links(project_key, created_at)"
            )

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _project(self, name: str) -> dict[str, Any]:
        project = self.memory.projects.get(name)
        if project is None:
            raise ResearchMemoryError(
                "Create this project on Mia's Projects page before importing findings"
            )
        return project

    def _confirmed_claim(self, claim_id: str) -> dict[str, Any]:
        claim = self.research.get_claim(claim_id)
        if claim["status"] != "confirmed":
            raise ResearchMemoryError("Only owner-confirmed research findings can enter memory")
        if not claim["citations"]:
            raise ResearchMemoryError("A research finding needs verified source citations")
        return claim

    @staticmethod
    def _record_fields(claim: dict[str, Any]) -> dict[str, Any]:
        citations = [
            {
                "source_id": item["source_id"],
                "url": item["url"],
                "host": item["host"],
                "title": item["title"],
                "quote": item["quote"],
                "fetched_at": item["fetched_at"],
                "sha256": item["sha256"],
                "page_date": item["page_date"],
                "date_source": item["date_source"],
            }
            for item in claim["citations"]
        ]
        evidence = []
        for citation in citations:
            url = citation["url"]
            if url and {"type": "url", "ref": url} not in evidence:
                evidence.append({"type": "url", "ref": url})
        details = {
            "research_claim_id": claim["claim_id"],
            "research_run_id": claim["run_id"],
            "research_confirmed_at": claim["reviewed_at"],
            "research_citations": citations,
            "source_hashes": sorted({item["sha256"] for item in citations if item["sha256"]}),
            "owner_confirmed": True,
        }
        return {
            "record_type": "fact",
            "title": claim["text"][:200],
            "body": claim["text"],
            "provenance": "source_backed",
            "confidence": None,
            "evidence": evidence,
            "details": details,
            "origin": {
                "kind": "research_finding",
                "claim_id": claim["claim_id"],
                "run_id": claim["run_id"],
                "confirmed_at": claim["reviewed_at"],
            },
            "actor": "owner",
        }

    def _current_record(self, record_id: str) -> dict[str, Any] | None:
        record = self.memory.structured.get(record_id)
        seen: set[str] = set()
        while record and record.get("superseded_by") and record["record_id"] not in seen:
            seen.add(record["record_id"])
            record = self.memory.structured.get(record["superseded_by"])
        return record

    def import_finding(self, claim_id: str, project_name: str) -> dict[str, Any]:
        with self._lock:
            claim = self._confirmed_claim(claim_id)
            project = self._project(project_name)
            project_key = project["project_key"]
            with self._db() as db:
                link = db.execute(
                    "SELECT record_id FROM research_memory_links WHERE claim_id=? AND project_key=?",
                    (claim_id, project_key),
                ).fetchone()
                if link:
                    existing = self._current_record(link["record_id"])
                    if existing:
                        return existing
                    db.execute(
                        "DELETE FROM research_memory_links WHERE claim_id=? AND project_key=?",
                        (claim_id, project_key),
                    )

            fields = self._record_fields(claim)
            record_id, _ = self.memory.structured.add(
                **fields, project=project["name"]
            )
            try:
                with self._db() as db:
                    db.execute(
                        "INSERT INTO research_memory_links VALUES (?,?,?,?)",
                        (claim_id, project_key, record_id, _now()),
                    )
            except sqlite3.IntegrityError:
                # A concurrent request may have won the idempotency key. Remove our
                # orphaned record and return the winning record instead.
                self.memory.structured.delete(record_id, chain=True)
                with self._db() as db:
                    link = db.execute(
                        "SELECT record_id FROM research_memory_links WHERE claim_id=? AND project_key=?",
                        (claim_id, project_key),
                    ).fetchone()
                if not link:
                    raise
                existing = self._current_record(link["record_id"])
                if existing:
                    return existing
                raise ResearchMemoryError("The existing project memory link is unavailable")
            except Exception:
                self.memory.structured.delete(record_id, chain=True)
                raise
            record = self.memory.structured.get(record_id)
            if record is None:
                raise ResearchMemoryError("The imported structured memory record could not be read")
            return record

    def list_for_project(self, project_name: str) -> list[dict[str, Any]]:
        project = self._project(project_name)
        with self._db() as db:
            links = db.execute(
                "SELECT claim_id, record_id FROM research_memory_links "
                "WHERE project_key=? ORDER BY created_at DESC",
                (project["project_key"],),
            ).fetchall()
        records: dict[str, dict[str, Any]] = {}
        stale: list[str] = []
        for link in links:
            record = self._current_record(link["record_id"])
            if record is None:
                stale.append(link["record_id"])
                continue
            if record.get("project") == project["name"] and record["status"] in {"active", "completed"}:
                records[record["record_id"]] = record
        if stale:
            with self._db() as db:
                db.executemany(
                    "DELETE FROM research_memory_links WHERE record_id=?",
                    [(record_id,) for record_id in stale],
                )
        return list(records.values())

    def correct(self, record_id: str, replacement_claim_id: str) -> dict[str, Any]:
        with self._lock:
            replacement = self._confirmed_claim(replacement_claim_id)
            current = self._current_record(record_id)
            if current is None:
                raise KeyError(record_id)
            with self._db() as db:
                linked = db.execute(
                    "SELECT project_key FROM research_memory_links WHERE record_id=?",
                    (record_id,),
                ).fetchone()
                if linked is None and current["record_id"] != record_id:
                    linked = db.execute(
                        "SELECT project_key FROM research_memory_links WHERE record_id=?",
                        (current["record_id"],),
                    ).fetchone()
                if linked is None:
                    raise ResearchMemoryError("This memory record is not linked to a research finding")
                duplicate = db.execute(
                    "SELECT record_id FROM research_memory_links WHERE claim_id=? AND project_key=?",
                    (replacement_claim_id, linked["project_key"]),
                ).fetchone()
                if duplicate:
                    existing = self._current_record(duplicate["record_id"])
                    if existing:
                        raise ResearchMemoryError("This replacement finding is already in the project")
            fields = self._record_fields(replacement)
            new_id, _ = self.memory.structured.supersede(
                current["record_id"],
                title=fields["title"],
                body=fields["body"],
                details=fields["details"],
                provenance=fields["provenance"],
                confidence=fields["confidence"],
                evidence=fields["evidence"],
                actor="owner",
            )
            with self._db() as db:
                db.execute(
                    "INSERT INTO research_memory_links VALUES (?,?,?,?)",
                    (replacement_claim_id, linked["project_key"], new_id, _now()),
                )
            record = self.memory.structured.get(new_id)
            if record is None:
                raise ResearchMemoryError("The corrected memory record could not be read")
            return record

    def delete(self, record_id: str) -> bool:
        with self._lock:
            record = self.memory.structured.get(record_id)
            if record is None:
                return False
            with self._db() as db:
                linked = db.execute(
                    "SELECT 1 FROM research_memory_links WHERE record_id=?",
                    (record_id,),
                ).fetchone()
                if linked is None:
                    raise ResearchMemoryError("This memory record is not linked to a research finding")
            chain_ids: set[str] = set()
            current = record
            while current and current["record_id"] not in chain_ids:
                chain_ids.add(current["record_id"])
                previous = current.get("supersedes_id")
                current = self.memory.structured.get(previous) if previous else None
            current = record
            while current:
                next_id = current.get("superseded_by")
                if not next_id:
                    break
                current = self.memory.structured.get(next_id)
                if current is None or current["record_id"] in chain_ids:
                    break
                chain_ids.add(current["record_id"])
            deleted = self.memory.structured.delete(record_id, chain=True, actor="owner")
            with self._db() as db:
                db.executemany(
                    "DELETE FROM research_memory_links WHERE record_id=?",
                    [(item,) for item in chain_ids],
                )
            return deleted


class ImportFindingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str = Field(min_length=1, max_length=64)


class CorrectFindingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replacement_claim_id: str = Field(min_length=1, max_length=64)


def register_research_memory_routes(
    app: FastAPI,
    bridge: ResearchMemoryBridge,
    record_event: Any,
) -> None:
    @app.post("/api/research/claims/{claim_id}/memory")
    def import_finding(claim_id: str, item: ImportFindingInput) -> dict[str, Any]:
        try:
            result = bridge.import_finding(claim_id, item.project)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown research claim or memory record") from exc
        except ResearchMemoryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        record_event(
            event_type="research_finding_imported",
            summary="Owner-confirmed research finding added to project memory",
            outcome="success",
            payload={"claim_id": claim_id, "record_id": result["record_id"], "project": result["project"]},
        )
        return result

    @app.get("/api/research/memory")
    def list_project_findings(project: str) -> dict[str, Any]:
        try:
            return {"project": project, "items": bridge.list_for_project(project)}
        except ResearchMemoryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/research/memory/{record_id}/correct")
    def correct_finding(record_id: str, item: CorrectFindingInput) -> dict[str, Any]:
        try:
            result = bridge.correct(record_id, item.replacement_claim_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown research memory record") from exc
        except ResearchMemoryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        record_event(
            event_type="research_memory_corrected",
            summary="Project research memory corrected using a new confirmed finding",
            outcome="success",
            payload={"record_id": record_id, "successor_id": result["record_id"]},
        )
        return result

    @app.delete("/api/research/memory/{record_id}")
    def delete_finding(record_id: str) -> dict[str, bool]:
        try:
            deleted = bridge.delete(record_id)
        except ResearchMemoryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if deleted:
            record_event(
                event_type="research_memory_deleted",
                summary="Project research memory and its correction chain deleted",
                outcome="success",
                payload={"record_id": record_id},
            )
        return {"deleted": deleted}
