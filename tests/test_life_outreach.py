from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.life.outreach import OutreachEngine, candidate_from_cycle
from mia.life.reflect import Reflector
from mia.life.runner import ExplorationResult, LifeRunner
from mia.life.store import LifeStore
from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def ok():
    return {"cpu_percent": 10.0, "memory_percent": 20.0}


class Scripted:
    name = "scripted"
    permission_level = 1

    def __init__(self, result):
        self.result = result

    def explore(self, item, context):
        return self.result


FOUND = ExplorationResult(
    outcome="completed", item_state="done",
    summary="Fetched docs.python.org: 10 bytes, stored as an untrusted research note.",
    sources=[{"type": "url", "ref": "https://docs.python.org/", "host": "docs.python.org",
              "sha256": "abc", "stored_item_id": "note1", "new": True}],
)
BLOCKED = ExplorationResult(
    outcome="deferred", item_state="blocked", summary="Fetch did not complete (dns).",
    state_reason="dns",
)


class FakeMonitor:
    def snapshot(self):
        return ok()


def parts(tmp_path, result, *, clock=None, **settings):
    memory = MemoryStore(tmp_path / "m.sqlite3")
    life = LifeStore(tmp_path / "l.sqlite3")
    life.update_settings({"min_interval_seconds": 0, "outreach_enabled": True, **settings})
    runner = LifeRunner(life, memory, Scripted(result), resource_snapshot=ok,
                        clock=clock or (lambda: NOW))
    return life, runner


def owner_item(life, title="Python docs overview", origin="owner"):
    return life.add_item(title=title, why_it_matters="Learn the docs layout", origin=origin)[0]


def test_finding_is_delivered_with_evidence_and_journaled(tmp_path):
    life, runner = parts(tmp_path, FOUND)
    owner_item(life)
    result = runner.run_cycle("schedule")
    messages = life.list_outreach()
    assert len(messages) == 1 and messages[0]["state"] == "pending"
    assert messages[0]["kind"] == "finding" and "untrusted" in messages[0]["body"]
    assert any(e.get("ref") == result["cycle_id"] for e in messages[0]["evidence"])
    entry = life.get_cycle(result["cycle_id"])
    assert entry["notification_decision"] == f"sent: inbox message {messages[0]['message_id']}"


def test_disabled_by_default_suppresses_and_says_why(tmp_path):
    life, runner = parts(tmp_path, FOUND, outreach_enabled=False)
    owner_item(life)
    result = runner.run_cycle("schedule")
    assert life.list_outreach() == []
    assert life.get_cycle(result["cycle_id"])["notification_decision"] == "suppressed: outreach_disabled"


def test_housekeeping_items_never_contact_owner(tmp_path):
    life, runner = parts(tmp_path, FOUND)
    owner_item(life, origin="self")
    result = runner.run_cycle("schedule")
    assert life.list_outreach() == []
    assert life.get_cycle(result["cycle_id"])["notification_decision"].startswith("none")


def test_blocked_owner_item_asks_for_help_once(tmp_path):
    life, runner = parts(tmp_path, BLOCKED)
    owner_item(life)
    runner.run_cycle("schedule")
    [message] = life.list_outreach()
    assert message["kind"] == "needs_help" and "blocked" in message["topic_key"]


def test_quiet_hours_daily_cap_and_gap_suppress(tmp_path):
    life, runner = parts(tmp_path, FOUND, quiet_hours_enabled=False, max_outreach_per_day=1)
    engine = OutreachEngine(life)
    owner_item(life)
    cid = runner.run_cycle("schedule")["cycle_id"]
    assert len(life.list_outreach()) == 1
    cand = candidate_from_cycle(life.get_cycle(cid), life.get_item(life.get_cycle(cid)["item_id"]))
    cand2 = type(cand)(cand.kind, "finding:other", cand.title, cand.body, cand.evidence)
    s = life.get_settings()
    assert engine.decide(cand2, s, NOW, owner_present=False) == "daily_outreach_cap"
    life.update_settings({"max_outreach_per_day": 5, "min_outreach_gap_minutes": 10_080})
    assert engine.decide(cand2, life.get_settings(), NOW, owner_present=False) == "outreach_gap"
    life.update_settings({"min_outreach_gap_minutes": 0})
    assert engine.decide(cand2, life.get_settings(), NOW, owner_present=False) is None
    life.update_settings({"quiet_hours_enabled": True, "quiet_start_minute": 600, "quiet_end_minute": 800})
    assert engine.decide(cand2, life.get_settings(), NOW, owner_present=False) == "quiet_hours"
    assert engine.decide(cand2, life.get_settings(), NOW, owner_present=True) is None


