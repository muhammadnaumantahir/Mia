"""Typed runtime-state and event contract for Mia's interface (MIA-HOLO-01).

One place that says which interface states exist, which *real* evidence may put Mia
in each state, how long that evidence stays believable, and what happens when there
is none. Nothing here renders anything and nothing is inferred from animation.

Rules, in short:

* A state exists only while fresh evidence of a registered kind supports it. Evidence
  older than its time-to-live is *stale* and ignored; with no fresh evidence the
  state is ``unknown``, never ``idle``. ``idle`` itself needs fresh positive evidence
  (the backend answered, or a turn finished).
* Unknown evidence kinds, future-dated evidence and server-only kinds sent by a
  client are *rejected* and reported, never mapped to a state.
* A newer event supersedes the older evidence it names (a finished chat turn ends
  "thinking"; a new turn replaces an old failure). ``offline`` wins over everything
  while the newest backend probe says unreachable.
* Evidence carries no free text from models or tools; ``detail`` is a short
  allow-listed set of flags.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = 1
MAX_CLIENT_EVIDENCE = 50
MAX_CLOCK_SKEW_SECONDS = 5.0
ALLOWED_DETAIL_KEYS = frozenset({"partial", "retryable", "count"})


class RuntimeState(StrEnum):
    UNKNOWN = "unknown"
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    TOOL_RUNNING = "tool_running"
    AWAITING_APPROVAL = "awaiting_approval"
    FAILED = "failed"
    OFFLINE = "offline"


class Source(StrEnum):
    BACKEND_PROBE = "backend_probe"
    CHAT_STREAM = "chat_stream"
    VOICE_SESSION = "voice_session"
    WAKE_WORD = "wake_word"
    COMPUTER_TASK = "computer_task"
    APPROVAL_STORE = "approval_store"


@dataclass(frozen=True)
class EvidenceKind:
    """One kind of real evidence: where it comes from and what it may show."""

    name: str
    source: Source
    state: RuntimeState
    ttl_seconds: float
    priority: int
    reporter: str  # "server" (read from a store) or "client" (reported by the page)
    backing: str  # the real API or event that produces it
    supersedes: tuple[str, ...] = ()


def _k(name, source, state, ttl, priority, reporter, backing, supersedes=()):
    return EvidenceKind(name, source, state, float(ttl), priority, reporter, backing,
                        tuple(supersedes))


_CHAT_ACTIVE = ("chat_turn_started", "chat_delta")
# A newer turn replaces what the previous turn left behind: an old failure, stop or "done" must not
# mask a reply that is being generated now (found by the HOLO-04 end-to-end test).
_CHAT_RESULTS = ("chat_error", "chat_done", "chat_stopped_by_owner")

EVIDENCE_KINDS: dict[str, EvidenceKind] = {k.name: k for k in (
    # Backend reachability. Answering this very request is itself evidence.
    _k("backend_reachable", Source.BACKEND_PROBE, RuntimeState.IDLE, 20, 10, "server",
       "GET /api/runtime/state answered", ("backend_unreachable",)),
    _k("backend_unreachable", Source.BACKEND_PROBE, RuntimeState.OFFLINE, 30, 100, "client",
       "a request to the backend failed at the network level", ("backend_reachable",)),
    # Text conversation, from POST /api/gateway/chat/stream events.
    _k("chat_turn_started", Source.CHAT_STREAM, RuntimeState.THINKING, 120, 60, "client",
       "a chat request was issued (POST /api/gateway/chat or /chat/stream)", _CHAT_RESULTS),
    _k("chat_delta", Source.CHAT_STREAM, RuntimeState.THINKING, 30, 60, "client",
       "SSE event: delta (a reply is being generated)", ("chat_turn_started", *_CHAT_RESULTS)),
    _k("chat_done", Source.CHAT_STREAM, RuntimeState.IDLE, 20, 10, "client",
       "SSE event: done", _CHAT_ACTIVE),
    _k("chat_error", Source.CHAT_STREAM, RuntimeState.FAILED, 60, 65, "client",
       "SSE event: error (detail.partial says whether text had arrived)", _CHAT_ACTIVE),
    _k("chat_stopped_by_owner", Source.CHAT_STREAM, RuntimeState.INTERRUPTED, 30, 40, "client",
       "the owner pressed Stop generating", _CHAT_ACTIVE),
    # Voice conversation, from the page's own voice session (no backend event exists).
    _k("voice_listening", Source.VOICE_SESSION, RuntimeState.LISTENING, 120, 50, "client",
       "voice session state 'listening' (microphone open, re-assert at least every 2 min)"),
    _k("voice_muted", Source.VOICE_SESSION, RuntimeState.IDLE, 120, 10, "client",
       "voice session state 'muted' (microphone off)", ("voice_listening",)),
    _k("voice_speaking", Source.VOICE_SESSION, RuntimeState.SPEAKING, 60, 70, "client",
       "voice session state 'speaking' (a reply is being spoken)"),
    _k("voice_interrupted", Source.VOICE_SESSION, RuntimeState.INTERRUPTED, 15, 40, "client",
       "owner barge-in or stop-speaking while a reply was spoken", ("voice_speaking",)),
    # Opt-in offline wake word, from the wake WebSocket session.
    _k("wake_listening", Source.WAKE_WORD, RuntimeState.LISTENING, 120, 50, "client",
       "wake-word session open on /api/voice/wake/stream (re-assert at least every 2 min)"),
    # Computer tasks, read from the task store.
    _k("task_running", Source.COMPUTER_TASK, RuntimeState.TOOL_RUNNING, 60, 80, "server",
       "GET /api/computer/tasks status 'running', aged by updated_at"),
    _k("task_failed", Source.COMPUTER_TASK, RuntimeState.FAILED, 120, 65, "server",
       "GET /api/computer/tasks status 'failed', aged by updated_at"),
    _k("task_interrupted", Source.COMPUTER_TASK, RuntimeState.INTERRUPTED, 300, 40, "server",
       "GET /api/computer/tasks status 'interrupted', aged by updated_at"),
    # Approvals, read from the approval store.
    _k("approval_pending", Source.APPROVAL_STORE, RuntimeState.AWAITING_APPROVAL, 30, 90,
       "server", "GET /api/computer/approvals entries with status 'pending'"),
)}

# Every non-unknown state must be reachable from at least one evidence kind.
UNSUPPORTED_STATES: frozenset[RuntimeState] = frozenset(
    set(RuntimeState) - {RuntimeState.UNKNOWN} - {k.state for k in EVIDENCE_KINDS.values()}
)

# What the interface may truthfully say in each state. No claims beyond the evidence.
STATE_STATEMENTS: dict[RuntimeState, str] = {
    RuntimeState.UNKNOWN: "Mia's current activity is not known.",
    RuntimeState.IDLE: "Nothing is happening that the app can see.",
    RuntimeState.LISTENING: "A listening session is open on this device.",
    RuntimeState.THINKING: "A reply is being generated.",
    RuntimeState.SPEAKING: "A reply is being spoken.",
    RuntimeState.INTERRUPTED: "The last activity was interrupted.",
    RuntimeState.TOOL_RUNNING: "A computer task is running.",
    RuntimeState.AWAITING_APPROVAL: "An action is waiting for the owner's approval.",
    RuntimeState.FAILED: "The last activity failed.",
    RuntimeState.OFFLINE: "The Mia backend cannot be reached.",
}


@dataclass(frozen=True)
class Evidence:
    kind: str
    observed_at: datetime
    detail: Mapping[str, Any] = field(default_factory=dict)
    reporter: str = "server"


@dataclass(frozen=True)
class RuntimeSnapshot:
    state: RuntimeState
    statement: str
    reason: str | None
    as_of: datetime
    since: datetime | None
    evidence_used: tuple[dict[str, Any], ...]
    stale: tuple[str, ...]
    rejected: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "state": self.state.value,
            "statement": self.statement,
            "reason": self.reason,
            "as_of": self.as_of.isoformat(),
            "since": self.since.isoformat() if self.since else None,
            "evidence_used": list(self.evidence_used),
            "stale": list(self.stale),
            "rejected": list(self.rejected),
        }


def _clean_detail(detail: Mapping[str, Any] | None) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in (detail or {}).items():
        if key in ALLOWED_DETAIL_KEYS and isinstance(value, (bool, int)):
            clean[key] = value
    return clean


def resolve(evidence: Iterable[Evidence], now: datetime | None = None) -> RuntimeSnapshot:
    """Reduce evidence to one state. Pure: same input, same output."""
    now = now or datetime.now(UTC)
    rejected: list[dict[str, str]] = []
    valid: list[Evidence] = []
    for item in evidence:
        kind = EVIDENCE_KINDS.get(item.kind)
        if kind is None:
            rejected.append({"kind": str(item.kind)[:60], "reason": "unknown_kind"})
        elif item.reporter == "client" and kind.reporter != "client":
            rejected.append({"kind": item.kind, "reason": "server_only_kind"})
        elif item.observed_at.tzinfo is None:
            rejected.append({"kind": item.kind, "reason": "naive_timestamp"})
        elif (item.observed_at - now).total_seconds() > MAX_CLOCK_SKEW_SECONDS:
            rejected.append({"kind": item.kind, "reason": "future_timestamp"})
        else:
            valid.append(item)

    # A newer event ends older evidence of the kinds it supersedes. On an exact tie it does so only
    # when the pair is one-directional: if both kinds list each other, neither wins the tie and
    # priority decides, so two simultaneous events can never cancel each other into "unknown".
    def replaces(other: Evidence, item: Evidence) -> bool:
        if other is item or item.kind not in EVIDENCE_KINDS[other.kind].supersedes:
            return False
        if other.observed_at > item.observed_at:
            return True
        return other.observed_at == item.observed_at and (
            other.kind not in EVIDENCE_KINDS[item.kind].supersedes)

    live: list[Evidence] = [
        item for item in valid if not any(replaces(other, item) for other in valid)
    ]

    fresh: list[Evidence] = []
    stale: list[str] = []
    for item in live:
        age = (now - item.observed_at).total_seconds()
        if age <= EVIDENCE_KINDS[item.kind].ttl_seconds:
            fresh.append(item)
        else:
            stale.append(item.kind)

    def snapshot(state, reason, since, used):
        return RuntimeSnapshot(
            state=state,
            statement=STATE_STATEMENTS[state],
            reason=reason,
            as_of=now,
            since=since,
            evidence_used=tuple(
                {"kind": e.kind, "observed_at": e.observed_at.isoformat(),
                 "detail": _clean_detail(e.detail)} for e in used
            ),
            stale=tuple(sorted(set(stale))),
            rejected=tuple(rejected),
        )

    if not fresh:
        return snapshot(RuntimeState.UNKNOWN, None, None, [])
    winner = max(fresh, key=lambda e: (EVIDENCE_KINDS[e.kind].priority, e.observed_at))
    kind = EVIDENCE_KINDS[winner.kind]
    return snapshot(kind.state, winner.kind, winner.observed_at, [winner])


def contract_description() -> dict[str, Any]:
    """The contract as data, so a client can show or check it without copying rules."""
    return {
        "contract_version": CONTRACT_VERSION,
        "states": [s.value for s in RuntimeState],
        "statements": {s.value: STATE_STATEMENTS[s] for s in RuntimeState},
        "evidence_kinds": [
            {
                "name": k.name, "source": k.source.value, "state": k.state.value,
                "ttl_seconds": k.ttl_seconds, "priority": k.priority,
                "reporter": k.reporter, "backing": k.backing,
                "supersedes": list(k.supersedes),
            }
            for k in EVIDENCE_KINDS.values()
        ],
        "rules": [
            "No fresh evidence means 'unknown', never 'idle'.",
            "Stale evidence is ignored and listed, never believed.",
            "Unknown kinds, future timestamps and server-only kinds from clients are rejected.",
            "A newer terminal event supersedes older activity of the same source.",
            "Highest priority among fresh evidence wins; ties go to the newest.",
            "Clients send age_seconds, not timestamps, so device clocks do not matter.",
        ],
    }


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def collect_server_evidence(
    *, approvals: Any, tasks: Any, now: datetime | None = None
) -> list[Evidence]:
    """Evidence the server can read itself: reachability, approvals and computer tasks."""
    now = now or datetime.now(UTC)
    found = [Evidence("backend_reachable", now)]
    try:
        # list_pending() does not filter by expiry, so an expired request would look
        # like a waiting approval forever; only unexpired ones count.
        pending = [
            a for a in approvals.list_pending()
            if a.get("status") == "pending"
            and ((expires := _parse_time(a.get("expires_at"))) is None or expires > now)
        ]
    except Exception:  # an unreadable store is "no evidence", not a guessed state
        pending = []
    if pending:
        found.append(Evidence("approval_pending", now, {"count": min(len(pending), 999)}))
    try:
        listed = tasks.list_tasks(50)
    except Exception:
        listed = []
    statuses = {"running": "task_running", "failed": "task_failed",
                "interrupted": "task_interrupted"}
    seen: set[str] = set()
    for task in listed:  # newest first; one piece of evidence per kind is enough
        kind = statuses.get(str(task.get("status")))
        stamp = _parse_time(task.get("updated_at"))
        if kind and kind not in seen and stamp is not None:
            seen.add(kind)
            found.append(Evidence(kind, stamp))
    return found


class ClientEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=60)
    age_seconds: float = Field(default=0.0, ge=0.0, le=86400.0)
    detail: dict[str, Any] = Field(default_factory=dict)


class ResolveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: list[ClientEvidence] = Field(default_factory=list, max_length=MAX_CLIENT_EVIDENCE)


def register_runtime_routes(app: FastAPI, *, approvals: Any, tasks: Any) -> None:
    """Attach the read-only runtime-state routes. They change nothing."""

    @app.get("/api/runtime/contract")
    def runtime_contract() -> dict[str, Any]:
        return contract_description()

    @app.get("/api/runtime/state")
    def runtime_state() -> dict[str, Any]:
        now = datetime.now(UTC)
        return resolve(collect_server_evidence(approvals=approvals, tasks=tasks, now=now),
                       now).to_dict()

    @app.post("/api/runtime/resolve")
    def runtime_resolve(item: ResolveInput) -> dict[str, Any]:
        """Resolve client-reported evidence together with what the server can see."""
        now = datetime.now(UTC)
        if len(item.evidence) > MAX_CLIENT_EVIDENCE:
            raise HTTPException(status_code=422, detail="too much evidence")
        client = [
            Evidence(e.kind, now - timedelta(seconds=e.age_seconds), e.detail, "client")
            for e in item.evidence
        ]
        server = collect_server_evidence(approvals=approvals, tasks=tasks, now=now)
        return resolve([*server, *client], now).to_dict()
