"""Project workspace: goal, status and milestones plus an isolated cross-store view (MIA-LIFE-04).

A project is identified by its normalised name (case/spacing-insensitive, the same key used
by conversations, structured records and personal items). The registry here only adds what
those stores do not have: a goal, a status and milestones. Tasks, decisions and
conversations stay in their own stores and are *selected* by project, never copied.

Isolation contract
- A project's workspace shows only items whose project key equals its own. Unassigned items
  and other projects' items never appear in it.
- Temporary (private) conversations are never listed, counted or surfaced.
- Deleting workspace metadata removes the goal, status and milestones only; tasks, decisions
  and conversations keep their project label (the owner can clear it per item).
- The project name is an owner-view filter, not a security boundary.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mia.memory.safety import reject_secret
from mia.memory.schema import AuditLog

if TYPE_CHECKING:
    from mia.memory.store import MemoryStore

PROJECT_STATUSES = frozenset({"active", "paused", "completed", "archived"})
MILESTONE_STATUSES = frozenset({"open", "done"})
MAX_GOAL = 2_000
MAX_TITLE = 200
MAX_MILESTONES = 200
SECTION_LIMIT = 25


class ProjectNotFoundError(KeyError):
    """The project has no workspace record."""


class MilestoneNotFoundError(KeyError):
    """No milestone has this id in this project."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _name_and_key(project: str) -> tuple[str, str]:
    from mia.memory.store import normalise_project_name  # lazy: store imports this module

    name = normalise_project_name(project)
    if name is None:
        raise ValueError("project name is required")
    return name, name.casefold()