def test_same_topic_cooldown_and_dismissal_memory(tmp_path):
    life, _ = parts(tmp_path, FOUND, max_outreach_per_day=10, min_outreach_gap_minutes=0)
    engine = OutreachEngine(life)
    cand = type("C", (), {})()
    from mia.life.outreach import OutreachCandidate
    cand = OutreachCandidate("finding", "finding:x", "t", "b", [{"type": "cycle", "ref": "c"}])
    s = life.get_settings()
    assert engine.decide(cand, s, NOW, owner_present=False) is None
    mid = life.add_outreach(kind="finding", topic_key="finding:x", title="t", body="b",
                            evidence=[{"type": "cycle", "ref": "c"}])
    assert engine.decide(cand, s, NOW, owner_present=False) == "topic_cooldown"
    life.set_outreach_state(mid, "dismissed")
    later = NOW + timedelta(days=20)  # past cooldown, inside the dismissal window
    assert engine.decide(cand, s, later, owner_present=False) == "topic_dismissed"
    assert engine.decide(cand, s, NOW + timedelta(days=45), owner_present=False) is None


def test_unanswered_backlog_stops_further_messages(tmp_path):
    life, _ = parts(tmp_path, FOUND, max_unanswered_outreach=2, min_outreach_gap_minutes=0,
                    max_outreach_per_day=20)
    from mia.life.outreach import OutreachCandidate
    engine = OutreachEngine(life)
    ev = [{"type": "cycle", "ref": "c"}]
    for i in range(2):
        life.add_outreach(kind="finding", topic_key=f"t{i}", title="t", body="b", evidence=ev)
    cand = OutreachCandidate("finding", "new", "t", "b", ev)
    assert engine.decide(cand, life.get_settings(), NOW, owner_present=False) == "unanswered_backlog"
    first = life.list_outreach()[0]["message_id"]
    life.set_outreach_state(first, "read")
    assert engine.decide(cand, life.get_settings(), NOW, owner_present=False) is None


def test_emergency_stop_and_pause_suppress(tmp_path):
    life, _ = parts(tmp_path, FOUND)
    from mia.life.outreach import OutreachCandidate
    engine = OutreachEngine(life)
    cand = OutreachCandidate("finding", "n", "t", "b", [{"type": "cycle", "ref": "c"}])
    life.update_settings({"paused": True})
    assert engine.decide(cand, life.get_settings(), NOW, owner_present=False) == "paused"
    life.update_settings({"emergency_stop": True})
    assert engine.decide(cand, life.get_settings(), NOW, owner_present=True) == "emergency_stop"


def test_goal_followup_checks_in_without_page_text(tmp_path):
    life, runner = parts(tmp_path, ExplorationResult(
        outcome="completed", item_state="done", summary="Reviewed memory"))
    life.add_item(title="Review progress on goal: Ship Mia", why_it_matters="Active goal stale for 20 days.",
                  origin="goal_followup", source_ref={"type": "record", "ref": "rec1"})
    runner.run_cycle("schedule")
    [message] = life.list_outreach()
    assert message["kind"] == "goal_check_in" and message["title"].endswith("Ship Mia")


