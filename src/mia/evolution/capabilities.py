"""Versioned capability persistence and validation state machine.

This module stores candidate source and validation evidence. It deliberately
does not import, execute, hot-load or activate generated Python code.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?$")
_SOURCE_KINDS = {"generated", "adapted", "reused", "composed"}
_VALIDATION_KEYS = {
    "static_checks",
    "unit_tests",
    "security_checks",
    "regression_tests",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class CapabilityLifecycleError(RuntimeError):
    """A capability transition or persistence operation is invalid."""


class CapabilityActivationBlocked(CapabilityLifecycleError):
    """Activation is disabled until a real isolated runtime has been verified."""


class CapabilityStore:
    MAX_SOURCE_BYTES = 512 * 1024

    def __init__(
        self,
        database_path: str | Path,
        workspace: str | Path = "./workspace/generated_code",
        activation_enabled: bool | None = None,
    ) -> None:
        # Fail closed: promotion needs an explicit owner opt-in
        # (MIA_CAPABILITY_ACTIVATION_ENABLED=true) or an explicit argument.
        self.activation_enabled = (
            activation_enabled
            if activation_enabled is not None
            else os.getenv("MIA_CAPABILITY_ACTIVATION_ENABLED", "false").strip().lower()
            == "true"
        )
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        if self.workspace.is_symlink():
            raise ValueError("Generated-code workspace cannot be a symlink")
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
            db.executescript("""
                CREATE TABLE IF NOT EXISTS capability_versions (
                    capability_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    name TEXT NOT NULL,
                    parent_id TEXT,
                    parent_version TEXT,
                    rationale TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    upstream_uri TEXT,
                    license_id TEXT,
                    permissions_json TEXT NOT NULL,
                    dependencies_json TEXT NOT NULL DEFAULT '[]',
                    source_sha256 TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    test_sha256 TEXT,
                    test_source_path TEXT,
                    status TEXT NOT NULL,
                    validation_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (capability_id, version)
                );

                CREATE TABLE IF NOT EXISTS capability_events (
                    event_id TEXT PRIMARY KEY,
                    capability_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY (capability_id, version)
                        REFERENCES capability_versions(capability_id, version)
                );

                CREATE INDEX IF NOT EXISTS idx_capability_versions_status
                ON capability_versions(status, updated_at DESC);

                CREATE INDEX IF NOT EXISTS idx_capability_events_lineage
                ON capability_events(capability_id, version, occurred_at);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_capability_one_active
                ON capability_versions(capability_id) WHERE status = 'active';
            """)
            columns = {
                row["name"]
                for row in db.execute("PRAGMA table_info(capability_versions)").fetchall()
            }
            if "test_sha256" not in columns:
                db.execute("ALTER TABLE capability_versions ADD COLUMN test_sha256 TEXT")
            if "test_source_path" not in columns:
                db.execute("ALTER TABLE capability_versions ADD COLUMN test_source_path TEXT")
            if "dependencies_json" not in columns:
                db.execute(
                    "ALTER TABLE capability_versions ADD COLUMN dependencies_json TEXT NOT NULL DEFAULT '[]'"
                )

    def record_candidate(
        self,
        *,
        capability_id: str,
        version: str,
        name: str,
        source_code: str,
        rationale: str,
        source_kind: str = "generated",
        parent_id: str | None = None,
        parent_version: str | None = None,
        upstream_uri: str | None = None,
        license_id: str | None = None,
        permissions: list[str] | None = None,
        dependencies: list[dict[str, str]] | None = None,
        test_source_code: str | None = None,
    ) -> dict[str, Any]:
        cap_id = capability_id.strip().lower()
        if not _CAPABILITY_ID.fullmatch(cap_id):
            raise ValueError("capability_id must be a safe lowercase identifier")
        if not _VERSION.fullmatch(version):
            raise ValueError("version must use semantic version format")
        if not name.strip() or len(name) > 120:
            raise ValueError("name must be 1-120 characters")
        if not rationale.strip() or len(rationale) > 4000:
            raise ValueError("rationale must be 1-4000 characters")
        if source_kind not in _SOURCE_KINDS:
            raise ValueError("unsupported source_kind")
        if source_kind != "generated" and (not upstream_uri or not license_id):
            raise ValueError("adapted, reused and composed code require upstream_uri and license_id")

        encoded = source_code.encode("utf-8")
        if not encoded or len(encoded) > self.MAX_SOURCE_BYTES:
            raise ValueError("source code must be 1-512 KB")
        safe_permissions = sorted(set(permissions or []))
        if any(not item or len(item) > 100 for item in safe_permissions):
            raise ValueError("permissions must be non-empty strings of at most 100 characters")
        normalized_dependencies: list[dict[str, str]] = []
        seen_dependencies: set[tuple[str, str]] = set()
        for dependency in dependencies or []:
            if not isinstance(dependency, dict) or set(dependency) != {"capability_id", "version"}:
                raise ValueError("each dependency must contain capability_id and version only")
            dep_id = dependency["capability_id"]
            dep_version = dependency["version"]
            if (
                not isinstance(dep_id, str) or not _CAPABILITY_ID.fullmatch(dep_id)
                or not isinstance(dep_version, str) or not _VERSION.fullmatch(dep_version)
            ):
                raise ValueError("dependency capability_id/version is invalid")
            if (dep_id, dep_version) == (cap_id, version):
                raise ValueError("a capability cannot depend on itself")
            if (dep_id, dep_version) in seen_dependencies:
                raise ValueError("duplicate capability dependency")
            dep = self.get(dep_id, dep_version)
            if dep is None or dep["status"] not in {"registered", "active", "deprecated"}:
                raise ValueError("dependencies must reference an existing registered capability version")
            seen_dependencies.add((dep_id, dep_version))
            normalized_dependencies.append({"capability_id": dep_id, "version": dep_version})
        normalized_dependencies.sort(key=lambda item: (item["capability_id"], item["version"]))
        source_hash = hashlib.sha256(encoded).hexdigest()
        test_encoded = test_source_code.encode("utf-8") if test_source_code is not None else None
        if test_encoded is not None and (
            not test_encoded or len(test_encoded) > self.MAX_SOURCE_BYTES
        ):
            raise ValueError("test source must be 1-512 KB")
        test_hash = hashlib.sha256(test_encoded).hexdigest() if test_encoded else None

        if (parent_id is None) != (parent_version is None):
            raise ValueError("parent_id and parent_version must be supplied together")
        if parent_id:
            parent = self.get(parent_id, parent_version or "")
            if parent is None or parent["status"] not in {"registered", "active", "deprecated"}:
                raise ValueError("parent capability version must already be registered")

        target_dir = (self.workspace / cap_id).resolve()
        try:
            target_dir.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("capability path escapes workspace") from exc
        if target_dir.is_symlink():
            raise ValueError("capability directory cannot be a symlink")
        target_dir.mkdir(parents=True, exist_ok=True)
        if target_dir.is_symlink():
            raise ValueError("capability directory cannot be a symlink")
        target_path = (target_dir / f"{version}.py").resolve()
        target_path.relative_to(self.workspace)
        if target_path.exists():
            raise FileExistsError("capability version source already exists and is immutable")
        test_target_path = (target_dir / f"{version}.test.py").resolve()
        test_target_path.relative_to(self.workspace)
        if test_target_path.exists():
            raise FileExistsError("capability test source already exists and is immutable")

        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target_dir,
            prefix=".candidate-",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(encoded)
            temp_path = Path(temp_file.name)

        test_temp_path: Path | None = None
        try:
            os.replace(temp_path, target_path)
            if test_encoded is not None:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=target_dir,
                    prefix=".candidate-test-",
                    suffix=".tmp",
                    delete=False,
                ) as test_temp_file:
                    test_temp_file.write(test_encoded)
                    test_temp_path = Path(test_temp_file.name)
                os.replace(test_temp_path, test_target_path)
        except Exception:
            target_path.unlink(missing_ok=True)
            test_target_path.unlink(missing_ok=True)
            raise
        finally:
            temp_path.unlink(missing_ok=True)
            if test_temp_path is not None:
                test_temp_path.unlink(missing_ok=True)

        now = _now()
        permissions_json = json.dumps(safe_permissions)
        with self._connection() as db:
            exists = db.execute(
                """
                SELECT 1 FROM capability_versions
                WHERE capability_id = ? AND version = ?
                """,
                (cap_id, version),
            ).fetchone()
            if exists:
                target_path.unlink(missing_ok=True)
                test_target_path.unlink(missing_ok=True)
                raise CapabilityLifecycleError("capability version is already registered")
            db.execute(
                """
                INSERT INTO capability_versions (
                    capability_id, version, name, parent_id, parent_version,
                    rationale, source_kind, upstream_uri, license_id,
                    permissions_json, dependencies_json, source_sha256, source_path,
                    test_sha256, test_source_path, status, validation_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', NULL, ?, ?)
                """,
                (
                    cap_id,
                    version,
                    name.strip(),
                    parent_id,
                    parent_version,
                    rationale,
                    source_kind,
                    upstream_uri,
                    license_id,
                    permissions_json,
                    _canonical({"items": normalized_dependencies})[9:-1],
                    source_hash,
                    str(target_path),
                    test_hash,
                    str(test_target_path) if test_hash else None,
                    now,
                    now,
                ),
            )
            self._insert_event(
                db,
                capability_id=cap_id,
                version=version,
                from_status=None,
                to_status="proposed",
                reason="Candidate source persisted without execution",
                payload={
                    "source_sha256": source_hash,
                    "test_sha256": test_hash,
                    "source_kind": source_kind,
                },
            )
        return self.get(cap_id, version) or {}

    def begin_validation(self, capability_id: str, version: str) -> dict[str, Any]:
        current = self.get(capability_id, version)
        if current is not None and current["status"] == "validating":
            return current
        return self._transition(
            capability_id,
            version,
            expected="proposed",
            new_status="validating",
            reason="Validation started in an external isolated test runner",
        )

    def record_validation(
        self,
        capability_id: str,
        version: str,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        missing = _VALIDATION_KEYS.difference(report)
        if missing or any(not isinstance(report.get(key), bool) for key in _VALIDATION_KEYS):
            raise ValueError(
                "report must contain boolean static_checks, unit_tests, "
                "security_checks and regression_tests"
            )

        evidence = report.get("evidence")
        if not isinstance(evidence, dict):
            raise ValueError("validation report must include sandbox evidence")
        if evidence.get("runner") != "docker-isolated-v1":
            raise ValueError("validation report must originate from the Docker sandbox")
        image_id = evidence.get("image_id")
        if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
            raise ValueError("validation report must include an immutable sandbox image ID")

        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT status, source_sha256, test_sha256
                FROM capability_versions
                WHERE capability_id = ? AND version = ?
                """,
                (capability_id, version),
            ).fetchone()
            if row is None:
                raise CapabilityLifecycleError("capability version not found")
            if row["status"] != "validating":
                raise CapabilityLifecycleError("capability is not awaiting validation")
            if evidence.get("source_sha256") != row["source_sha256"]:
                raise ValueError("validation evidence source hash does not match the stored candidate")
            if evidence.get("test_sha256") != row["test_sha256"]:
                raise ValueError("validation evidence test hash does not match the stored tests")
            if report["unit_tests"] and not row["test_sha256"]:
                raise ValueError("unit_tests cannot pass without immutable candidate test source")

            passed = all(report[key] for key in _VALIDATION_KEYS)
            new_status = "validated" if passed else "rejected"
            report_copy = dict(report)
            report_copy["passed"] = passed
            report_copy["recorded_at"] = _now()
            report_json = _canonical(report_copy)
            now = _now()
            db.execute(
                """
                UPDATE capability_versions
                SET status = ?, validation_json = ?, updated_at = ?
                WHERE capability_id = ? AND version = ? AND status = 'validating'
                """,
                (new_status, report_json, now, capability_id, version),
            )
            self._insert_event(
                db,
                capability_id=capability_id,
                version=version,
                from_status="validating",
                to_status=new_status,
                reason="Docker sandbox validation report recorded",
                payload=report_copy,
            )
        return self.get(capability_id, version) or {}

    def register(self, capability_id: str, version: str) -> dict[str, Any]:
        return self._transition(
            capability_id,
            version,
            expected="validated",
            new_status="registered",
            reason="Validated capability added to the registry; not activated",
        )

    def get_active(self, capability_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM capability_versions WHERE capability_id = ? AND status = 'active'",
                (capability_id,),
            ).fetchone()
        return self._decode(row) if row else None

    def activate(
        self,
        capability_id: str,
        version: str,
        *,
        approved_by: str = "",
        reason: str = "",
        permission_authorizations: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Promote a registered version after the host permission broker approves its scope.

        Generated source is still never imported or executed in the Mia process.
        Permission declarations are bound to active owner grants here; at execution
        time each requested action is converted into an ordinary Mia computer task,
        which re-checks target paths and one-time approvals.
        """
        if not self.activation_enabled:
            raise CapabilityActivationBlocked(
                "Activation is disabled by default. The owner must set "
                "MIA_CAPABILITY_ACTIVATION_ENABLED=true; promotion only records the "
                "approved version, and generated code still runs only in the isolated sandbox "
                "container, never in the Mia process."
            )
        if not approved_by.strip() or not reason.strip():
            raise ValueError("approved_by and a promotion reason are required")
        item = self.get(capability_id, version)
        if item is None:
            raise CapabilityLifecycleError("capability version not found")
        if item["status"] != "registered":
            raise CapabilityLifecycleError(
                f"only a registered version can be activated, found {item['status']}"
            )
        declared_permissions = set(item["permissions"])
        authorizations = dict(permission_authorizations or {})
        if set(authorizations) != declared_permissions:
            if declared_permissions:
                raise CapabilityActivationBlocked(
                    "Every declared permission needs a matching active owner grant "
                    "before this capability can be activated."
                )
            if authorizations:
                raise ValueError("permission authorizations were supplied for a permission-free capability")
        if any(not isinstance(value, str) or not value.strip() for value in authorizations.values()):
            raise ValueError("permission authorization references must be non-empty")
        self.read_source(capability_id, version)  # source must still match its hash
        validation = item["validation"] or {}
        if validation.get("passed") is not True:
            raise CapabilityLifecycleError("version has no passing validation report")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT version FROM capability_versions "
                "WHERE capability_id = ? AND status = 'active'",
                (capability_id,),
            ).fetchone()
            now = _now()
            previous = current["version"] if current else None
            if previous is not None:
                db.execute(
                    "UPDATE capability_versions SET status = 'deprecated', updated_at = ? "
                    "WHERE capability_id = ? AND version = ? AND status = 'active'",
                    (now, capability_id, previous),
                )
                self._insert_event(
                    db,
                    capability_id=capability_id,
                    version=previous,
                    from_status="active",
                    to_status="deprecated",
                    reason=f"Superseded by {version}",
                    payload={"superseded_by": version},
                )
            updated = db.execute(
                "UPDATE capability_versions SET status = 'active', updated_at = ? "
                "WHERE capability_id = ? AND version = ? AND status = 'registered'",
                (now, capability_id, version),
            )
            if updated.rowcount != 1:
                raise CapabilityLifecycleError("version changed during activation")
            self._insert_event(
                db,
                capability_id=capability_id,
                version=version,
                from_status="registered",
                to_status="active",
                reason=reason.strip(),
                payload={
                    "approved_by": approved_by.strip(),
                    "previous_active": previous,
                    "source_sha256": item["source_sha256"],
                    "permission_authorizations": authorizations,
                },
            )
        return self.get(capability_id, version) or {}

    def rollback(
        self,
        capability_id: str,
        *,
        approved_by: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        """Retire the active version as `rolled_back` and restore the one it replaced.

        If it replaced nothing, the capability is left with no active version.
        A rolled-back version is terminal and cannot be re-activated.
        """
        if not approved_by.strip() or not reason.strip():
            raise ValueError("approved_by and a rollback reason are required")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute(
                "SELECT version, source_sha256 FROM capability_versions "
                "WHERE capability_id = ? AND status = 'active'",
                (capability_id,),
            ).fetchone()
            if active is None:
                raise CapabilityLifecycleError("capability has no active version")
            activation = db.execute(
                """
                SELECT payload_json FROM capability_events
                WHERE capability_id = ? AND version = ? AND to_status = 'active'
                ORDER BY occurred_at DESC, rowid DESC LIMIT 1
                """,
                (capability_id, active["version"]),
            ).fetchone()
            previous = (
                json.loads(activation["payload_json"]).get("previous_active")
                if activation
                else None
            )
            restore = None
            if previous:
                restore = db.execute(
                    "SELECT version FROM capability_versions "
                    "WHERE capability_id = ? AND version = ? AND status = 'deprecated'",
                    (capability_id, previous),
                ).fetchone()
            now = _now()
            db.execute(
                "UPDATE capability_versions SET status = 'rolled_back', updated_at = ? "
                "WHERE capability_id = ? AND version = ? AND status = 'active'",
                (now, capability_id, active["version"]),
            )
            self._insert_event(
                db,
                capability_id=capability_id,
                version=active["version"],
                from_status="active",
                to_status="rolled_back",
                reason=reason.strip(),
                payload={
                    "approved_by": approved_by.strip(),
                    "restored_version": restore["version"] if restore else None,
                },
            )
            if restore:
                db.execute(
                    "UPDATE capability_versions SET status = 'active', updated_at = ? "
                    "WHERE capability_id = ? AND version = ? AND status = 'deprecated'",
                    (now, capability_id, restore["version"]),
                )
                self._insert_event(
                    db,
                    capability_id=capability_id,
                    version=restore["version"],
                    from_status="deprecated",
                    to_status="active",
                    reason=f"Restored by rollback of {active['version']}",
                    payload={
                        "approved_by": approved_by.strip(),
                        "rolled_back_version": active["version"],
                    },
                )
        return {
            "capability_id": capability_id,
            "rolled_back_version": active["version"],
            "restored_version": restore["version"] if restore else None,
        }

    def retire(self, capability_id: str, version: str, reason: str) -> dict[str, Any]:
        return self._transition(
            capability_id,
            version,
            expected="registered",
            new_status="retired",
            reason=reason,
        )

    def get(self, capability_id: str, version: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                """
                SELECT * FROM capability_versions
                WHERE capability_id = ? AND version = ?
                """,
                (capability_id, version),
            ).fetchone()
        return self._decode(row) if row else None

    def list_versions(self, capability_id: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as db:
            if capability_id:
                rows = db.execute(
                    """
                    SELECT * FROM capability_versions
                    WHERE capability_id = ?
                    ORDER BY created_at DESC
                    """,
                    (capability_id,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM capability_versions ORDER BY updated_at DESC"
                ).fetchall()
        return [self._decode(row) for row in rows]

    def event_counts(self, since: str, until: str) -> dict[str, Any]:
        """Count status transitions with occurred_at in [since, until)."""
        with self._connection() as db:
            rows = db.execute(
                """
                SELECT to_status, COUNT(*) AS n FROM capability_events
                WHERE occurred_at >= ? AND occurred_at < ? GROUP BY to_status
                """,
                (since, until),
            ).fetchall()
            approvals = db.execute(
                """
                SELECT COUNT(*) AS n FROM capability_events
                WHERE occurred_at >= ? AND occurred_at < ?
                  AND to_status IN ('active', 'rolled_back')
                  AND payload_json LIKE '%"approved_by"%'
                """,
                (since, until),
            ).fetchone()
        return {
            "transitions": {row["to_status"]: row["n"] for row in rows},
            "owner_approved_actions": approvals["n"],
        }

    def list_events(self, capability_id: str, version: str) -> list[dict[str, Any]]:
        """Status-transition history of one version, oldest first."""
        with self._connection() as db:
            rows = db.execute(
                """
                SELECT * FROM capability_events
                WHERE capability_id = ? AND version = ?
                ORDER BY occurred_at, rowid
                """,
                (capability_id, version),
            ).fetchall()
        return [dict(row) for row in rows]

    def lineage(self, capability_id: str, version: str) -> list[dict[str, Any]]:
        chain = []
        current = self.get(capability_id, version)
        seen = set()
        while current:
            key = (current["capability_id"], current["version"])
            if key in seen:
                raise CapabilityLifecycleError("capability lineage contains a cycle")
            seen.add(key)
            chain.append(current)
            if not current["parent_id"]:
                break
            current = self.get(current["parent_id"], current["parent_version"])
        return chain

    def read_source(self, capability_id: str, version: str) -> str:
        item = self.get(capability_id, version)
        if item is None:
            raise CapabilityLifecycleError("capability version not found")
        path = Path(item["source_path"]).resolve()
        path.relative_to(self.workspace)
        source = path.read_text(encoding="utf-8")
        if hashlib.sha256(source.encode("utf-8")).hexdigest() != item["source_sha256"]:
            raise CapabilityLifecycleError("persisted source hash does not match registry")
        return source

    def read_test_source(self, capability_id: str, version: str) -> str:
        item = self.get(capability_id, version)
        if item is None:
            raise CapabilityLifecycleError("capability version not found")
        if not item["test_source_path"] or not item["test_sha256"]:
            raise CapabilityLifecycleError("candidate has no immutable test source")
        path = Path(item["test_source_path"]).resolve()
        path.relative_to(self.workspace)
        source = path.read_text(encoding="utf-8")
        if hashlib.sha256(source.encode("utf-8")).hexdigest() != item["test_sha256"]:
            raise CapabilityLifecycleError("persisted test source hash does not match registry")
        return source

    def _transition(
        self,
        capability_id: str,
        version: str,
        *,
        expected: str,
        new_status: str,
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("transition reason is required")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT status FROM capability_versions
                WHERE capability_id = ? AND version = ?
                """,
                (capability_id, version),
            ).fetchone()
            if row is None:
                raise CapabilityLifecycleError("capability version not found")
            if row["status"] != expected:
                raise CapabilityLifecycleError(
                    f"expected {expected}, found {row['status']}"
                )
            now = _now()
            db.execute(
                """
                UPDATE capability_versions SET status = ?, updated_at = ?
                WHERE capability_id = ? AND version = ? AND status = ?
                """,
                (new_status, now, capability_id, version, expected),
            )
            self._insert_event(
                db,
                capability_id=capability_id,
                version=version,
                from_status=expected,
                to_status=new_status,
                reason=reason,
                payload={},
            )
        return self.get(capability_id, version) or {}

    @staticmethod
    def _insert_event(
        db: sqlite3.Connection,
        *,
        capability_id: str,
        version: str,
        from_status: str | None,
        to_status: str,
        reason: str,
        payload: dict[str, Any],
    ) -> None:
        db.execute(
            """
            INSERT INTO capability_events (
                event_id, capability_id, version, occurred_at,
                from_status, to_status, reason, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                capability_id,
                version,
                _now(),
                from_status,
                to_status,
                reason,
                _canonical(payload),
            ),
        )

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["permissions"] = json.loads(result.pop("permissions_json"))
        raw_dependencies = result.pop("dependencies_json", "[]")
        result["dependencies"] = json.loads(raw_dependencies)
        raw_report = result.pop("validation_json")
        result["validation"] = json.loads(raw_report) if raw_report else None
        return result
