"""Persistent model registry with capability provenance and owner configuration.

Discovery output (``ModelCandidate``) is rebuilt on every refresh and carries no
memory. This registry remembers what Mia has been told or has observed about each
model, and keeps the owner's choices (enabled, alias, preference) across refreshes.

Every capability fact has a source, kept separate and never merged away:

- ``reported``   the provider's own metadata (can be wrong or optimistic);
- ``configured`` the owner declared it;
- ``tested``     Mia exercised it at runtime and recorded the result;
- ``unknown``    nothing is known; an unknown capability is never advertised.

Precedence for the effective answer is tested > configured > reported. A model
removed from a provider's listing is marked stale, not deleted, so aliases and
preferences survive a provider that is temporarily missing a model.
No credentials are stored here.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import ModelCandidate

SOURCES = ("tested", "configured", "reported")  # precedence order, strongest first
_ALIAS = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_CAPABILITY = re.compile(r"[a-z][a-z0-9_]{0,31}")
_NAME = re.compile(r"[^\x00-\x1f\x7f]{1,200}")
_MAX_CONTEXT = 100_000_000


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ModelRegistryError(ValueError):
    """Invalid registry input (bad alias, capability, unknown model, ...)."""


@dataclass(frozen=True, slots=True)
class ModelRecord:
    provider_id: str
    model_id: str
    display_name: str
    family: str | None
    context_window: int
    local: bool
    free_tier: bool
    enabled: bool
    stale: bool
    manual: bool
    alias: str | None
    preference: int
    first_seen: str
    last_seen: str
    capabilities: dict[str, dict[str, str]]  # capability -> {source: supported|unsupported}

    @property
    def key(self) -> str:
        return f"{self.provider_id}/{self.model_id}"

    def effective_capabilities(self) -> frozenset[str]:
        return frozenset(
            name for name, by_source in self.capabilities.items() if _effective(by_source)
        )

    def verification(self) -> str:
        """Strongest evidence on record for the model as a whole."""
        for source in SOURCES:
            if any(source in by_source for by_source in self.capabilities.values()):
                return source
        return "unknown"


def _effective(by_source: dict[str, str]) -> bool:
    for source in SOURCES:
        if source in by_source:
            return by_source[source] == "supported"
    return False  # unknown: never advertised


class ModelRegistry:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS models (
                    provider_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    family TEXT,
                    context_window INTEGER NOT NULL,
                    local INTEGER NOT NULL,
                    free_tier INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    stale INTEGER NOT NULL DEFAULT 0,
                    manual INTEGER NOT NULL DEFAULT 0,
                    alias TEXT UNIQUE,
                    preference INTEGER NOT NULL DEFAULT 0,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    PRIMARY KEY (provider_id, model_id)
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS capabilities (
                    provider_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    source TEXT NOT NULL CHECK (source IN ('reported','configured','tested')),
                    status TEXT NOT NULL CHECK (status IN ('supported','unsupported')),
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (provider_id, model_id, capability, source),
                    FOREIGN KEY (provider_id, model_id)
                        REFERENCES models(provider_id, model_id) ON DELETE CASCADE
                )
                """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    # ---- writes -----------------------------------------------------------------

    def sync_discovered(
        self,
        candidates: Iterable[ModelCandidate],
        listed_providers: Iterable[str],
    ) -> None:
        """Record a discovery pass.

        ``listed_providers`` are the providers whose listing *succeeded*. Only
        their absent models become stale; a provider that was offline keeps its
        models as they were. Owner settings are never overwritten.
        """
        now = _now()
        seen: dict[str, set[str]] = {}
        with self._connection() as db:
            for c in candidates:
                seen.setdefault(c.provider_id, set()).add(c.model_id)
                db.execute(
                    """
                    INSERT INTO models (provider_id, model_id, display_name, context_window,
                        local, free_tier, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider_id, model_id) DO UPDATE SET
                        context_window = excluded.context_window,
                        local = excluded.local,
                        free_tier = excluded.free_tier,
                        stale = 0,
                        last_seen = excluded.last_seen
                    """,
                    (
                        c.provider_id,
                        c.model_id,
                        c.model_id,
                        max(0, int(c.context_window)),
                        int(c.data_local),
                        int(c.free_tier),
                        now,
                        now,
                    ),
                )
                # Provider-reported evidence is replaced wholesale on each sync.
                db.execute(
                    "DELETE FROM capabilities WHERE provider_id=? AND model_id=? "
                    "AND source='reported'",
                    (c.provider_id, c.model_id),
                )
                for capability in c.capabilities:
                    if _CAPABILITY.fullmatch(capability):
                        db.execute(
                            "INSERT INTO capabilities VALUES (?, ?, ?, 'reported', "
                            "'supported', ?)",
                            (c.provider_id, c.model_id, capability, now),
                        )
            for provider in set(listed_providers):
                present = seen.get(provider, set())
                rows = db.execute(
                    "SELECT model_id FROM models WHERE provider_id=? AND manual=0 AND stale=0",
                    (provider,),
                ).fetchall()
                for row in rows:
                    if row["model_id"] not in present:
                        db.execute(
                            "UPDATE models SET stale=1 WHERE provider_id=? AND model_id=?",
                            (provider, row["model_id"]),
                        )

    def register_manual(
        self,
        provider_id: str,
        model_id: str,
        *,
        capabilities: Iterable[str],
        context_window: int,
        display_name: str | None = None,
        family: str | None = None,
        local: bool = False,
        free_tier: bool = False,
    ) -> None:
        """Register a model the provider does not list; capabilities are 'configured'."""
        provider_id, model_id = provider_id.strip().lower(), model_id.strip()
        if not provider_id or not model_id or len(model_id) > 200:
            raise ModelRegistryError("provider and model id are required")
        if not isinstance(context_window, int) or not 0 < context_window <= _MAX_CONTEXT:
            raise ModelRegistryError("context_window must be a positive integer")
        name = display_name or model_id
        if not _NAME.fullmatch(name):
            raise ModelRegistryError("invalid display name")
        caps = list(capabilities)
        for capability in caps:
            if not _CAPABILITY.fullmatch(capability):
                raise ModelRegistryError(f"invalid capability: {capability!r}")
        now = _now()
        with self._connection() as db:
            db.execute(
                """
                INSERT INTO models (provider_id, model_id, display_name, family, context_window,
                    local, free_tier, manual, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(provider_id, model_id) DO UPDATE SET
                    display_name = excluded.display_name, family = excluded.family,
                    context_window = excluded.context_window, local = excluded.local,
                    free_tier = excluded.free_tier, manual = 1, stale = 0,
                    last_seen = excluded.last_seen
                """,
                (provider_id, model_id, name, family, context_window, int(local),
                 int(free_tier), now, now),
            )
            db.execute(
                "DELETE FROM capabilities WHERE provider_id=? AND model_id=? "
                "AND source='configured'",
                (provider_id, model_id),
            )
            for capability in caps:
                db.execute(
                    "INSERT INTO capabilities VALUES (?, ?, ?, 'configured', 'supported', ?)",
                    (provider_id, model_id, capability, now),
                )

    def set_capability(
        self,
        provider_id: str,
        model_id: str,
        capability: str,
        supported: bool,
        source: str,
    ) -> None:
        """Record evidence; ``source`` is 'configured' (owner) or 'tested' (runtime test)."""
        if source not in ("configured", "tested"):
            raise ModelRegistryError("source must be 'configured' or 'tested'")
        if not _CAPABILITY.fullmatch(capability):
            raise ModelRegistryError(f"invalid capability: {capability!r}")
        self._require(provider_id, model_id)
        with self._connection() as db:
            db.execute(
                "INSERT INTO capabilities VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(provider_id, model_id, capability, source) DO UPDATE SET "
                "status=excluded.status, recorded_at=excluded.recorded_at",
                (provider_id.lower(), model_id, capability, source,
                 "supported" if supported else "unsupported", _now()),
            )

    def set_enabled(self, provider_id: str, model_id: str, enabled: bool) -> None:
        self._update(provider_id, model_id, "enabled", int(bool(enabled)))

    def set_preference(self, provider_id: str, model_id: str, preference: int) -> None:
        if isinstance(preference, bool) or not isinstance(preference, int):
            raise ModelRegistryError("preference must be an integer from -10 to 10")
        if not -10 <= preference <= 10:
            raise ModelRegistryError("preference must be an integer from -10 to 10")
        self._update(provider_id, model_id, "preference", preference)

    def set_alias(self, provider_id: str, model_id: str, alias: str | None) -> None:
        if alias is not None:
            alias = alias.strip().lower()
            if not _ALIAS.fullmatch(alias):
                raise ModelRegistryError("alias must be 1-64 chars of a-z, 0-9, '.', '_', '-'")
        try:
            self._update(provider_id, model_id, "alias", alias)
        except sqlite3.IntegrityError as exc:
            raise ModelRegistryError("alias is already in use") from exc

    def remove(self, provider_id: str, model_id: str) -> None:
        """Delete a *stale or manual* model; live discovered models cannot be removed."""
        record = self.get(provider_id, model_id)
        if record is None:
            raise ModelRegistryError("unknown model")
        if not (record.stale or record.manual):
            raise ModelRegistryError("only stale or manually registered models can be removed")
        with self._connection() as db:
            db.execute(
                "DELETE FROM models WHERE provider_id=? AND model_id=?",
                (provider_id.lower(), model_id),
            )

    def _update(self, provider_id: str, model_id: str, column: str, value: Any) -> None:
        self._require(provider_id, model_id)
        with self._connection() as db:
            db.execute(
                f"UPDATE models SET {column}=? WHERE provider_id=? AND model_id=?",  # noqa: S608
                (value, provider_id.lower(), model_id),
            )

    def _require(self, provider_id: str, model_id: str) -> None:
        if self.get(provider_id, model_id) is None:
            raise ModelRegistryError("unknown model")

    # ---- reads ------------------------------------------------------------------

    def get(self, provider_id: str, model_id: str) -> ModelRecord | None:
        for record in self.list(include_stale=True):
            if record.provider_id == provider_id.lower() and record.model_id == model_id:
                return record
        return None

    def resolve(self, name: str) -> ModelRecord | None:
        """Find a model by alias or exact ``provider/model``; None if absent or ambiguous."""
        name = name.strip()
        records = self.list(include_stale=True)
        for record in records:
            if record.alias and record.alias == name.lower():
                return record
        matches = [r for r in records if r.key == name or r.key == name.lower()]
        return matches[0] if len(matches) == 1 else None

    def list(self, *, include_stale: bool = False) -> list[ModelRecord]:
        with self._connection() as db:
            models = db.execute("SELECT * FROM models ORDER BY provider_id, model_id").fetchall()
            caps = db.execute("SELECT * FROM capabilities").fetchall()
        grouped: dict[tuple[str, str], dict[str, dict[str, str]]] = {}
        for row in caps:
            grouped.setdefault((row["provider_id"], row["model_id"]), {}).setdefault(
                row["capability"], {}
            )[row["source"]] = row["status"]
        records = []
        for row in models:
            if row["stale"] and not include_stale:
                continue
            records.append(
                ModelRecord(
                    provider_id=row["provider_id"],
                    model_id=row["model_id"],
                    display_name=row["display_name"],
                    family=row["family"],
                    context_window=row["context_window"],
                    local=bool(row["local"]),
                    free_tier=bool(row["free_tier"]),
                    enabled=bool(row["enabled"]),
                    stale=bool(row["stale"]),
                    manual=bool(row["manual"]),
                    alias=row["alias"],
                    preference=row["preference"],
                    first_seen=row["first_seen"],
                    last_seen=row["last_seen"],
                    capabilities=grouped.get((row["provider_id"], row["model_id"]), {}),
                )
            )
        return records

    # ---- bridge to routing ------------------------------------------------------

    def apply(
        self,
        candidates: Iterable[ModelCandidate],
        *,
        registered_providers: Iterable[str] | None = None,
    ) -> list[ModelCandidate]:
        """Turn discovery output into routable candidates.

        Disabled and stale models are dropped. Capabilities become the effective
        set (tested > configured > reported). Manual models whose provider is
        registered are added even if the provider does not list them.
        """
        records = {r.key: r for r in self.list(include_stale=True)}
        out: list[ModelCandidate] = []
        emitted: set[str] = set()
        for c in candidates:
            key = f"{c.provider_id}/{c.model_id}"
            record = records.get(key)
            if record is None:
                out.append(c)
            elif record.enabled and not record.stale:
                out.append(replace(c, capabilities=record.effective_capabilities()))
            emitted.add(key)
        allowed = None if registered_providers is None else set(registered_providers)
        for key, record in records.items():
            if key in emitted or not record.manual or not record.enabled:
                continue
            if allowed is not None and record.provider_id not in allowed:
                continue
            out.append(
                ModelCandidate(
                    provider_id=record.provider_id,
                    model_id=record.model_id,
                    capabilities=record.effective_capabilities(),
                    context_window=record.context_window,
                    free_tier=record.free_tier,
                    healthy=True,
                    enabled=True,
                    data_local=record.local,
                )
            )
        return out
