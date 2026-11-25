"""Versioned, device-scoped companion state sync (MIA-LIFE-13).

This store deliberately syncs only small, non-secret companion state/preferences. It does not
expose Mia's conversations, files, personal memory, credentials or governance records.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from mia.memory.safety import reject_secret

SYNC_NAMESPACES = frozenset({"companion_state", "preferences"})
MAX_BATCH_ITEMS = 50
MAX_PULL_ITEMS = 100
MAX_PAYLOAD_BYTES = 16_384
MAX_PAYLOAD_DEPTH = 8
MAX_PAYLOAD_NODES = 500
MAX_SYNC_ITEMS = 5_000
MAX_SYNC_CHANGES = 100_000
MAX_BATCHES_PER_DEVICE = 1_000
BATCH_RETENTION_DAYS = 30
_FORBIDDEN_KEYS = frozenset({
    "password", "passwd", "secret", "token", "access_token", "refresh_token",
    "api_key", "private_key", "client_secret", "authorization", "cookie",
})


class CompanionSyncError(ValueError):
    """Invalid or unsafe companion sync request."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean_id(value: str, *, field: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(rf"[A-Za-z0-9][A-Za-z0-9._:-]{{0,{maximum - 1}}}", text):
        raise CompanionSyncError(f"{field} has an invalid format")
    return text


