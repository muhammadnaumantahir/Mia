"""Evidence-based capability-gap discovery (MIA-EVOL-01), against real task and memory stores."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from mia.agent.tasks import TaskStore
from mia.evolution.gaps import MIN_EVIDENCE, WINDOW_DAYS, GapStore
from mia.memory.store import MemoryStore

from .test_brain_pipeline import client_for


@pytest.fixture
def env(tmp_path):
    return (GapStore(tmp_path / "gaps.sqlite3"), TaskStore(tmp_path / "tasks.sqlite3"),
            MemoryStore(tmp_path / "memory.sqlite3"))


def fail_task(tasks, title="Summarise the PDF", error="tool_unavailable", source="interactive"):
    tid = tasks.create_task(title=title, goal="goal text", source=source)
    tasks.transition(tid, "approved", reason="t")
    tasks.transition(tid, "running", reason="t")
    tasks.transition(tid, "failed", reason="t", payload={"error_type": error})
    return tid


def correct(memory, n, record_type="fact"):
    for i in range(n):
        rid, _ = memory.structured.add(record_type=record_type, title=f"Fact {n}-{i}", body=f"Original {n} {i}.")
        memory.structured.supersede(rid, body=f"Corrected {n} {i}.")


def grant(gaps, *sources):
    for s in sources:
        gaps.set_consent(s, True)


def scan(env, **kw):
    gaps, tasks, memory = env
    kw.setdefault("dry_run", False)
    return gaps.scan(tasks, memory, **kw)


# ------------------------------------------------------------------ consent


def test_nothing_is_read_without_consent(env):
    gaps, tasks, memory = env
    for _ in range(3):
        fail_task(tasks)
    correct(memory, 3)
    assert gaps.consent() == {"failed_tasks": False, "corrections": False, "evaluations": False}
    report = scan(env)
    assert report["examined"] == {} and report["created"] == []
    assert report["skipped"] == {"failed_tasks": "no_consent", "corrections": "no_consent",
                                 "evaluations": "no_consent"}
    assert gaps.list(include_duplicates=True) == []


def test_consent_validation_and_evaluation_submission_requires_it(env):
    gaps = env[0]
    with pytest.raises(ValueError):
        gaps.set_consent("browser_history", True)
    with pytest.raises(PermissionError):
        gaps.record_evaluation_failure("suite", "case", "run-1")
    grant(gaps, "evaluations")
    assert gaps.record_evaluation_failure("suite", "case", "run-1") is True
    assert gaps.record_evaluation_failure("suite", "case", "run-1") is False  # idempotent
    for bad in (("", "c", "r"), ("s", "", "r"), ("s", "c", "x" * 81)):
        with pytest.raises(ValueError):
            gaps.record_evaluation_failure(*bad)


def test_revoking_consent_deletes_evidence_and_orphan_gaps(env):
    gaps, tasks, memory = env
    grant(gaps, "failed_tasks", "corrections")
    for _ in range(2):
        fail_task(tasks)
    correct(memory, 3)
    scan(env)
    assert len(gaps.list()) == 2
    revoked = gaps.set_consent("failed_tasks", False)
    assert revoked["removed_evidence"] == 2 and revoked["removed_gaps"] == 1
    assert [g["category"] for g in gaps.list()] == ["corrections"]
    again = scan(env)
    assert again["skipped"]["failed_tasks"] == "no_consent" and len(gaps.list()) == 1
    with sqlite3.connect(gaps.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM gap_evidence WHERE source = 'failed_tasks'").fetchone()[0] == 0
    actions = [e["action"] for e in gaps.events()]
    assert "consent_revoked" in actions and "gap_removed_consent_revoked" in actions


def test_revoking_evaluations_also_deletes_submitted_signals(env):
    gaps = env[0]
    grant(gaps, "evaluations")
    gaps.record_evaluation_failure("s", "c", "r1")
    gaps.set_consent("evaluations", False)
    with sqlite3.connect(gaps.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM eval_signals").fetchone()[0] == 0


# ------------------------------------------------------------------ thresholds: no fabricated demand


def test_a_single_failure_or_correction_is_not_a_gap(env):
    gaps, tasks, memory = env
    grant(gaps, "failed_tasks", "corrections", "evaluations")
    fail_task(tasks)
    correct(memory, 2)
    gaps.record_evaluation_failure("s", "c", "r1")
    report = scan(env)
    assert report["created"] == [] and gaps.list(include_duplicates=True) == []
    got = {b["key"]: (b["evidence"], b["needed"]) for b in report["below_threshold"]}
    assert got == {"failed_task:interactive:tool_unavailable": (1, MIN_EVIDENCE["failed_tasks"]),
                   "correction:fact": (2, MIN_EVIDENCE["corrections"]),
                   "eval:s:c": (1, MIN_EVIDENCE["evaluations"])}


def test_nothing_else_creates_gaps(env):
    gaps, tasks, memory = env
    grant(gaps, "failed_tasks", "corrections", "evaluations")
    for n in range(10):  # lots of ordinary activity and successes, no failures
        tid = tasks.create_task(title=f"ok {n}", goal="g")
        tasks.transition(tid, "approved", reason="t")
        tasks.transition(tid, "running", reason="t")
        tasks.transition(tid, "completed", reason="t")
        memory.structured.add(record_type="fact", title=f"F{n}", body=f"Plain fact {n}.")
    assert scan(env)["created"] == []


# ------------------------------------------------------------------ detection


def test_repeated_failed_tasks_make_a_scored_gap_with_cited_evidence(env):
    gaps, tasks, _ = env
    grant(gaps, "failed_tasks")
    ids = [fail_task(tasks, title=f"Summarise PDF number {n}") for n in range(3)]
    fail_task(tasks, error="timeout")  # different signature, only one: stays below threshold
    report = scan(env)
    assert [c["key"] for c in report["created"]] == ["failed_task:interactive:tool_unavailable"]
    gap = gaps.get(report["created"][0]["gap_id"])
    assert (gap["confidence"], gap["impact"], gap["evidence_count"]) == (0.65, "medium", 3)
    assert gap["status"] == "open" and gap["consent_scope"] == ["failed_tasks"]
    assert {(e["ref_type"], e["ref_id"]) for e in gap["evidence"]} == {("task", i) for i in ids}
    assert all(e["excerpt"].startswith("Summarise PDF") for e in gap["evidence"])
    assert [b["key"] for b in report["below_threshold"]] == ["failed_task:interactive:timeout"]


def test_confidence_and_impact_grow_with_evidence(env):
    gaps, tasks, _ = env
    grant(gaps, "failed_tasks")
    for _ in range(2):
        fail_task(tasks)
    first = gaps.get(scan(env)["created"][0]["gap_id"])
    assert (first["confidence"], first["impact"]) == (0.55, "low")
    for _ in range(3):
        fail_task(tasks)
    rescan = scan(env)
    assert rescan["created"] == [] and rescan["updated"][0]["new_evidence"] == 3
    grown = gaps.get(first["gap_id"])
    assert (grown["evidence_count"], grown["confidence"], grown["impact"]) == (5, 0.85, "high")
    assert scan(env)["updated"] == []  # idempotent: no new evidence, no change


def test_corrections_store_no_memory_text(env):
    gaps, _, memory = env
    grant(gaps, "corrections")
    correct(memory, 3, record_type="preference")
    gap = gaps.get(scan(env)["created"][0]["gap_id"])
    assert gap["category"] == "corrections" and gap["confidence"] == 0.6
    assert all(e["excerpt"] == "" for e in gap["evidence"])
    assert "Corrected" not in str(gap) and "Original" not in str(gap)


def test_evaluation_failures_need_distinct_runs(env):
    gaps = env[0]
    grant(gaps, "evaluations")
    gaps.record_evaluation_failure("tools", "calendar-read", "run-1", "no calendar tool")
    gaps.record_evaluation_failure("tools", "calendar-read", "run-1", "same run again")
    assert scan(env)["created"] == []
    gaps.record_evaluation_failure("tools", "calendar-read", "run-2", "still no tool")
    report = scan(env)
    gap = gaps.get(report["created"][0]["gap_id"])
    assert gap["category"] == "evaluations" and gap["evidence_count"] == 2 and gap["confidence"] == 0.7


def test_old_evidence_outside_the_window_does_not_count(env):
    gaps, tasks, _ = env
    grant(gaps, "failed_tasks")
    for _ in range(3):
        fail_task(tasks)
    later = datetime.now(UTC) + timedelta(days=WINDOW_DAYS + 5)
    report = scan(env, now=later)
    assert report["created"] == [] and report["below_threshold"] == []


def test_credential_like_text_is_never_stored(env):
    gaps, tasks, _ = env
    grant(gaps, "failed_tasks", "evaluations")
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    for _ in range(2):
        fail_task(tasks, title=f"use key {secret}")
    gaps.record_evaluation_failure("s", "c", "r1", f"token {secret}")
    gaps.record_evaluation_failure("s", "c", "r2", f"token {secret}")
    scan(env)
    with sqlite3.connect(gaps.database_path) as db:
        dump = str(db.execute("SELECT * FROM gap_evidence").fetchall()) + str(
            db.execute("SELECT * FROM eval_signals").fetchall())
    assert secret not in dump and "withheld" in dump


def test_dry_run_writes_nothing(env):
    gaps, tasks, _ = env
    grant(gaps, "failed_tasks")
    for _ in range(2):
        fail_task(tasks)
    report = scan(env, dry_run=True)
    assert len(report["created"]) == 1 and "gap_id" not in report["created"][0]
    assert gaps.list(include_duplicates=True) == []


# ------------------------------------------------------------------ owner requests


def test_owner_request_is_explicit_idempotent_and_needs_no_grant(env):
    gaps = env[0]
    gap_id, created = gaps.add_owner_request("Read PDF files", "I get many PDFs", impact="high", project="Atlas")
    assert created is True
    gap = gaps.get(gap_id)
    assert (gap["confidence"], gap["impact"], gap["project"]) == (1.0, "high", "Atlas")
    assert gap["consent_scope"] == ["owner_request"] and gap["evidence"][0]["source"] == "owner_request"
    assert gaps.add_owner_request("  read pdf   FILES ") == (gap_id, False)
    for bad in (dict(title=""), dict(title="x" * 121), dict(title="ok", impact="huge"),
                dict(title="key sk-abcdefghijklmnopqrstuvwxyz123456"),
                dict(title="ok", rationale="key sk-abcdefghijklmnopqrstuvwxyz123456")):
        with pytest.raises(ValueError):
            gaps.add_owner_request(**bad)
    gaps.set_consent("failed_tasks", False)  # revoking another source never removes an owner request
    assert gaps.get(gap_id) is not None


# ------------------------------------------------------------------ duplicates and lifecycle


def test_similar_gaps_are_linked_not_merged(env):
    gaps = env[0]
    first, _ = gaps.add_owner_request("Calendar sync with Outlook")
    second, _ = gaps.add_owner_request("Outlook calendar sync")
    other, _ = gaps.add_owner_request("Generate invoices")
    assert gaps.get(second)["duplicate_of"] == first and gaps.get(other)["duplicate_of"] is None
    assert {g["gap_id"] for g in gaps.list()} == {first, other}
    assert {g["gap_id"] for g in gaps.list(include_duplicates=True)} == {first, second, other}
    assert gaps.get(first)["duplicates"] == [second]
    assert gaps.delete(first) is True and gaps.get(second)["duplicate_of"] is None


def test_status_changes_dismissal_survives_new_evidence_and_events(env):
    gaps, tasks, _ = env
    grant(gaps, "failed_tasks")
    for _ in range(2):
        fail_task(tasks)
    gap_id = scan(env)["created"][0]["gap_id"]
    assert gaps.set_status(gap_id, "dismissed", "not a priority") is True
    assert gaps.set_status(gap_id, "dismissed") is False
    fail_task(tasks)
    scan(env)
    got = gaps.get(gap_id)
    assert got["status"] == "dismissed" and got["evidence_count"] == 3  # evidence kept, status not reopened
    assert [g for g in gaps.list(status="open")] == [] and gaps.list(status="dismissed")[0]["gap_id"] == gap_id
    for bad in ("done", ""):
        with pytest.raises(ValueError):
            gaps.set_status(gap_id, bad)
    with pytest.raises(KeyError):
        gaps.set_status("missing", "open")
    assert {"gap_created", "status_changed", "evidence_added"} <= {e["action"] for e in gaps.events(gap_id)}


def test_list_ranks_by_impact_then_confidence(env):
    gaps = env[0]
    low, _ = gaps.add_owner_request("Alpha thing", impact="low")
    high, _ = gaps.add_owner_request("Beta thing", impact="high")
    mid, _ = gaps.add_owner_request("Gamma thing", impact="medium")
    assert [g["gap_id"] for g in gaps.list()] == [high, mid, low]
    with pytest.raises(ValueError):
        gaps.list(status="bogus")


# ------------------------------------------------------------------ API


def test_api_flow(tmp_path, monkeypatch):
    client, memory, _ = client_for(tmp_path, monkeypatch)
    assert client.get("/api/evolution/gaps/consent").json()["consent"] == {
        "failed_tasks": False, "corrections": False, "evaluations": False}
    assert client.post("/api/evolution/gaps/evaluation-failure",
                       json={"suite": "s", "case_id": "c", "run_id": "r1"}).status_code == 403
    assert client.put("/api/evolution/gaps/consent", json={"source": "nope", "granted": True}).status_code == 400
    assert client.put("/api/evolution/gaps/consent", json={"source": "evaluations", "granted": True}).status_code == 200
    for run in ("r1", "r2"):
        assert client.post("/api/evolution/gaps/evaluation-failure",
                           json={"suite": "tools", "case_id": "cal", "run_id": run, "detail": "no tool"}
                           ).json() == {"recorded": True}
    dry = client.post("/api/evolution/gaps/scan", json={}).json()
    assert dry["dry_run"] is True and len(dry["created"]) == 1 and client.get("/api/evolution/gaps").json()["gaps"] == []
    done = client.post("/api/evolution/gaps/scan", json={"dry_run": False}).json()
    gap_id = done["created"][0]["gap_id"]
    detail = client.get(f"/api/evolution/gaps/{gap_id}").json()
    assert detail["evidence_count"] == 2 and detail["events"][0]["action"] == "gap_created"
    assert client.get("/api/evolution/gaps/missing").status_code == 404

    owner = client.post("/api/evolution/gaps/owner-request", json={"title": "Read PDFs", "impact": "high"})
    assert owner.status_code == 201 and owner.json()["created"] is True
    assert client.post("/api/evolution/gaps/owner-request", json={"title": "Read PDFs"}).json()["created"] is False
    assert client.post("/api/evolution/gaps/owner-request", json={"title": "x", "impact": "huge"}).status_code == 400
    assert [g["title"] for g in client.get("/api/evolution/gaps").json()["gaps"]][0] == "Read PDFs"

    assert client.post(f"/api/evolution/gaps/{gap_id}/status", json={"status": "parked"}).json()["changed"] is True
    assert client.post(f"/api/evolution/gaps/{gap_id}/status", json={"status": "bogus"}).status_code == 400
    assert client.post("/api/evolution/gaps/missing/status", json={"status": "open"}).status_code == 404
    revoked = client.put("/api/evolution/gaps/consent", json={"source": "evaluations", "granted": False}).json()
    assert revoked["removed_gaps"] == 1 and client.get(f"/api/evolution/gaps/{gap_id}").status_code == 404
    owner_id = owner.json()["gap_id"]
    assert client.delete(f"/api/evolution/gaps/{owner_id}").status_code == 200
    assert client.delete(f"/api/evolution/gaps/{owner_id}").status_code == 404


def test_recomputing_an_owner_request_never_rescores_it(env):
    gaps = env[0]
    gap_id, _ = gaps.add_owner_request("Read PDF files", impact="high")
    with gaps._connection() as db:
        assert gaps._recompute(db, gap_id) == gap_id
    got = gaps.get(gap_id)
    assert (got["confidence"], got["impact"], got["evidence_count"]) == (1.0, "high", 1)
