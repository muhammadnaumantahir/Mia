"""Owner-controlled memory settings (consent, capture mode, retrieval, retention).

Settings are stored in the memory database so they survive restarts and apply the
same way whichever model answers. Unknown keys and wrongly-typed values are
rejected. Every change is audited with the old and new value (settings hold no
personal content).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any

from mia.memory.schema import AuditLog

CAPTURE_MODES = ("off", "approval", "auto")

DEFAULT_SETTINGS: dict[str, Any] = {
    # Master switch. When false Mia neither saves nor retrieves memory.
    "memory_enabled": True,
    # Governs AUTOMATIC capture only. Explicit "remember this" always works while
    # memory is enabled and the conversation is not temporary.
    #   off      - never capture automatically
    #   approval - propose suggestions; nothing is used until the owner approves
    #   auto     - save high-confidence suggestions directly, queue the rest
    "capture_mode": "approval",
    "retrieval_enabled": True,
    # In auto mode, suggestions below this confidence go to the approval queue.
    "auto_save_min_confidence": 0.8,
    # Unreviewed suggestions expire after this many days.
    "pending_retention_days": 30,
    # Advisory retention for conversation history (None keeps it). Applied only
    # when the owner runs the retention action; nothing is deleted automatically.
    "conversation_retention_days": None,
    # Advisory retention for *retired* structured records (superseded, outdated,
    # archived, rejected, expired). None keeps them. Live records are never covered,
    # and nothing is deleted until the owner runs the retention action.
    "structured_retention_days": None,
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def validate_setting(key: str, value: Any) -> Any:
    if key not in DEFAULT_SETTINGS:
        raise ValueError(f"unknown memory setting: {key}")
    if key in {"memory_enabled", "retrieval_enabled"}:
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
        return value
    if key == "capture_mode":
        if value not in CAPTURE_MODES:
            raise ValueError("capture_mode must be one of: " + ", ".join(CAPTURE_MODES))
        return value
    if key == "auto_save_min_confidence":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("auto_save_min_confidence must be a number")
        if not 0.5 <= float(value) <= 1.0:
            raise ValueError("auto_save_min_confidence must be between 0.5 and 1")
        return float(value)
    if key == "pending_retention_days":
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 365:
            raise ValueError("pending_retention_days must be a whole number from 1 to 365")
        return value
    if key == "conversation_retention_days":
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 36500:
            raise ValueError("conversation_retention_days must be null or 1 to 36500")
        return value
    if key == "structured_retention_days":
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 36500:
            raise ValueError("structured_retention_days must be null or 1 to 36500")
        return value
    raise ValueError(f"unknown memory setting: {key}")  # pragma: no cover


class SettingsStore:
    def __init__(
        self, connect: Callable[[], AbstractContextManager[sqlite3.Connection]]
    ) -> None:
        self._connect = connect

    def get_all(self) -> dict[str, Any]:
        with self._connect() as db:
            rows = db.execute("SELECT key, value_json FROM memory_settings").fetchall()
        values = dict(DEFAULT_SETTINGS)
        for row in rows:
            if row["key"] in DEFAULT_SETTINGS:
                try:
                    values[row["key"]] = validate_setting(row["key"], json.loads(row["value_json"]))
                except (ValueError, json.JSONDecodeError):
                    continue  # a corrupt stored value falls back to the safe default
        return values

    def get(self, key: str) -> Any:
        return self.get_all()[key]

    def update(self, changes: dict[str, Any], *, actor: str = "owner") -> dict[str, Any]:
        if not isinstance(changes, dict) or not changes:
            raise ValueError("provide at least one setting to change")
        clean = {key: validate_setting(key, value) for key, value in changes.items()}
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for key, value in clean.items():
                row = db.execute(
                    "SELECT value_json FROM memory_settings WHERE key = ?", (key,)
                ).fetchone()
                old = json.loads(row["value_json"]) if row else DEFAULT_SETTINGS[key]
                if row and old == value:
                    continue
                db.execute(
                    """
                    INSERT INTO memory_settings (key, value_json, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE
                        SET value_json = excluded.value_json, updated_at = excluded.updated_at
                    """,
                    (key, json.dumps(value), _now()),
                )
                AuditLog.write(
                    db,
                    actor=actor,
                    action="setting_changed",
                    target_type="memory_setting",
                    target_id=key,
                    detail={"from": old, "to": value},
                )
        return self.get_all()