def test_outreach_requires_evidence_and_valid_state(tmp_path):
    life = LifeStore(tmp_path / "l.sqlite3")
    with pytest.raises(ValueError):
        life.add_outreach(kind="finding", topic_key="k", title="t", body="b", evidence=[])
    mid = life.add_outreach(kind="finding", topic_key="k", title="t", body="b",
                            evidence=[{"type": "cycle", "ref": "c"}])
    with pytest.raises(ValueError):
        life.set_outreach_state(mid, "pending")
    with pytest.raises(KeyError):
        life.set_outreach_state("missing", "read")


def test_invalid_outreach_settings_rejected(tmp_path):
    life = LifeStore(tmp_path / "l.sqlite3")
    for bad in ({"outreach_enabled": "yes"}, {"max_outreach_per_day": -1},
                {"outreach_cooldown_days": 0}, {"max_unanswered_outreach": 0}):
        with pytest.raises(ValueError):
            life.update_settings(bad)
    assert life.get_settings().outreach_enabled is False


def test_reflection_waits_for_enough_cycles_and_cites_them(tmp_path):
    life, runner = parts(tmp_path, FOUND)
    reflector = Reflector(life, every=3)
    runner.reflector = reflector
    for i in range(2):
        owner_item(life, title=f"Topic number {i} docs")
        runner.run_cycle("schedule")
    assert life.list_reflections() == []
    owner_item(life, title="Topic number 9 docs")
    runner.run_cycle("schedule")
    [reflection] = life.list_reflections()
    assert reflection["cycles_reviewed"] == 3 and len(reflection["evidence"]) == 3
    assert any("docs.python.org" in o for o in reflection["observations"])
    assert any("Owner messages" in o for o in reflection["observations"])
    owner_item(life, title="Topic number 10 docs")
    runner.run_cycle("schedule")
    assert len(life.list_reflections()) == 1  # not re-reviewed until 3 new cycles


def test_after_cycle_faults_do_not_break_the_cycle(tmp_path):
    life, runner = parts(tmp_path, FOUND)

    class Boom:
        def consider(self, *a):
            raise RuntimeError("x")

        def reflect(self, *a):
            raise RuntimeError("x")

    runner.outreach, runner.reflector = Boom(), Boom()
    owner_item(life)
    assert runner.run_cycle("schedule")["status"] == "completed"


def test_api_inbox_flow_and_status(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MIA_LIFE_ENABLED", "false")
    life = LifeStore(tmp_path / "l.sqlite3")
    app = create_app(
        store=ObservatoryStore(tmp_path / "o.sqlite3"),
        gateway=ModelGateway(ProviderRegistry()),
        memory=MemoryStore(tmp_path / "m.sqlite3"),
        life=life,
        resource_monitor=FakeMonitor(),
    )
    with TestClient(app) as client:
        assert client.get("/api/life/status").json()["proactive_delivery_enabled"] is False
        assert client.put("/api/life/settings", json={"outreach_enabled": True}).status_code == 200
        assert client.put("/api/life/settings", json={"outreach_enabled": "x"}).status_code in (400, 422)
        mid = life.add_outreach(kind="finding", topic_key="k", title="t", body="b",
                                evidence=[{"type": "cycle", "ref": "c"}])
        status = client.get("/api/life/status").json()
        assert status["proactive_delivery_enabled"] is True and status["unanswered_outreach"] == 1
        assert len(client.get("/api/life/outreach?state=pending").json()["messages"]) == 1
        assert client.post(f"/api/life/outreach/{mid}/state", json={"state": "dismissed"}).status_code == 200
        assert client.post(f"/api/life/outreach/{mid}/state", json={"state": "pending"}).status_code == 400
        assert client.post("/api/life/outreach/nope/state", json={"state": "read"}).status_code == 404
        assert client.get("/api/life/reflections").json() == {"reflections": []}


def test_conversation_origin_item_reports_its_finding(tmp_path):
    life, runner = parts(tmp_path, FOUND)
    owner_item(life, title="Look into: bm25 normalisation", origin="conversation")
    runner.run_cycle("schedule")
    [message] = life.list_outreach()
    assert message["kind"] == "finding" and "bm25" in message["title"]
