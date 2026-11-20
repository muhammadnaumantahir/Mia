"""Proactive outreach: deterministic candidates from journal evidence, strict delivery policy.

Mia only ever speaks to the owner's in-app inbox (pull, never push). A message is
built from templates over facts the journal already holds (the item the owner or a
goal supplied, the cycle's own summary, the stored note id) so nothing is invented
and untrusted page text is never echoed. Every cycle's journal entry records the
delivery decision, including why a message was suppressed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from mia.life.store import LifeSettings, LifeStore, in_quiet_hours

DISMISSED_SUPPRESS_DAYS = 30


@dataclass(frozen=True, slots=True)
class OutreachCandidate:
    kind: str  # finding | needs_help | goal_check_in
    topic_key: str
    title: str
    body: str
    evidence: list[dict[str, Any]]


def candidate_from_cycle(cycle: dict[str, Any], item: dict[str, Any] | None) -> OutreachCandidate | None:
    """A candidate only when the cycle's recorded facts justify speaking up."""
    if item is None or cycle.get("outcome") not in {"completed", "deferred"}:
        return None
    ref = {"type": "cycle", "ref": cycle["cycle_id"]}
    origin = item["origin"]
    if origin == "goal_followup":
        return OutreachCandidate(
            kind="goal_check_in",
            topic_key=f"goal:{item['source_ref'].get('ref', item['item_id'])}",
            title=f"Check in on a goal: {item['title'].removeprefix('Review progress on goal: ')}"[:200],
            body=f"{item['why_it_matters']} Do you want to update it, pause it or drop it?",
            evidence=[ref, {"type": "item", "ref": item["item_id"]}],
        )
    if origin not in {"owner", "conversation"}:
        return None  # Mia does not interrupt the owner about its own housekeeping
    if cycle["outcome"] == "completed":
        stored = [s for s in cycle["sources"] if s.get("stored_item_id")]
        if not stored:
            return None
        note = stored[0]
        return OutreachCandidate(
            kind="finding",
            topic_key=f"finding:{note.get('sha256') or note['stored_item_id']}",
            title=f"I looked into: {item['title']}"[:200],
            body=(
                f"{cycle['summary']} The note is stored (id {note['stored_item_id']}) and marked "
                "untrusted until you review it."
            ),
            evidence=[ref, {"type": "item", "ref": item["item_id"]}, *cycle["sources"][:3]],
        )
    if item["state"] == "blocked":
        return OutreachCandidate(
            kind="needs_help",
            topic_key=f"blocked:{item['item_id']}",
            title=f"I could not finish: {item['title']}"[:200],
            body=f"{cycle['summary']} It stays blocked until you allow a source or drop it.",
            evidence=[ref, {"type": "item", "ref": item["item_id"]}],
        )
    return None


class OutreachEngine:
    def __init__(self, store: LifeStore) -> None:
        self.store = store

    def decide(
        self, candidate: OutreachCandidate, settings: LifeSettings, now: datetime, *, owner_present: bool
    ) -> str | None:
        """Return the reason to suppress, or None when the message may be delivered."""
        if settings.emergency_stop:
            return "emergency_stop"
        if not settings.outreach_enabled:
            return "outreach_disabled"
        if not owner_present:
            if settings.paused:
                return "paused"
            if in_quiet_hours(settings, now):
                return "quiet_hours"
            if self.store.outreach_sent_since(now - timedelta(hours=24)) >= settings.max_outreach_per_day:
                return "daily_outreach_cap"
            last = self.store.last_outreach_at()
            if last and (now - last).total_seconds() < settings.min_outreach_gap_minutes * 60:
                return "outreach_gap"
        elif settings.max_outreach_per_day == 0:
            return "daily_outreach_cap"
        if self.store.unanswered_outreach() >= settings.max_unanswered_outreach:
            return "unanswered_backlog"
        dismissed_since = now - timedelta(days=DISMISSED_SUPPRESS_DAYS)
        if "dismissed" in self.store.topic_history(candidate.topic_key, dismissed_since):
            return "topic_dismissed"
        if self.store.topic_history(candidate.topic_key, now - timedelta(days=settings.outreach_cooldown_days)):
            return "topic_cooldown"
        return None

    def consider(self, cycle_id: str, now: datetime) -> str:
        """Evaluate one finished cycle, deliver at most one message, journal the decision."""
        cycle = self.store.get_cycle(cycle_id)
        if cycle is None or cycle["status"] != "finished":
            return "none: cycle not finished"
        item = self.store.get_item(cycle["item_id"]) if cycle["item_id"] else None
        candidate = candidate_from_cycle(cycle, item)
        if candidate is None:
            decision = "none: nothing in this cycle warrants contacting the owner"
        else:
            settings = self.store.get_settings()
            reason = self.decide(candidate, settings, now, owner_present=cycle["trigger"] == "owner")
            if reason:
                decision = f"suppressed: {reason}"
            else:
                message_id = self.store.add_outreach(
                    kind=candidate.kind, topic_key=candidate.topic_key, title=candidate.title,
                    body=candidate.body, evidence=candidate.evidence,
                )
                decision = f"sent: inbox message {message_id}"
        self.store.set_notification_decision(cycle_id, decision)
        return decision
