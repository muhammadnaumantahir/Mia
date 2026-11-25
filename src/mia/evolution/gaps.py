"""Evidence-based capability-gap discovery (MIA-EVOL-01).

A *gap* is a repeated, real, owner-consented signal that Mia could not do something. This module
only **records and ranks** gaps. It never writes or runs code and never triggers evolution; a
later, separately governed step may read open gaps as input.

Principles
- Consent first. Failed-task, correction and evaluation signals are each **off until the owner
  grants that source**. A source without consent is not read at all (not even counted). An explicit
  owner request needs no grant: submitting it is the consent. Revoking a source deletes its
  evidence, recomputes the affected gaps and deletes any gap left with no evidence.
- No fabricated demand. One failure is never a gap: a signature needs a minimum number of distinct
  pieces of evidence (failed tasks 2, evaluation cases 2, corrections 3) inside the window (90 days).
  Signatures below the threshold are reported as such. Nothing is inferred from code volume,
  file counts, model opinions or guesses; there is no model in this module.
- Evidence is cited, not copied. Each item stores its source, a reference id and a short,
  credential-scrubbed excerpt. Corrections store *no text* (only that a record of some type was
  corrected).
- Deterministic scoring (documented, reproducible):
  confidence = min(cap, base + step * evidence_count): failed tasks 0.35 + 0.10n (cap 0.90),
  evaluations 0.40 + 0.15n (cap 0.90), corrections 0.30 + 0.10n (cap 0.80); an owner request is 1.0.
  impact: count >= 5 -> high, >= 3 -> medium, else low (owner requests: set by the owner).
- Duplicates are linked, not merged: a new gap whose distinguishing words overlap an existing open
  gap by 60% or more gets ``duplicate_of`` pointing at the older gap and is hidden from the
  default listing.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from mia.memory.safety import find_secret, reject_secret

CONSENT_SOURCES = ("failed_tasks", "corrections", "evaluations")
OWNER_REQUEST = "owner_request"
STATUSES = frozenset({"open", "parked", "dismissed", "resolved"})
IMPACTS = ("low", "medium", "high")
MIN_EVIDENCE = {"failed_tasks": 2, "corrections": 3, "evaluations": 2}
_CONFIDENCE = {"failed_tasks": (0.35, 0.10, 0.90), "evaluations": (0.40, 0.15, 0.90),
               "corrections": (0.30, 0.10, 0.80)}
WINDOW_DAYS = 90
SCAN_LIMIT = 500
MAX_TITLE = 120
MAX_TEXT = 1_000
EXCERPT = 160
DUPLICATE_OVERLAP = 0.6
_GENERIC = frozenset({"repeated", "task", "tasks", "failure", "failures", "failed", "correction",
                      "corrections", "evaluation", "owner", "request", "capability", "the", "and",
                      "for", "with", "able", "can", "use", "into", "from", "that", "this"})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _excerpt(text: str | None) -> str:
    text = " ".join((text or "").split())[:EXCERPT]
    return "[withheld: looks like a credential]" if text and find_secret(text) else text


def _tokens(title: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", title.casefold()) if len(w) >= 3 and w not in _GENERIC}


def _score(source: str, count: int) -> tuple[float, str]:
    base, step, cap = _CONFIDENCE[source]
    impact = "high" if count >= 5 else "medium" if count >= 3 else "low"
    return round(min(cap, base + step * count), 2), impact


class GapStore:
    """Gap records, evidence, consent and events in a dedicated SQLite file."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS gap_consent (
                    source TEXT PRIMARY KEY, granted INTEGER NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS capability_gaps (
                    gap_id TEXT PRIMARY KEY, gap_key TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                    summary TEXT NOT NULL, category TEXT NOT NULL, status TEXT NOT NULL,
                    confidence REAL NOT NULL, impact TEXT NOT NULL, evidence_count INTEGER NOT NULL,
                    consent_scope TEXT NOT NULL, duplicate_of TEXT, status_reason TEXT NOT NULL DEFAULT '',
                    project TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS gap_evidence (
                    evidence_id TEXT PRIMARY KEY, gap_id TEXT NOT NULL, source TEXT NOT NULL,
                    ref_type TEXT NOT NULL, ref_id TEXT NOT NULL, observed_at TEXT NOT NULL,
                    excerpt TEXT NOT NULL, UNIQUE (source, ref_type, ref_id));
                CREATE TABLE IF NOT EXISTS eval_signals (
                    signal_id TEXT PRIMARY KEY, suite TEXT NOT NULL, case_id TEXT NOT NULL,
                    run_id TEXT NOT NULL, detail TEXT NOT NULL, observed_at TEXT NOT NULL,
                    UNIQUE (suite, case_id, run_id));
                CREATE TABLE IF NOT EXISTS gap_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT, gap_id TEXT, at TEXT NOT NULL,
                    actor TEXT NOT NULL, action TEXT NOT NULL, detail_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_gap_evidence_gap ON gap_evidence(gap_id);
            """)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _event(db: sqlite3.Connection, gap_id: str | None, actor: str, action: str,
               detail: dict[str, Any]) -> None:
        db.execute("INSERT INTO gap_events (gap_id, at, actor, action, detail_json) VALUES (?,?,?,?,?)",
                   (gap_id, _now(), actor, action, json.dumps(detail, sort_keys=True)))

    # ---------------------------------------------------------------- consent

    def consent(self) -> dict[str, bool]:
        with self._connection() as db:
            rows = {r["source"]: bool(r["granted"]) for r in db.execute("SELECT * FROM gap_consent")}
        return {s: rows.get(s, False) for s in CONSENT_SOURCES}

    def set_consent(self, source: str, granted: bool, *, actor: str = "owner") -> dict[str, Any]:
        if source not in CONSENT_SOURCES:
            raise ValueError("source must be one of " + ", ".join(CONSENT_SOURCES))
        removed = {"evidence": 0, "gaps": 0}
        with self._connection() as db:
            db.execute("INSERT INTO gap_consent (source, granted, updated_at) VALUES (?,?,?) "
                       "ON CONFLICT(source) DO UPDATE SET granted = excluded.granted, updated_at = excluded.updated_at",
                       (source, 1 if granted else 0, _now()))
            self._event(db, None, actor, "consent_granted" if granted else "consent_revoked", {"source": source})
            if not granted:
                removed = self._purge_source(db, source, actor)
        return {"source": source, "granted": bool(granted), **{f"removed_{k}": v for k, v in removed.items()}}

    def _purge_source(self, db: sqlite3.Connection, source: str, actor: str) -> dict[str, int]:
        affected = {r["gap_id"] for r in db.execute(
            "SELECT DISTINCT gap_id FROM gap_evidence WHERE source = ?", (source,))}
        evidence = db.execute("DELETE FROM gap_evidence WHERE source = ?", (source,)).rowcount
        if source == "evaluations":
            db.execute("DELETE FROM eval_signals")
        gaps = 0
        for gap_id in sorted(affected):
            if self._recompute(db, gap_id) is None:
                gaps += 1
                self._event(db, gap_id, actor, "gap_removed_consent_revoked", {"source": source})
        return {"evidence": evidence, "gaps": gaps}

    def _recompute(self, db: sqlite3.Connection, gap_id: str) -> str | None:
        """Refresh counts/scores from the remaining evidence; delete the gap if none is left."""
        gap = db.execute("SELECT * FROM capability_gaps WHERE gap_id = ?", (gap_id,)).fetchone()
        if gap is None:
            return None
        rows = db.execute("SELECT source, observed_at FROM gap_evidence WHERE gap_id = ?", (gap_id,)).fetchall()
        if not rows:
            db.execute("DELETE FROM capability_gaps WHERE gap_id = ?", (gap_id,))
            db.execute("UPDATE capability_gaps SET duplicate_of = NULL WHERE duplicate_of = ?", (gap_id,))
            return None
        scope = sorted({r["source"] for r in rows})
        if gap["category"] == OWNER_REQUEST:
            confidence, impact = gap["confidence"], gap["impact"]
        else:
            confidence, impact = _score(gap["category"], len(rows))
        stamps = sorted(r["observed_at"] for r in rows)
        db.execute("UPDATE capability_gaps SET evidence_count = ?, confidence = ?, impact = ?, consent_scope = ?, "
                   "first_seen = ?, last_seen = ?, updated_at = ? WHERE gap_id = ?",
                   (len(rows), confidence, impact, json.dumps(scope), stamps[0], stamps[-1], _now(), gap_id))
        return gap_id

    # ---------------------------------------------------------------- signals

    def record_evaluation_failure(self, suite: str, case_id: str, run_id: str, detail: str = "",
                                  observed_at: str | None = None) -> bool:
        """Submit one failed evaluation case. Requires consent; returns False if already recorded."""
        if not self.consent()["evaluations"]:
            raise PermissionError("evaluation signals are not consented; grant the evaluations source first")
        for name, value in (("suite", suite), ("case_id", case_id), ("run_id", run_id)):
            if not value or not str(value).strip() or len(str(value)) > 80:
                raise ValueError(f"{name} is required (max 80 characters)")
        if len(detail) > MAX_TEXT:
            raise ValueError(f"detail is limited to {MAX_TEXT} characters")
        when = _parse(observed_at).isoformat() if observed_at else _now()
        with self._connection() as db:
            try:
                db.execute("INSERT INTO eval_signals VALUES (?,?,?,?,?,?)",
                           (uuid.uuid4().hex, suite.strip(), case_id.strip(), run_id.strip(), _excerpt(detail), when))
            except sqlite3.IntegrityError:
                return False
        return True

    def add_owner_request(self, title: str, rationale: str = "", impact: str = "medium",
                          project: str | None = None, *, actor: str = "owner") -> tuple[str, bool]:
        """An explicit owner request for a capability. Returns (gap_id, created)."""
        title = " ".join(title.split())
        if not title or len(title) > MAX_TITLE:
            raise ValueError(f"title is required (max {MAX_TITLE} characters)")
        if len(rationale) > MAX_TEXT:
            raise ValueError(f"rationale is limited to {MAX_TEXT} characters")
        if impact not in IMPACTS:
            raise ValueError("impact must be low, medium or high")
        reject_secret(title, field="title")
        reject_secret(rationale, field="rationale")
        from mia.memory.store import normalise_project_name

        name = normalise_project_name(project)
        key = "owner_request:" + hashlib.sha256(title.casefold().encode()).hexdigest()[:12]
        now = _now()
        with self._connection() as db:
            row = db.execute("SELECT gap_id FROM capability_gaps WHERE gap_key = ?", (key,)).fetchone()
            if row:
                return row["gap_id"], False
            gap_id = uuid.uuid4().hex
            dup = self._find_duplicate(db, title)
            db.execute("INSERT INTO capability_gaps (gap_id, gap_key, title, summary, category, status, confidence, "
                       "impact, evidence_count, consent_scope, duplicate_of, project, first_seen, last_seen, "
                       "created_at, updated_at) VALUES (?,?,?,?,?,'open',1.0,?,1,?,?,?,?,?,?,?)",
                       (gap_id, key, title, rationale.strip() or "The owner asked for this capability.",
                        OWNER_REQUEST, impact, json.dumps([OWNER_REQUEST]), dup, name, now, now, now, now))
            db.execute("INSERT INTO gap_evidence VALUES (?,?,?,?,?,?,?)",
                       (uuid.uuid4().hex, gap_id, OWNER_REQUEST, "owner_request", gap_id, now, _excerpt(title)))
            self._event(db, gap_id, actor, "gap_created", {"source": OWNER_REQUEST, "duplicate_of": dup})
        return gap_id, True

    # ---------------------------------------------------------------- scan

    def _collect(self, tasks: Any, memory: Any, consent: dict[str, bool], cutoff: datetime,
                 report: dict[str, Any]) -> dict[str, dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}

        def add(key: str, source: str, title: str, summary: str, ref_type: str, ref_id: str,
                observed: str, excerpt: str) -> None:
            g = groups.setdefault(key, {"source": source, "title": title, "summary": summary, "evidence": {}})
            g["evidence"][(ref_type, ref_id)] = (observed, excerpt)

        if consent["failed_tasks"] and tasks is not None:
            rows = tasks.list_tasks(SCAN_LIMIT)
            report["examined"]["failed_tasks"] = len(rows)
            for t in rows:
                if t["status"] != "failed" or _parse(t["updated_at"]) < cutoff:
                    continue
                sig = f"{t['source']}:{t.get('last_error') or 'task_failed'}"
                add(f"failed_task:{sig}", "failed_tasks", f"Repeated task failures: {sig}",
                    f"Tasks from '{t['source']}' keep failing with '{t.get('last_error') or 'task_failed'}'.",
                    "task", t["task_id"], t["updated_at"], _excerpt(t["title"]))
        elif not consent["failed_tasks"]:
            report["skipped"]["failed_tasks"] = "no_consent"
        if consent["corrections"] and memory is not None:
            rows = memory.structured.list(limit=SCAN_LIMIT)
            report["examined"]["corrections"] = len(rows)
            for r in rows:
                origin = r.get("origin") or {}
                if not origin.get("corrects") or _parse(r["created_at"]) < cutoff:
                    continue
                add(f"correction:{r['record_type']}", "corrections",
                    f"Repeated corrections to {r['record_type']} memory",
                    f"The owner has corrected stored {r['record_type']} memory several times.",
                    "memory_record", r["record_id"], r["created_at"], "")  # no memory text is copied
        elif not consent["corrections"]:
            report["skipped"]["corrections"] = "no_consent"
        if consent["evaluations"]:
            with self._connection() as db:
                rows = db.execute("SELECT * FROM eval_signals").fetchall()
            report["examined"]["evaluations"] = len(rows)
            for r in rows:
                if _parse(r["observed_at"]) < cutoff:
                    continue
                add(f"eval:{r['suite']}:{r['case_id']}", "evaluations",
                    f"Repeated evaluation failure: {r['suite']} {r['case_id']}",
                    f"Evaluation case '{r['case_id']}' in suite '{r['suite']}' fails across runs.",
                    "eval_run", f"{r['suite']}:{r['case_id']}:{r['run_id']}", r["observed_at"], r["detail"])
        else:
            report["skipped"]["evaluations"] = "no_consent"
        return groups

    def scan(self, tasks: Any, memory: Any, *, now: datetime | None = None, dry_run: bool = True,
             actor: str = "owner") -> dict[str, Any]:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        report: dict[str, Any] = {"dry_run": dry_run, "generated_at": now.isoformat(), "window_days": WINDOW_DAYS,
                                  "examined": {}, "skipped": {}, "created": [], "updated": [],
                                  "below_threshold": []}
        groups = self._collect(tasks, memory, self.consent(), now - timedelta(days=WINDOW_DAYS), report)
        with self._connection() as db:
            for key in sorted(groups):
                g = groups[key]
                n, need = len(g["evidence"]), MIN_EVIDENCE[g["source"]]
                existing = db.execute("SELECT gap_id FROM capability_gaps WHERE gap_key = ?", (key,)).fetchone()
                if n < need and not existing:
                    report["below_threshold"].append({"key": key, "evidence": n, "needed": need})
                    continue
                confidence, impact = _score(g["source"], n)
                if existing:
                    new = [e for e in g["evidence"] if not db.execute(
                        "SELECT 1 FROM gap_evidence WHERE source = ? AND ref_type = ? AND ref_id = ?",
                        (g["source"], *e)).fetchone()]
                    if new:
                        report["updated"].append({"key": key, "gap_id": existing["gap_id"], "new_evidence": len(new)})
                    if new and not dry_run:
                        self._attach(db, existing["gap_id"], g, new)
                        self._recompute(db, existing["gap_id"])
                        self._event(db, existing["gap_id"], actor, "evidence_added", {"count": len(new)})
                    continue
                entry = {"key": key, "title": g["title"], "evidence": n, "confidence": confidence, "impact": impact,
                         "duplicate_of": self._find_duplicate(db, g["title"])}
                if not dry_run:
                    gap_id = uuid.uuid4().hex
                    stamp = _now()
                    db.execute("INSERT INTO capability_gaps (gap_id, gap_key, title, summary, category, status, "
                               "confidence, impact, evidence_count, consent_scope, duplicate_of, first_seen, last_seen, "
                               "created_at, updated_at) VALUES (?,?,?,?,?,'open',?,?,?,?,?,?,?,?,?)",
                               (gap_id, key, g["title"], g["summary"], g["source"], confidence, impact, n,
                                json.dumps([g["source"]]), entry["duplicate_of"], stamp, stamp, stamp, stamp))
                    self._attach(db, gap_id, g, list(g["evidence"]))
                    self._recompute(db, gap_id)
                    self._event(db, gap_id, actor, "gap_created", {"source": g["source"], "evidence": n,
                                                                   "duplicate_of": entry["duplicate_of"]})
                    entry["gap_id"] = gap_id
                report["created"].append(entry)
        return report

    @staticmethod
    def _attach(db: sqlite3.Connection, gap_id: str, g: dict[str, Any], refs: list[tuple[str, str]]) -> None:
        for ref_type, ref_id in refs:
            observed, excerpt = g["evidence"][(ref_type, ref_id)]
            db.execute("INSERT OR IGNORE INTO gap_evidence VALUES (?,?,?,?,?,?,?)",
                       (uuid.uuid4().hex, gap_id, g["source"], ref_type, ref_id, observed, excerpt))

    @staticmethod
    def _find_duplicate(db: sqlite3.Connection, title: str) -> str | None:
        mine = _tokens(title)
        if not mine:
            return None
        best: tuple[float, str, str] | None = None
        for row in db.execute("SELECT gap_id, title, created_at FROM capability_gaps "
                              "WHERE duplicate_of IS NULL AND status IN ('open', 'parked') "
                              "ORDER BY created_at, gap_id"):
            theirs = _tokens(row["title"])
            if not theirs:
                continue
            overlap = len(mine & theirs) / len(mine | theirs)
            if overlap >= DUPLICATE_OVERLAP and (best is None or overlap > best[0]):
                best = (overlap, row["created_at"], row["gap_id"])
        return best[2] if best else None

    # ---------------------------------------------------------------- read / manage

    def list(self, *, status: str | None = None, include_duplicates: bool = False,
             limit: int = 100) -> list[dict[str, Any]]:
        where, params = [], []
        if status:
            if status not in STATUSES:
                raise ValueError("status must be one of " + ", ".join(sorted(STATUSES)))
            where.append("status = ?")
            params.append(status)
        if not include_duplicates:
            where.append("duplicate_of IS NULL")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._connection() as db:
            rows = db.execute(f"SELECT * FROM capability_gaps {clause} "  # noqa: S608 - fixed fragments
                              "ORDER BY CASE impact WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
                              "confidence DESC, last_seen DESC, gap_id LIMIT ?",
                              (*params, max(1, min(int(limit), 500)))).fetchall()
        return [self._decode(r) for r in rows]

    def get(self, gap_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute("SELECT * FROM capability_gaps WHERE gap_id = ?", (gap_id,)).fetchone()
            if row is None:
                return None
            evidence = [dict(e) for e in db.execute(
                "SELECT source, ref_type, ref_id, observed_at, excerpt FROM gap_evidence WHERE gap_id = ? "
                "ORDER BY observed_at, evidence_id", (gap_id,))]
            dups = [r["gap_id"] for r in db.execute(
                "SELECT gap_id FROM capability_gaps WHERE duplicate_of = ? ORDER BY created_at", (gap_id,))]
        return {**self._decode(row), "evidence": evidence, "duplicates": dups}

    def set_status(self, gap_id: str, status: str, reason: str = "", *, actor: str = "owner") -> bool:
        if status not in STATUSES:
            raise ValueError("status must be one of " + ", ".join(sorted(STATUSES)))
        reason = _excerpt(reason)
        with self._connection() as db:
            row = db.execute("SELECT status FROM capability_gaps WHERE gap_id = ?", (gap_id,)).fetchone()
            if row is None:
                raise KeyError(gap_id)
            if row["status"] == status:
                return False
            db.execute("UPDATE capability_gaps SET status = ?, status_reason = ?, updated_at = ? WHERE gap_id = ?",
                       (status, reason, _now(), gap_id))
            self._event(db, gap_id, actor, "status_changed", {"from": row["status"], "to": status})
        return True

    def delete(self, gap_id: str, *, actor: str = "owner") -> bool:
        with self._connection() as db:
            if not db.execute("SELECT 1 FROM capability_gaps WHERE gap_id = ?", (gap_id,)).fetchone():
                return False
            db.execute("DELETE FROM gap_evidence WHERE gap_id = ?", (gap_id,))
            db.execute("DELETE FROM capability_gaps WHERE gap_id = ?", (gap_id,))
            db.execute("UPDATE capability_gaps SET duplicate_of = NULL WHERE duplicate_of = ?", (gap_id,))
            self._event(db, gap_id, actor, "gap_deleted", {})
        return True

    def events(self, gap_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._connection() as db:
            if gap_id:
                rows = db.execute("SELECT * FROM gap_events WHERE gap_id = ? ORDER BY event_id DESC LIMIT ?",
                                  (gap_id, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM gap_events ORDER BY event_id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail_json"])} for r in rows]

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["consent_scope"] = json.loads(result["consent_scope"])
        return result