def _clean_payload(value: Any) -> str:
    if not isinstance(value, dict):
        raise CompanionSyncError("payload must be a JSON object")
    nodes = 0

    def walk(item: Any, depth: int = 0) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_PAYLOAD_NODES or depth > MAX_PAYLOAD_DEPTH:
            raise CompanionSyncError("payload exceeds the supported structure limits")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or not key or len(key) > 80:
                    raise CompanionSyncError("payload keys must be short strings")
                if key.strip().casefold() in _FORBIDDEN_KEYS:
                    raise CompanionSyncError("payload contains a credential-like field")
                walk(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 100:
                raise CompanionSyncError("payload arrays are limited to 100 items")
            for child in item:
                walk(child, depth + 1)
        elif isinstance(item, str):
            if len(item) > 4096:
                raise CompanionSyncError("payload strings are limited to 4096 characters")
            reject_secret(item, field="sync payload")
        elif item is not None and not isinstance(item, (bool, int, float)):
            raise CompanionSyncError("payload contains an unsupported value")
        elif isinstance(item, float) and (item != item or abs(item) == float("inf")):
            raise CompanionSyncError("payload numbers must be finite")

    walk(value)
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CompanionSyncError("payload must be valid JSON") from exc
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise CompanionSyncError(f"payload is limited to {MAX_PAYLOAD_BYTES} bytes")
    reject_secret(encoded, field="sync payload")
    return encoded


class CompanionSyncStore:
    """Atomic optimistic-concurrency sync log shared by owner-paired devices."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sync_items (
                    namespace TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload_json TEXT,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    payload_sha256 TEXT,
                    updated_by_device TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(namespace, item_id)
                );
                CREATE TABLE IF NOT EXISTS sync_changes (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    namespace TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload_json TEXT,
                    deleted INTEGER NOT NULL,
                    payload_sha256 TEXT,
                    device_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sync_changes_seq ON sync_changes(seq);
                CREATE TABLE IF NOT EXISTS sync_batches (
                    device_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(device_id, batch_id)
                );
                CREATE TABLE IF NOT EXISTS sync_cursors (
                    device_id TEXT PRIMARY KEY,
                    last_seq INTEGER NOT NULL DEFAULT 0,
                    last_seen_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _clean_change(change: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(change, dict):
            raise CompanionSyncError("each sync change must be an object")
        allowed = {"item_id", "namespace", "base_version", "payload", "deleted"}
        if set(change) - allowed:
            raise CompanionSyncError("sync change contains unsupported fields")
        item_id = _clean_id(change.get("item_id"), field="item_id", maximum=80)
        namespace = change.get("namespace")
        if not isinstance(namespace, str) or namespace not in SYNC_NAMESPACES:
            raise CompanionSyncError("namespace is not enabled for companion sync")
        base_version = change.get("base_version")
        if isinstance(base_version, bool) or not isinstance(base_version, int) or base_version < 0:
            raise CompanionSyncError("base_version must be a non-negative integer")
        deleted = change.get("deleted", False)
        if not isinstance(deleted, bool):
            raise CompanionSyncError("deleted must be a boolean")
        if deleted:
            if change.get("payload") is not None:
                raise CompanionSyncError("deleted sync items cannot include a payload")
            payload_json = None
            payload_hash = None
        else:
            payload_json = _clean_payload(change.get("payload"))
            payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        return {
            "item_id": item_id,
            "namespace": namespace,
            "base_version": base_version,
            "payload_json": payload_json,
            "payload_sha256": payload_hash,
            "deleted": deleted,
        }

    def push(self, device_id: str, batch_id: str, changes: list[dict[str, Any]]) -> dict[str, Any]:
        device = _clean_id(device_id, field="device_id", maximum=64)
        batch = _clean_id(batch_id, field="batch_id", maximum=64)
        if len(batch) < 16:
            raise CompanionSyncError("batch_id must contain at least 16 characters")
        if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_BATCH_ITEMS:
            raise CompanionSyncError(f"a sync batch must contain 1 to {MAX_BATCH_ITEMS} changes")
        cleaned = [self._clean_change(change) for change in changes]
        keys = [(item["namespace"], item["item_id"]) for item in cleaned]
        if len(keys) != len(set(keys)):
            raise CompanionSyncError("a sync batch cannot change the same item twice")

        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            now = _now()
            cutoff = (datetime.now(UTC) - timedelta(days=BATCH_RETENTION_DAYS)).isoformat()
            db.execute("DELETE FROM sync_batches WHERE created_at < ?", (cutoff,))
            prior = db.execute(
                "SELECT result_json FROM sync_batches WHERE device_id=? AND batch_id=?",
                (device, batch),
            ).fetchone()
            if prior:
                result = json.loads(prior["result_json"])
                result["replayed"] = True
                return result
            batch_count = int(db.execute(
                "SELECT COUNT(*) AS n FROM sync_batches WHERE device_id=?", (device,)
            ).fetchone()["n"])
            if batch_count >= MAX_BATCHES_PER_DEVICE:
                raise CompanionSyncError("device sync batch retention limit reached")
            change_count = int(db.execute(
                "SELECT COUNT(*) AS n FROM sync_changes"
            ).fetchone()["n"])
            if change_count + len(cleaned) > MAX_SYNC_CHANGES:
                raise CompanionSyncError("sync history limit reached; owner maintenance is required")

            conflicts: list[dict[str, Any]] = []
            current_rows: dict[tuple[str, str], sqlite3.Row | None] = {}
            for change in cleaned:
                key = (change["namespace"], change["item_id"])
                row = db.execute(
                    "SELECT * FROM sync_items WHERE namespace=? AND item_id=?", key
                ).fetchone()
                current_rows[key] = row
                current_version = int(row["version"]) if row else 0
                if change["base_version"] != current_version:
                    conflicts.append({
                        "namespace": change["namespace"],
                        "item_id": change["item_id"],
                        "expected_base_version": change["base_version"],
                        "current_version": current_version,
                        "current_deleted": bool(row["deleted"]) if row else False,
                    })
            if conflicts:
                result = {"status": "conflict", "batch_id": batch, "conflicts": conflicts, "items": []}
                db.execute(
                    "INSERT INTO sync_batches VALUES (?,?,?,?)",
                    (device, batch, json.dumps(result, separators=(",", ":")), _now()),
                )
                return result

            current_count = int(db.execute("SELECT COUNT(*) AS n FROM sync_items").fetchone()["n"])
            new_items = sum(1 for row in current_rows.values() if row is None)
            if current_count + new_items > MAX_SYNC_ITEMS:
                raise CompanionSyncError("sync item limit reached; owner maintenance is required")

            applied: list[dict[str, Any]] = []
            next_cursor = 0
            for change in cleaned:
                key = (change["namespace"], change["item_id"])
                previous = current_rows[key]
                version = (int(previous["version"]) if previous else 0) + 1
                db.execute(
                    """
                    INSERT INTO sync_items (
                        namespace,item_id,version,payload_json,deleted,payload_sha256,
                        updated_by_device,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(namespace,item_id) DO UPDATE SET
                        version=excluded.version,payload_json=excluded.payload_json,
                        deleted=excluded.deleted,payload_sha256=excluded.payload_sha256,
                        updated_by_device=excluded.updated_by_device,updated_at=excluded.updated_at
                    """,
                    (change["namespace"], change["item_id"], version, change["payload_json"],
                     int(change["deleted"]), change["payload_sha256"], device, now),
                )
                cursor = db.execute(
                    """
                    INSERT INTO sync_changes (
                        namespace,item_id,version,payload_json,deleted,payload_sha256,device_id,created_at
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (change["namespace"], change["item_id"], version, change["payload_json"],
                     int(change["deleted"]), change["payload_sha256"], device, now),
                ).lastrowid
                next_cursor = max(next_cursor, int(cursor))
                applied.append({
                    "namespace": change["namespace"],
                    "item_id": change["item_id"],
                    "version": version,
                    "deleted": change["deleted"],
                    "seq": int(cursor),
                })
            result = {
                "status": "applied",
                "batch_id": batch,
                "items": applied,
                "next_cursor": next_cursor,
                "replayed": False,
            }
            db.execute(
                "INSERT INTO sync_batches VALUES (?,?,?,?)",
                (device, batch, json.dumps(result, separators=(",", ":")), now),
            )
            return result

    def pull(self, device_id: str, *, since: int = 0, limit: int = MAX_PULL_ITEMS) -> dict[str, Any]:
        device = _clean_id(device_id, field="device_id", maximum=64)
        if isinstance(since, bool) or not isinstance(since, int) or since < 0:
            raise CompanionSyncError("since must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PULL_ITEMS:
            raise CompanionSyncError(f"limit must be from 1 to {MAX_PULL_ITEMS}")
        with self._db() as db:
            maximum = int(db.execute("SELECT COALESCE(MAX(seq),0) AS seq FROM sync_changes").fetchone()["seq"])
            if since > maximum:
                raise CompanionSyncError("sync cursor is ahead of the server")
            rows = db.execute(
                "SELECT * FROM sync_changes WHERE seq>? ORDER BY seq LIMIT ?",
                (since, limit),
            ).fetchall()
            changes = []
            for row in rows:
                changes.append({
                    "seq": int(row["seq"]),
                    "namespace": row["namespace"],
                    "item_id": row["item_id"],
                    "version": int(row["version"]),
                    "payload": json.loads(row["payload_json"]) if row["payload_json"] else None,
                    "deleted": bool(row["deleted"]),
                    "payload_sha256": row["payload_sha256"],
                    "device_id": row["device_id"],
                    "created_at": row["created_at"],
                })
            next_cursor = int(rows[-1]["seq"]) if rows else since
            db.execute(
                """
                INSERT INTO sync_cursors(device_id,last_seq,last_seen_at) VALUES (?,?,?)
                ON CONFLICT(device_id) DO UPDATE SET
                    last_seq=MAX(sync_cursors.last_seq,excluded.last_seq),
                    last_seen_at=excluded.last_seen_at
                """,
                (device, next_cursor, _now()),
            )
            return {"changes": changes, "next_cursor": next_cursor, "has_more": next_cursor < maximum}
