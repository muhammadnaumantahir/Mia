"""One-time, payload-bound approvals for sensitive computer actions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
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


MAX_CARD_BYTES = 64 * 1024


class ApprovalError(RuntimeError):
    """An approval is invalid, expired, mismatched or already consumed."""


def _not_pending_message(status: str) -> str:
    return {
        "approved": "This approval was already submitted and is being processed",
        "consumed": "This approval was already used; ask for a new preview",
        "rejected": "This approval was rejected",
        "expired": "This approval has expired; ask for a new preview",
    }.get(status, f"Approval is {status}, not pending")


class ApprovalStore:
    """Persistent approvals bound to the exact action and payload hash."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._connection() as db:
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
            columns = {row["name"] for row in db.execute("PRAGMA table_info(action_approvals)")}
            if "card_json" not in columns:  # exact-action card kept only while the approval is pending
                db.execute("ALTER TABLE action_approvals ADD COLUMN card_json TEXT")

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
        with self._connection() as db:
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
        now = _now()
        now_text = now.isoformat()
        expired = False
        summary = ""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status, expires_at, summary FROM action_approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if not row:
                raise ApprovalError("Approval was not found")
            if row["status"] != "pending":
                raise ApprovalError(_not_pending_message(row["status"]))
            summary = row["summary"]
            if datetime.fromisoformat(row["expires_at"]) <= now:
                db.execute(
                    "UPDATE action_approvals SET status = 'expired' WHERE approval_id = ?",
                    (approval_id,),
                )
                expired = True
            else:
                db.execute(
                    """
                    UPDATE action_approvals
                    SET status = 'approved', approved_at = ?
                    WHERE approval_id = ? AND status = 'pending'
                    """,
                    (now_text, approval_id),
                )
        if expired:
            raise ApprovalError("Approval has expired")
        return {"approval_id": approval_id, "status": "approved", "summary": summary}

    def reject(self, approval_id: str) -> dict[str, str]:
        """Reject a pending approval or revoke an approved action before it is consumed."""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status, summary, expires_at FROM action_approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if not row:
                raise ApprovalError("Approval was not found")
            if row["status"] not in {"pending", "approved"}:
                raise ApprovalError(_not_pending_message(row["status"]))
            if datetime.fromisoformat(row["expires_at"]) <= _now():
                db.execute(
                    "UPDATE action_approvals SET status = 'expired', card_json = NULL WHERE approval_id = ?",
                    (approval_id,),
                )
                expired = True
            else:
                db.execute(
                    """
                    UPDATE action_approvals
                    SET status = 'rejected', card_json = NULL
                    WHERE approval_id = ? AND status IN ('pending', 'approved')
                    """,
                    (approval_id,),
                )
                expired = False
        if expired:
            raise ApprovalError("Approval has expired")
        return {"approval_id": approval_id, "status": "rejected", "summary": row["summary"]}

    def consume(
        self,
        *,
        approval_id: str,
        action_id: str,
        payload: dict[str, Any],
    ) -> None:
        now = _now()
        now_text = now.isoformat()
        expired = False
        with self._connection() as db:
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
                expired = True
            elif row["action_id"] != action_id:
                raise ApprovalError("Approval action does not match")
            elif row["payload_sha256"] != _digest(action_id, payload):
                raise ApprovalError("Approved payload has changed")
            else:
                updated = db.execute(
                    """
                    UPDATE action_approvals
                    SET status = 'consumed', consumed_at = ?, card_json = NULL
                    WHERE approval_id = ? AND status = 'approved'
                    """,
                    (now_text, approval_id),
                )
                if updated.rowcount != 1:
                    raise ApprovalError("Approval was already consumed")
        if expired:
            raise ApprovalError("Approval has expired")

    def save_card(self, approval_id: str, card: dict[str, Any]) -> bool:
        """Remember the exact action shown for a pending approval so it survives a page reload.

        The card is dropped as soon as the approval is used, rejected or expired. Cards over
        64 KB keep their summary and display text but not the (large) payload, and are marked
        ``restorable: false`` because the exact payload is needed to execute.
        """
        keep = {
            "kind": str(card.get("kind", ""))[:80],
            "summary": str(card.get("summary", ""))[:500],
            "display": card.get("display") if isinstance(card.get("display"), dict) else {},
            "payload": card.get("payload") if isinstance(card.get("payload"), dict) else {},
            "restorable": True,
        }
        text = json.dumps(keep, ensure_ascii=False, default=str)
        if len(text.encode("utf-8")) > MAX_CARD_BYTES:
            keep["payload"] = {}
            keep["restorable"] = False
            text = json.dumps(keep, ensure_ascii=False, default=str)
        with self._connection() as db:
            updated = db.execute(
                "UPDATE action_approvals SET card_json = ? WHERE approval_id = ? AND status = 'pending'",
                (text, approval_id),
            )
            return updated.rowcount == 1

    def expires_at(self, approval_id: str) -> str | None:
        with self._connection() as db:
            row = db.execute("SELECT expires_at FROM action_approvals WHERE approval_id = ?",
                             (approval_id,)).fetchone()
        return str(row["expires_at"]) if row else None

    def pending_cards(self) -> list[dict[str, Any]]:
        """Cards for approvals still waiting for the owner, oldest first; stale cards are purged."""
        now = _now().isoformat()
        with self._connection() as db:
            db.execute(
                "UPDATE action_approvals SET card_json = NULL WHERE card_json IS NOT NULL "
                "AND (status != 'pending' OR expires_at <= ?)",
                (now,),
            )
            rows = db.execute(
                "SELECT approval_id, summary, created_at, expires_at, card_json FROM action_approvals "
                "WHERE status = 'pending' AND card_json IS NOT NULL AND expires_at > ? ORDER BY created_at",
                (now,),
            ).fetchall()
        cards = []
        for row in rows:
            card = json.loads(row["card_json"])
            cards.append({**card, "approval_id": row["approval_id"], "created_at": row["created_at"],
                          "expires_at": row["expires_at"]})
        return cards

    def release(self, approval_id: str) -> bool:
        """Return an approved-but-unused approval to ``pending`` after a failed attempt.

        Only an ``approved`` (not consumed, not expired) approval can be released, so a spent
        approval can never be revived. The owner can then retry or reject it.
        """
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            updated = db.execute(
                "UPDATE action_approvals SET status = 'pending', approved_at = NULL "
                "WHERE approval_id = ? AND status = 'approved' AND expires_at > ?",
                (approval_id, _now().isoformat()),
            )
            return updated.rowcount == 1

    def status(self, approval_id: str) -> str | None:
        """Current state of one approval (pending/approved/rejected/consumed/expired) or None."""
        with self._connection() as db:
            row = db.execute(
                "SELECT status, expires_at FROM action_approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
        if not row:
            return None
        if row["status"] in {"pending", "approved"} and row["expires_at"] <= _now().isoformat():
            return "expired"
        return str(row["status"])

    def list_pending(self) -> list[dict[str, str]]:
        with self._connection() as db:
            rows = db.execute("""
                SELECT approval_id, action_id, summary, status, created_at, expires_at
                FROM action_approvals
                WHERE status IN ('pending', 'approved')
                ORDER BY created_at
            """).fetchall()
        return [dict(row) for row in rows]
