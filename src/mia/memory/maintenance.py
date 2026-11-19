"""Memory maintenance: find what needs the owner's attention, apply only safe sweeps.

Automatic (when ``apply`` is true): expire temporary memories past their expiry and
suggestions the owner never reviewed. Everything else is REPORTED, never changed:

- duplicate clusters (consolidation needs the owner's approval; it keeps both texts),
- conflicting records (same subject or similar wording but different content),
- possibly stale goals and projects (not updated or used for a long time).

Mia never decides which of two conflicting memories is true, and never marks a
decision outdated because time passed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mia.memory.capture import CONFLICT_SIMILARITY, DUPLICATE_SIMILARITY, tokens
from mia.memory.structured import normalise_title

MAX_PER_TYPE = 400
MAX_CONFLICTS = 50
STALE_TYPES = ("project", "goal")
_TRUST_ORDER = {"owner_provided": 0, "validated": 1, "observed": 2, "source_backed": 3,
                "inferred": 4, "hypothesis": 5}


def _age_days(timestamp: str | None, now: datetime) -> float | None:
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, (now - parsed).total_seconds() / 86400)


class MaintenanceService:
    def __init__(self, memory: Any) -> None:
        self.memory = memory

    def run(
        self, *, apply: bool = False, stale_days: int = 180, now: datetime | None = None
    ) -> dict[str, Any]:
        if stale_days < 1:
            raise ValueError("stale_days must be at least 1")
        now = now or datetime.now(UTC)
        structured = self.memory.structured
        retention = int(self.memory.settings.get("pending_retention_days"))
        due = structured.due_for_expiry(now=now.isoformat())
        stale_pending = structured.pending_older_than(retention)
        if apply:
            due = structured.expire_due(now=now.isoformat())
            stale_pending = structured.expire_pending(older_than_days=retention)
        duplicates, conflicts, truncated = self._analyse()
        return {
            "applied": apply,
            "expired_temporary": due,
            "expired_pending": stale_pending,
            "duplicates": duplicates,
            "conflicts": conflicts,
            "possibly_stale": self._stale(stale_days, now),
            "truncated": truncated,
        }

    def consolidate(self, keep_id: str, merge_ids: list[str], *, actor: str = "owner") -> list[str]:
        """Owner-approved merge of duplicates into one kept record."""
        if not merge_ids:
            raise ValueError("choose at least one record to merge")
        merged: list[str] = []
        for duplicate_id in merge_ids:
            self.memory.structured.merge_into(duplicate_id, keep_id, actor=actor)
            merged.append(duplicate_id)
        return merged

    # ---------------------------------------------------------------- analysis

    def _active_by_type(self) -> tuple[dict[str, list[dict[str, Any]]], bool]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        truncated = False
        for status in ("active", "completed"):
            for record in self.memory.structured.list(status=status, limit=500):
                grouped.setdefault(record["record_type"], []).append(record)
        for record_type, rows in grouped.items():
            if len(rows) > MAX_PER_TYPE:
                grouped[record_type] = rows[:MAX_PER_TYPE]
                truncated = True
        return grouped, truncated

    def _analyse(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        grouped, truncated = self._active_by_type()
        duplicates: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        for record_type, rows in grouped.items():
            toks = {r["record_id"]: tokens(f"{r['body']}") for r in rows}
            keys = {r["record_id"]: normalise_title(r["title"]) for r in rows}
            parent: dict[str, str] = {}

            def find(x: str, parent: dict[str, str] = parent) -> str:
                while parent.get(x, x) != x:
                    x = parent[x]
                return x

            related: list[tuple[dict[str, Any], dict[str, Any], float]] = []
            for index, a in enumerate(rows):
                for b in rows[index + 1:]:
                    left, right = toks[a["record_id"]], toks[b["record_id"]]
                    sim = len(left & right) / len(left | right) if left and right else 0.0
                    if sim >= DUPLICATE_SIMILARITY:
                        parent[find(b["record_id"])] = find(a["record_id"])
                        continue
                    same_subject = keys[a["record_id"]] == keys[b["record_id"]]
                    if (same_subject or sim >= CONFLICT_SIMILARITY) and len(conflicts) < MAX_CONFLICTS:
                        related.append((a, b, sim))
            clusters: dict[str, list[dict[str, Any]]] = {}
            for r in rows:
                clusters.setdefault(find(r["record_id"]), []).append(r)
            for members in clusters.values():
                if len(members) < 2:
                    continue
                members.sort(key=lambda r: (_TRUST_ORDER.get(r["provenance"], 9),
                                            -float(r["importance"]), r["created_at"]))
                duplicates.append({
                    "record_type": record_type,
                    "suggested_keep": members[0]["record_id"],
                    "duplicates": [m["record_id"] for m in members[1:]],
                    "titles": [m["title"] for m in members],
                })
            dup_pairs = {frozenset((m["record_id"], n["record_id"]))
                         for c in clusters.values() for m in c for n in c if m is not n}
            for a, b, sim in related:
                if frozenset((a["record_id"], b["record_id"])) in dup_pairs:
                    continue
                if a["superseded_by"] == b["record_id"] or b["superseded_by"] == a["record_id"]:
                    continue
                conflicts.append({
                    "record_type": record_type,
                    "record_ids": [a["record_id"], b["record_id"]],
                    "reason": "same_subject" if keys[a["record_id"]] == keys[b["record_id"]]
                    else "similar_wording",
                    "similarity": round(sim, 2),
                    "titles": [a["title"], b["title"]],
                })
        return duplicates, conflicts[:MAX_CONFLICTS], truncated

    def _stale(self, stale_days: int, now: datetime) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for record_type in STALE_TYPES:
            for record in self.memory.structured.list(record_type=record_type, status="active",
                                                      limit=200):
                age = _age_days(record["updated_at"], now)
                used = _age_days(record.get("last_used_at"), now)
                if age is not None and age >= stale_days and (used is None or used >= stale_days):
                    found.append({
                        "record_id": record["record_id"],
                        "record_type": record_type,
                        "title": record["title"],
                        "days_since_update": int(age),
                        "days_since_used": int(used) if used is not None else None,
                    })
        return found
