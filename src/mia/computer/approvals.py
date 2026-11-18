"""One-time, payload-bound approvals for sensitive computer actions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def _now() -> datetime:
    return datetime.now(UTC)


def _digest(action_id: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"action_id": action_id, "payload": payload},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ApprovalError(RuntimeError):
    """An approval is invalid, expired, mismatched or already consumed."""


class ApprovalStore:
    """Persistent approvals bound to the exact action and payload hash."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS action_approvals (
                    approval_id TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    approved_at TEXT,
                    consumed_at TEXT
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_approvals_status_expiry
                ON action_approvals(status, expires_at)
            """)

    def request(
        self,
        *,
        action_id: str,
        payload: dict[str, Any],
        summary: str,
        ttl_seconds: int = 600,
    ) -> dict[str, str]:
        if not action_id.strip() or not summary.strip():
            raise ValueError("action_id and summary are required")
        if not 30 <= ttl_seconds <= 3600:
            raise ValueError("ttl_seconds must be between 30 and 3600")
        created = _now()
        expires = created + timedelta(seconds=ttl_seconds)
        approval_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO action_approvals (
                    approval_id, action_id, payload_sha256, summary, status,
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    approval_id,
                    action_id,
                    _digest(action_id, payload),
                    summary,
                    created.isoformat(),
                    expires.isoformat(),
                ),
            )
        return {
            "approval_id": approval_id,
            "status": "pending",
            "expires_at": expires.isoformat(),
            "summary": summary,
        }

    def approve(self, approval_id: str) -> dict[str, str]:
        now = _now().isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status, expires_at, summary FROM action_approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if not row:
                raise ApprovalError("Approval was not found")
            if row["status"] != "pending":
                raise ApprovalError(f"Approval is {row['status']}, not pending")
            if row["expires_at"] <= now:
                db.execute(
                    "UPDATE action_approvals SET status = 'expired' WHERE approval_id = ?",
                    (approval_id,),
                )
                raise ApprovalError("Approval has expired")
            db.execute(
                """
                UPDATE action_approvals
                SET status = 'approved', approved_at = ?
                WHERE approval_id = ? AND status = 'pending'
                """,
                (now, approval_id),
            )
        return {"approval_id": approval_id, "status": "approved", "summary": row["summary"]}

    def consume(
        self,
        *,
        approval_id: str,
        action_id: str,
        payload: dict[str, Any],
    ) -> None:
        now = _now()
        now_text = now.isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT action_id, payload_sha256, status, expires_at
                FROM action_approvals WHERE approval_id = ?
                """,
                (approval_id,),
            ).fetchone()
            if not row:
                raise ApprovalError("Approval was not found")
            if row["status"] != "approved":
                raise ApprovalError(f"Approval is {row['status']}, not approved")
            if datetime.fromisoformat(row["expires_at"]) <= now:
                db.execute(
                    "UPDATE action_approvals SET status = 'expired' WHERE approval_id = ?",
                    (approval_id,),
                )
                raise ApprovalError("Approval has expired")
            if row["action_id"] != action_id:
                raise ApprovalError("Approval action does not match")
            if row["payload_sha256"] != _digest(action_id, payload):
                raise ApprovalError("Approved payload has changed")
            updated = db.execute(
                """
                UPDATE action_approvals
                SET status = 'consumed', consumed_at = ?
                WHERE approval_id = ? AND status = 'approved'
                """,
                (now_text, approval_id),
            )
            if updated.rowcount != 1:
                raise ApprovalError("Approval was already consumed")

    def list_pending(self) -> list[dict[str, str]]:
        with self._connect() as db:
            rows = db.execute("""
                SELECT approval_id, action_id, summary, status, created_at, expires_at
                FROM action_approvals
                WHERE status IN ('pending', 'approved')
                ORDER BY created_at
            """).fetchall()
        return [dict(row) for row in rows]