def _clean_due(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError("due_at must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("due_at must include a timezone offset (for example +05:00 or Z)")
    return parsed.astimezone(UTC).isoformat()


def _clean_goal(goal: str | None) -> str | None:
    if goal is None:
        return None
    goal = goal.strip()
    if len(goal) > MAX_GOAL:
        raise ValueError(f"goal is limited to {MAX_GOAL} characters")
    reject_secret(goal, field="goal")
    return goal


def _clean_title(title: str) -> str:
    title = " ".join(str(title).split())
    if not title:
        raise ValueError("title is required")
    if len(title) > MAX_TITLE:
        raise ValueError(f"title is limited to {MAX_TITLE} characters")
    reject_secret(title, field="title")
    return title


class ProjectStore:
    """Registry and milestones in the shared memory database."""

    def __init__(
        self, connect: Callable[[], AbstractContextManager[sqlite3.Connection]]
    ) -> None:
        self._connect = connect

    @staticmethod
    def initialize(db: sqlite3.Connection) -> None:
        db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                project_key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                goal TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS project_milestones (
                milestone_id TEXT PRIMARY KEY,
                project_key TEXT NOT NULL,
                title TEXT NOT NULL,
                due_at TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_milestones_project "
            "ON project_milestones(project_key, status)"
        )

    # -------------------------------------------------------------- projects

    def create(
        self, name: str, *, goal: str = "", status: str = "active", actor: str = "owner"
    ) -> dict[str, Any]:
        clean, key = _name_and_key(name)
        if status not in PROJECT_STATUSES:
            raise ValueError("status must be one of " + ", ".join(sorted(PROJECT_STATUSES)))
        goal = _clean_goal(goal) or ""
        now = _now()
        with self._connect() as db:
            if db.execute("SELECT 1 FROM projects WHERE project_key = ?", (key,)).fetchone():
                raise ValueError("a workspace for this project already exists")
            db.execute(
                "INSERT INTO projects (project_key, name, goal, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, clean, goal, status, now, now),
            )
            AuditLog.write(
                db, actor=actor, action="project_created", target_type="project",
                target_id=key, detail={"status": status},
            )
        return self.get(clean)  # type: ignore[return-value]

    def get(self, name: str) -> dict[str, Any] | None:
        _, key = _name_and_key(name)
        with self._connect() as db:
            row = db.execute("SELECT * FROM projects WHERE project_key = ?", (key,)).fetchone()
        return dict(row) if row else None

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE").fetchall()
        return [dict(r) for r in rows]

    def update(
        self, name: str, *, new_name: str | None = None, goal: str | None = None,
        status: str | None = None, actor: str = "owner",
    ) -> list[str]:
        """Update project metadata and optionally rename it without losing linked material.

        Project names are the shared labels used by conversations, tasks and structured
        records. Rename therefore updates every same-database reference in one transaction.
        It refuses to merge with a different project label rather than silently combining data.
        """
        _, key = _name_and_key(name)
        target_name, target_key = _name_and_key(new_name) if new_name is not None else (None, key)
        changes: dict[str, Any] = {}
        if goal is not None:
            changes["goal"] = _clean_goal(goal)
        if status is not None:
            if status not in PROJECT_STATUSES:
                raise ValueError("status must be one of " + ", ".join(sorted(PROJECT_STATUSES)))
            changes["status"] = status

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM projects WHERE project_key = ?", (key,)).fetchone()
            if row is None:
                raise ProjectNotFoundError(key)

            changed: set[str] = {
                field for field, value in changes.items() if row[field] != value
            }
            renamed = target_name is not None and row["name"] != target_name
            key_changed = target_key != key

            if key_changed:
                if db.execute(
                    "SELECT 1 FROM projects WHERE project_key = ?", (target_key,)
                ).fetchone():
                    raise ValueError("a workspace for the new project name already exists")

                # An unregistered label may already own tasks, decisions or chats. Do not
                # merge those materials into this workspace as a side effect of renaming.
                from mia.memory.store import project_key

                for table in ("conversations", "personal_items", "structured_memory"):
                    columns = {
                        item["name"] for item in db.execute(f"PRAGMA table_info({table})")
                    }
                    if "project" not in columns:
                        continue
                    rows = db.execute(
                        f"SELECT project FROM {table} WHERE project IS NOT NULL"
                    ).fetchall()
                    if any(project_key(item["project"]) == target_key for item in rows):
                        raise ValueError(
                            "the new project name is already used by labelled material"
                        )

            if renamed:
                changed.add("name")

            linked_counts: dict[str, int] = {}
            if key_changed or renamed:
                from mia.memory.store import project_key

                for table in ("conversations", "personal_items", "structured_memory"):
                    columns = {
                        item["name"] for item in db.execute(f"PRAGMA table_info({table})")
                    }
                    if "project" not in columns:
                        continue
                    rows = db.execute(
                        f"SELECT rowid, project FROM {table} WHERE project IS NOT NULL"
                    ).fetchall()
                    rowids = [
                        item["rowid"] for item in rows
                        if project_key(item["project"]) == key
                    ]
                    for rowid in rowids:
                        db.execute(
                            f"UPDATE {table} SET project = ? WHERE rowid = ?",
                            (target_name or row["name"], rowid),
                        )
                    linked_counts[table] = len(rowids)

            if key_changed:
                db.execute(
                    "UPDATE project_milestones SET project_key = ? WHERE project_key = ?",
                    (target_key, key),
                )

            project_updates: dict[str, Any] = {}
            if renamed:
                project_updates["name"] = target_name
            if key_changed:
                project_updates["project_key"] = target_key
            project_updates.update(changes)

            if project_updates:
                fields = list(project_updates)
                db.execute(
                    f"UPDATE projects SET {', '.join(f'{field} = ?' for field in fields)}, "
                    "updated_at = ? WHERE project_key = ?",
                    (*[project_updates[field] for field in fields], _now(), key),
                )
            if not changed:
                return []
            result = sorted(changed)
            AuditLog.write(
                db, actor=actor, action="project_updated", target_type="project",
                target_id=key,
                detail={"fields": result, "linked_material_counts": linked_counts},
            )
        return result

    def delete(self, name: str, *, actor: str = "owner") -> bool:
        """Remove the goal, status and milestones. Tasks, decisions and chats keep their label."""
        _, key = _name_and_key(name)
        with self._connect() as db:
            if not db.execute("SELECT 1 FROM projects WHERE project_key = ?", (key,)).fetchone():
                return False
            gone = db.execute(
                "DELETE FROM project_milestones WHERE project_key = ?", (key,)
            ).rowcount
            db.execute("DELETE FROM projects WHERE project_key = ?", (key,))
            AuditLog.write(
                db, actor=actor, action="project_deleted", target_type="project",
                target_id=key, detail={"milestones_removed": gone},
            )
        return True

    # ------------------------------------------------------------ milestones

    def add_milestone(
        self, name: str, title: str, *, due_at: str | None = None, actor: str = "owner"
    ) -> str:
        _, key = _name_and_key(name)
        title = _clean_title(title)
        due = _clean_due(due_at)
        milestone_id = uuid.uuid4().hex
        now = _now()
        with self._connect() as db:
            if not db.execute("SELECT 1 FROM projects WHERE project_key = ?", (key,)).fetchone():
                raise ProjectNotFoundError(key)
            count = db.execute(
                "SELECT COUNT(*) FROM project_milestones WHERE project_key = ?", (key,)
            ).fetchone()[0]
            if count >= MAX_MILESTONES:
                raise ValueError(f"a project is limited to {MAX_MILESTONES} milestones")
            db.execute(
                "INSERT INTO project_milestones "
                "(milestone_id, project_key, title, due_at, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'open', ?, ?)",
                (milestone_id, key, title, due, now, now),
            )
            AuditLog.write(
                db, actor=actor, action="milestone_created", target_type="project_milestone",
                target_id=milestone_id, detail={"project": key},
            )
        return milestone_id

    def update_milestone(
        self, name: str, milestone_id: str, *, title: str | None = None,
        status: str | None = None, due_at: str | None = None, clear_due: bool = False,
        actor: str = "owner",
    ) -> list[str]:
        _, key = _name_and_key(name)
        changes: dict[str, Any] = {}
        if title is not None:
            changes["title"] = _clean_title(title)
        if status is not None:
            if status not in MILESTONE_STATUSES:
                raise ValueError("status must be open or done")
            changes["status"] = status
        if clear_due:
            changes["due_at"] = None
        elif due_at is not None:
            changes["due_at"] = _clean_due(due_at)
        if not changes:
            raise ValueError("nothing to change")
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM project_milestones WHERE milestone_id = ? AND project_key = ?",
                (milestone_id, key),
            ).fetchone()
            if row is None:  # also refuses another project's milestone id
                raise MilestoneNotFoundError(milestone_id)
            changed = sorted(k for k, v in changes.items() if row[k] != v)
            if not changed:
                return []
            now = _now()
            sets = {k: changes[k] for k in changed}
            sets["updated_at"] = now
            if "status" in changed:
                sets["completed_at"] = now if changes["status"] == "done" else None
            db.execute(
                f"UPDATE project_milestones SET {', '.join(f'{k} = ?' for k in sets)} "  # noqa: S608
                "WHERE milestone_id = ?",
                (*sets.values(), milestone_id),
            )
            AuditLog.write(
                db, actor=actor, action="milestone_updated", target_type="project_milestone",
                target_id=milestone_id, detail={"project": key, "fields": changed},
            )
        return changed

    def delete_milestone(self, name: str, milestone_id: str, *, actor: str = "owner") -> bool:
        _, key = _name_and_key(name)
        with self._connect() as db:
            cur = db.execute(
                "DELETE FROM project_milestones WHERE milestone_id = ? AND project_key = ?",
                (milestone_id, key),
            )
            if not cur.rowcount:
                return False
            AuditLog.write(
                db, actor=actor, action="milestone_deleted", target_type="project_milestone",
                target_id=milestone_id, detail={"project": key},
            )
        return True

    def milestones(self, name: str) -> list[dict[str, Any]]:
        _, key = _name_and_key(name)
        with self._connect() as db:
            rows = db.execute(
                "SELECT milestone_id, title, due_at, status, created_at, updated_at, completed_at "
                "FROM project_milestones WHERE project_key = ? "
                "ORDER BY status DESC, due_at IS NULL, due_at, created_at",
                (key,),
            ).fetchall()
        return [dict(r) for r in rows]

    def export(self) -> dict[str, list[dict[str, Any]]]:
        with self._connect() as db:
            projects = db.execute("SELECT * FROM projects ORDER BY project_key").fetchall()
            milestones = db.execute(
                "SELECT * FROM project_milestones ORDER BY project_key, created_at, milestone_id"
            ).fetchall()
        return {"projects": [dict(r) for r in projects],
                "project_milestones": [dict(r) for r in milestones]}


def workspace(store: MemoryStore, name: str) -> dict[str, Any]:
    """Isolated view of one project across tasks, decisions, milestones and conversations."""
    clean, key = _name_and_key(name)
    registry = store.projects.get(clean)
    tasks = [
        t for t in store.personal.list(kind="task", project=clean, limit=500)
        if t["status"] == "open"
    ]
    decisions = [
        {k: r[k] for k in ("record_id", "title", "body", "status", "updated_at")}
        for r in store.structured.list(record_type="decision", limit=500)
        if r["status"] in {"active", "completed"} and _same(r.get("project"), key)
    ]
    chats = [
        c for c in store.list_conversations(200)
        if not c["temporary"] and _same(c.get("project"), key)
    ]
    milestones = store.projects.milestones(clean) if registry else []
    return {
        "name": registry["name"] if registry else clean,
        "registered": registry is not None,
        "goal": registry["goal"] if registry else "",
        "status": registry["status"] if registry else None,
        "milestones": milestones,
        "tasks": tasks[:SECTION_LIMIT],
        "decisions": decisions[:SECTION_LIMIT],
        "conversations": [
            {"conversation_id": c["conversation_id"], "title": c["title"],
             "updated_at": c["updated_at"], "message_count": c["message_count"]}
            for c in chats[:SECTION_LIMIT]
        ],
        "counts": {
            "open_tasks": len(tasks),
            "decisions": len(decisions),
            "conversations": len(chats),
            "open_milestones": sum(1 for m in milestones if m["status"] == "open"),
        },
    }


def list_projects(store: MemoryStore) -> list[dict[str, Any]]:
    """Registered projects plus names discovered on conversations, tasks and decisions."""
    seen: dict[str, dict[str, Any]] = {}

    def entry(name: str | None, registered: dict[str, Any] | None = None) -> dict[str, Any] | None:
        from mia.memory.store import project_key

        key = project_key(name)
        if key is None:
            return None
        if key not in seen:
            seen[key] = {
                "name": registered["name"] if registered else str(name).strip(),
                "registered": registered is not None,
                "status": registered["status"] if registered else None,
                "open_tasks": 0, "decisions": 0, "conversations": 0,
            }
        return seen[key]

    for project in store.projects.list():
        entry(project["name"], project)
    for task in store.personal.list(kind="task", status="open", limit=500):
        if (e := entry(task["project"])):
            e["open_tasks"] += 1
    for record in store.structured.list(record_type="decision", limit=500):
        if record["status"] in {"active", "completed"} and (e := entry(record.get("project"))):
            e["decisions"] += 1
    for convo in store.list_conversations(200):
        if not convo["temporary"] and (e := entry(convo.get("project"))):
            e["conversations"] += 1
    return sorted(seen.values(), key=lambda p: p["name"].casefold())


def _same(value: str | None, key: str) -> bool:
    from mia.memory.store import project_key

    return project_key(value) == key
