"""Bounded proactive suggestions about stalled work (MIA-LIFE-14).

Mia may speak up about two kinds of real, owner-written unfinished work:

* an **idle goal**: an active project with a goal and no task, milestone or goal change for
  ``stale_goal_days``;
* an **idle task**: an open, non-private task nobody has changed for ``suggestion_idle_task_days``
  and that is not already due or overdue (due and overdue items belong to the notification
  center, MIA-LIFE-09).

Everything is deterministic and model-free; the message is a template over stored fields, so
nothing is invented. Each candidate is filtered, in this order, and the reason is reported:

``stale``           idle for more than 90 days: too old to interrupt about (it still appears in
                    the briefing and the task list);
``low_confidence``  Mia captured it from a chat and the owner never touched it (confidence < 0.5);
``low_relevance``   score below the owner's ``suggestion_min_relevance_pct``;
``duplicate``       same title and project as a higher-ranked candidate;
then the shared ``OutreachEngine`` policy: emergency stop, outreach opt-in, paused, quiet hours,
daily cap, minimum gap, unanswered backlog, dismissed-topic memory and per-topic cooldown.
Delivery is the in-app inbox only (pull, never push). Pending suggestions whose evidence has
since changed are withdrawn, and turning suggestions off (or the emergency stop) withdraws them all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from mia.life.outreach import OutreachCandidate, OutreachEngine
from mia.life.store import LifeStore

STALE_AFTER_DAYS = 90
MIN_CONFIDENCE = 0.5
UNCONFIRMED_AFTER_DAYS = 14
_PROVENANCE_CONFIDENCE = {"owner_provided": 1.0, "imported": 0.7, "conversation_captured": 0.6}
TASK_PREFIX = "stalled-task:"
GOAL_PREFIX = "stalled-goal:"
REPORT_LIMIT = 50


@dataclass(frozen=True, slots=True)
class Suggestion:
    candidate: OutreachCandidate
    relevance: float
    confidence: float
    idle_days: int
    dedupe: str


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _idle_bonus(idle_days: int, threshold: int) -> float:
    return min(max(idle_days - threshold, 0), 45) / 45 * 0.2


def build_suggestions(memory: Any, life: LifeStore, now: datetime) -> list[Suggestion]:
    """All candidates, before any filtering, in deterministic order (best first)."""
    from mia.memory.store import project_key

    settings = life.get_settings()
    found: list[Suggestion] = []

    for item in memory.personal.list(kind="task", status="open", limit=500):
        if item["private"]:
            continue  # a private item's title never leaves the owner's own task view
        if item["due_at"] and _parse(item["due_at"]) <= now:
            continue  # due/overdue is the notification center's job
        idle = (now - _parse(item["updated_at"])).days
        if idle < settings.suggestion_idle_task_days:
            continue
        confidence = _PROVENANCE_CONFIDENCE.get(item["provenance"], 0.6)
        untouched = item["updated_at"] == item["created_at"]
        if item["provenance"] == "conversation_captured" and untouched and (
            idle >= UNCONFIRMED_AFTER_DAYS
        ):
            confidence = 0.4  # Mia inferred it and the owner never confirmed it
        relevance = 0.45 + {1: 0.2, 3: -0.2}.get(item["priority"], 0.0)
        relevance += _idle_bonus(idle, settings.suggestion_idle_task_days)
        due_note = (
            f" It is due {_parse(item['due_at']).strftime('%Y-%m-%d')}." if item["due_at"] else ""
        )
        found.append(Suggestion(
            candidate=OutreachCandidate(
                kind="task_check_in",
                topic_key=f"{TASK_PREFIX}{item['item_id']}",
                title=f"Still on your list: {item['title']}"[:200],
                body=(
                    f"This open task has had no change for {idle} days.{due_note} "
                    "Do you want to update it, defer it or drop it?"
                ),
                evidence=[{"type": "personal_item", "ref": item["item_id"]}],
            ),
            relevance=round(min(max(relevance, 0.0), 1.0), 2),
            confidence=confidence, idle_days=idle,
            dedupe=f"{' '.join(item['title'].split()).casefold()}|{project_key(item['project']) or ''}",
        ))

    cutoff = timedelta(days=settings.stale_goal_days)
    for proj in memory.projects.list():
        if proj["status"] != "active" or not proj["goal"]:
            continue
        stamps = [proj["updated_at"]]
        stamps += [i["updated_at"] for i in memory.personal.list(project=proj["name"], limit=500)]
        stamps += [m["updated_at"] for m in memory.projects.milestones(proj["name"])]
        newest = max(_parse(s) for s in stamps)
        if now - newest < cutoff:
            continue
        idle = (now - newest).days
        found.append(Suggestion(
            candidate=OutreachCandidate(
                kind="goal_check_in",
                topic_key=f"{GOAL_PREFIX}{proj['project_key']}",
                title=f"Check in on a goal: {proj['name']}"[:200],
                body=(
                    f"The goal \"{proj['goal']}\" has had no task, milestone or goal change "
                    f"for {idle} days. Do you want to update it, pause it or drop it?"
                ),
                evidence=[{"type": "project", "ref": proj["project_key"]}],
            ),
            relevance=round(min(0.55 + _idle_bonus(idle, settings.stale_goal_days), 1.0), 2),
            confidence=1.0, idle_days=idle, dedupe=f"goal|{proj['project_key']}",
        ))
    found.sort(key=lambda s: (-s.relevance, -s.idle_days, s.candidate.topic_key))
    return found


def withdraw_stale(memory: Any, life: LifeStore, now: datetime, *, withdraw_all: bool = False) -> list[str]:
    """Withdraw pending suggestions whose evidence changed (or all of them when asked)."""
    withdrawn: list[str] = []
    for message in life.list_outreach(state="pending", limit=200):
        topic = message["topic_key"]
        if not topic.startswith((TASK_PREFIX, GOAL_PREFIX)):
            continue
        created = _parse(message["created_at"])
        stale = withdraw_all
        if not stale and topic.startswith(TASK_PREFIX):
            item = memory.personal.get(topic.removeprefix(TASK_PREFIX))
            stale = (
                item is None or item["status"] != "open" or item["private"]
                or _parse(item["updated_at"]) > created
            )
        elif not stale:
            key = topic.removeprefix(GOAL_PREFIX)
            proj = memory.projects.get(key)
            if proj is None or proj["status"] != "active" or not proj["goal"]:
                stale = True
            else:
                stamps = [proj["updated_at"]]
                stamps += [i["updated_at"] for i in memory.personal.list(project=proj["name"], limit=500)]
                stamps += [m["updated_at"] for m in memory.projects.milestones(proj["name"])]
                stale = max(_parse(s) for s in stamps) > created
        if stale:
            life.set_outreach_state(message["message_id"], "withdrawn")
            withdrawn.append(message["message_id"])
    return withdrawn


def run_suggestions(
    memory: Any, life: LifeStore, *, now: datetime | None = None,
    dry_run: bool = True, max_per_run: int = 1,
) -> dict[str, Any]:
    """Evaluate stalled work once. ``dry_run`` (the default) reports without sending."""
    if isinstance(max_per_run, bool) or not isinstance(max_per_run, int) or not 1 <= max_per_run <= 3:
        raise ValueError("max_per_run must be a whole number from 1 to 3")
    now = (now or datetime.now(UTC)).astimezone(UTC)
    settings = life.get_settings()
    off = not settings.suggestions_enabled or settings.emergency_stop
    withdrawn = [] if dry_run else withdraw_stale(memory, life, now, withdraw_all=off)
    report: dict[str, Any] = {
        "generated_at": now.isoformat(), "dry_run": dry_run, "evaluated": 0,
        "withdrawn": withdrawn, "sent": [], "would_send": [], "suppressed": [],
        "disabled": None,
    }
    if not settings.suggestions_enabled:
        report["disabled"] = "suggestions_disabled"
        return report
    engine = OutreachEngine(life)
    seen: set[str] = set()
    min_relevance = settings.suggestion_min_relevance_pct / 100
    for sug in build_suggestions(memory, life, now):
        report["evaluated"] += 1
        cand = sug.candidate
        reason: str | None = None
        if sug.idle_days > STALE_AFTER_DAYS:
            reason = "stale"
        elif sug.confidence < MIN_CONFIDENCE:
            reason = "low_confidence"
        elif sug.relevance < min_relevance:
            reason = "low_relevance"
        elif sug.dedupe in seen:
            reason = "duplicate"
        else:
            # After a send in this run the store has stamped a later time than `now`; deciding
            # with an earlier clock would see a negative gap and wrongly block the next one.
            moment = max(now, datetime.now(UTC)) if report["sent"] else now
            reason = engine.decide(cand, settings, moment, owner_present=False)
            if reason is None and len(report["sent"]) + len(report["would_send"]) >= max_per_run:
                reason = "run_limit"
        entry = {"topic_key": cand.topic_key, "kind": cand.kind, "relevance": sug.relevance,
                 "idle_days": sug.idle_days, "evidence": cand.evidence}
        if reason:
            if len(report["suppressed"]) < REPORT_LIMIT:
                report["suppressed"].append({**entry, "reason": reason})
            continue
        seen.add(sug.dedupe)
        if dry_run:
            report["would_send"].append({**entry, "title": cand.title, "body": cand.body})
        else:
            message_id = life.add_outreach(
                kind=cand.kind, topic_key=cand.topic_key, title=cand.title,
                body=cand.body, evidence=cand.evidence,
            )
            report["sent"].append({**entry, "message_id": message_id, "title": cand.title})
    return report
