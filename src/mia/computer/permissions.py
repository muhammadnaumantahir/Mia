"""Owner-controlled permission grants and the authorization decision for computer actions.

Rules enforced here, independent of any model output:

* Only the owner can create grants (``granted_by`` must be ``"owner"``).
* OBSERVE/READ actions of LOW risk are allowed by default.
* WRITE/EXECUTE/INTERACT actions (or MEDIUM risk) need a matching active grant or a
  per-action approval.
* HIGH and CRITICAL risk actions always need a per-action approval; grants never cover them.
* ADMINISTRATIVE actions are denied unless a separate admin grant for that exact capability
  exists, and still always need a per-action approval.
* A grant covers one capability (or an explicit set) at or below its level, optionally
  limited to a directory; it never silently authorizes unrelated capabilities.
* Grants expire, can be revoked, and session grants die with the process.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePath
from typing import Any

from mia.computer.capabilities import CapabilitySpec, PermissionLevel, RiskLevel

MAX_GRANT_SECONDS = 365 * 24 * 3600


class PermissionError_(RuntimeError):
    """A grant request is invalid or not made by the owner."""


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    requires_approval: bool
    reason: str
    grant_id: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _within(directory: str, candidate: str) -> bool:
    """Component-wise containment. Callers must pass already-resolved absolute paths."""
    base = PurePath(os.path.normcase(directory))
    target = PurePath(os.path.normcase(candidate))
    return target == base or base in target.parents


class PermissionPolicy:
    def __init__(self, database_path: str | Path, *, clock: Any = _now) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self.session_id = uuid.uuid4().hex
        self._init()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self._db() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS permission_grants (
                    grant_id TEXT PRIMARY KEY,
                    capabilities TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    directory TEXT,
                    kind TEXT NOT NULL,
                    session_id TEXT,
                    admin INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    revoked_at TEXT,
                    note TEXT NOT NULL DEFAULT ''
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS permission_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at TEXT NOT NULL, event TEXT NOT NULL, grant_id TEXT,
                    capability TEXT, outcome TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
                )"""
            )

    def _log(self, event: str, outcome: str, *, grant_id: str | None = None,
             capability: str | None = None, detail: str = "") -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO permission_history(at,event,grant_id,capability,outcome,detail)"
                " VALUES (?,?,?,?,?,?)",
                (self._clock().isoformat(), event, grant_id, capability, outcome, detail[:300]),
            )

    # ---- owner operations -------------------------------------------------------------
    def grant(
        self,
        *,
        granted_by: str,
        capabilities: list[str],
        level: PermissionLevel,
        directory: str | None = None,
        kind: str = "session",
        duration_seconds: int | None = None,
        admin: bool = False,
        note: str = "",
    ) -> dict[str, Any]:
        if granted_by != "owner":
            self._log("grant", "denied", detail=f"granted_by={granted_by!r}")
            raise PermissionError_("only the owner can grant permissions")
        if not capabilities or any(not c or "." not in c for c in capabilities):
            raise PermissionError_("at least one valid capability name is required")
        if kind not in {"session", "persistent"}:
            raise PermissionError_("kind must be session or persistent")
        if level >= PermissionLevel.ADMINISTRATIVE and not admin:
            raise PermissionError_("administrative level requires an explicit admin grant")
        if admin and "*" in capabilities:
            raise PermissionError_("admin grants must name exact capabilities")
        if duration_seconds is not None and not 0 < duration_seconds <= MAX_GRANT_SECONDS:
            raise PermissionError_("duration is out of range")
        if kind == "persistent" and duration_seconds is None:
            raise PermissionError_("persistent grants must have an expiry")
        now = self._clock()
        grant_id = uuid.uuid4().hex
        expires = (now + timedelta(seconds=duration_seconds)).isoformat() if duration_seconds else None
        with self._db() as db:
            db.execute(
                "INSERT INTO permission_grants(grant_id,capabilities,level,directory,kind,session_id,"
                "admin,created_at,expires_at,note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    grant_id, json.dumps(sorted(capabilities)), int(level), directory, kind,
                    self.session_id if kind == "session" else None, int(admin),
                    now.isoformat(), expires, note[:200],
                ),
            )
        self._log("grant", "created", grant_id=grant_id, detail=",".join(capabilities))
        return {"grant_id": grant_id, "expires_at": expires, "kind": kind}

    def revoke(self, grant_id: str) -> bool:
        with self._db() as db:
            cur = db.execute(
                "UPDATE permission_grants SET revoked_at=? WHERE grant_id=? AND revoked_at IS NULL",
                (self._clock().isoformat(), grant_id),
            )
            changed = cur.rowcount > 0
        self._log("revoke", "revoked" if changed else "not_found", grant_id=grant_id)
        return changed

    def revoke_all(self) -> int:
        with self._db() as db:
            cur = db.execute(
                "UPDATE permission_grants SET revoked_at=? WHERE revoked_at IS NULL",
                (self._clock().isoformat(),),
            )
            count = cur.rowcount
        self._log("revoke_all", "revoked", detail=str(count))
        return count

    def active_grants(self) -> list[dict[str, Any]]:
        now = self._clock().isoformat()
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM permission_grants WHERE revoked_at IS NULL "
                "AND (expires_at IS NULL OR expires_at > ?) "
                "AND (kind='persistent' OR session_id=?) ORDER BY created_at",
                (now, self.session_id),
            ).fetchall()
        return [
            {
                "grant_id": r["grant_id"],
                "capabilities": json.loads(r["capabilities"]),
                "level": PermissionLevel(r["level"]).name.lower(),
                "directory": r["directory"],
                "kind": r["kind"],
                "admin": bool(r["admin"]),
                "expires_at": r["expires_at"],
                "note": r["note"],
            }
            for r in rows
        ]

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT at,event,grant_id,capability,outcome,detail FROM permission_history "
                "ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- decision -----------------------------------------------------------------------
    def authorize(self, spec: CapabilitySpec, *, path: str | None = None) -> Decision:
        decision = self._decide(spec, path)
        self._log(
            "authorize", "allowed" if decision.allowed else "denied", grant_id=decision.grant_id,
            capability=spec.name,
            detail=f"approval={decision.requires_approval}; {decision.reason}",
        )
        return decision

    def _decide(self, spec: CapabilitySpec, path: str | None) -> Decision:
        if spec.permission == PermissionLevel.ADMINISTRATIVE:
            grant = self._matching(spec, path, admin=True)
            if grant is None:
                return Decision(False, True, "administrative action needs a separate owner grant")
            return Decision(True, True, "admin grant present; per-action approval still required",
                            grant["grant_id"])
        if spec.risk >= RiskLevel.HIGH:
            return Decision(True, True, "high-risk action always needs per-action approval")
        if not spec.requires_approval_by_default:
            return Decision(True, False, "low-risk observe/read action")
        grant = self._matching(spec, path, admin=False)
        if grant is not None:
            return Decision(True, False, "covered by an active owner grant", grant["grant_id"])
        return Decision(True, True, "no grant covers this action; per-action approval required")

    def _matching(self, spec: CapabilitySpec, path: str | None, *, admin: bool) -> dict[str, Any] | None:
        for grant in self.active_grants():
            if grant["admin"] != admin:
                continue
            if spec.name not in grant["capabilities"] and "*" not in grant["capabilities"]:
                continue
            if PermissionLevel[grant["level"].upper()] < spec.permission:
                continue
            if grant["directory"] is not None:
                if path is None or not _within(grant["directory"], path):
                    continue
            return grant
        return None
