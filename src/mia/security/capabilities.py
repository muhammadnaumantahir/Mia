"""Typed inventory of every state-changing API route and its risk class (MIA-COMP-01).

This is a *declaration*, not enforcement. Enforcement (a permission broker that refuses
un-approved high-risk calls) is MIA-COMP-04. What this module adds is a machine-checked list: a
test enumerates the real FastAPI routes and fails when a mutating route has no entry, so new
endpoints cannot ship without someone classifying them. ``declared`` means the classification was
assigned by reading route names, not proven by tracing each handler.

Rules are matched top to bottom as ``"METHOD /path"`` globs; the first match wins, so specific
rules must precede general ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase


class SideEffect(StrEnum):
    INTERNAL_STATE = "internal_state"  # Mia's own stores, settings, telemetry
    OWNER_DATA = "owner_data"  # memory, tasks, projects, identity: the owner's content
    DEVICE_CONTROL = "device_control"  # browser, terminal, desktop, files, processes, apps
    CODE_CHANGE = "code_change"  # self-development: capabilities, experiments, code graph
    EXTERNAL_NETWORK = "external_network"  # outbound inference or web fetch
    SAFETY_CONTROL = "safety_control"  # pause, stop, revoke: reduce Mia's power


class ApprovalClass(StrEnum):
    NONE = "none"  # direct owner action in the UI, low risk
    PREVIEW_THEN_EXECUTE = "preview_then_execute"  # execute needs a prior preview/approval record
    GRANT_GATED = "grant_gated"  # refused (409) unless an active owner grant covers it
    OWNER_CONFIRM = "owner_confirm"  # destructive or wide; UI must confirm
    ALWAYS_ALLOWED = "always_allowed"  # safety stops must never be blocked by approval


@dataclass(frozen=True)
class Capability:
    pattern: str
    side_effect: SideEffect
    approval: ApprovalClass
    scope: str
    recovery: str  # how the owner undoes or contains it
    declared: bool = True


_S, _A = SideEffect, ApprovalClass
_RULES: tuple[Capability, ...] = (
    # Safety controls first: they must stay reachable and are never approval-gated.
    Capability("POST /api/computer/stop-all", _S.SAFETY_CONTROL, _A.ALWAYS_ALLOWED, "computer", "POST /api/computer/resume"),
    Capability("POST /api/computer/resume", _S.SAFETY_CONTROL, _A.NONE, "computer", "POST /api/computer/stop-all"),
    Capability("POST /api/computer/permissions/revoke-all", _S.SAFETY_CONTROL, _A.ALWAYS_ALLOWED, "computer", "grant again"),
    Capability("POST /api/life/emergency-stop", _S.SAFETY_CONTROL, _A.ALWAYS_ALLOWED, "life", "POST /api/life/emergency-stop/clear"),
    Capability("POST /api/life/emergency-stop/clear", _S.SAFETY_CONTROL, _A.NONE, "life", "emergency-stop"),
    Capability("POST /api/life/pause", _S.SAFETY_CONTROL, _A.ALWAYS_ALLOWED, "life", "POST /api/life/resume"),
    Capability("POST /api/life/resume", _S.SAFETY_CONTROL, _A.NONE, "life", "POST /api/life/pause"),
    Capability("POST /api/autonomy/pause", _S.SAFETY_CONTROL, _A.ALWAYS_ALLOWED, "autonomy", "POST /api/autonomy/resume"),
    Capability("POST /api/autonomy/resume", _S.SAFETY_CONTROL, _A.NONE, "autonomy", "POST /api/autonomy/pause"),
    Capability("POST /api/voice/wake/stop", _S.SAFETY_CONTROL, _A.ALWAYS_ALLOWED, "microphone", "owner re-enables listening"),
    Capability("WS /api/voice/wake/stream", _S.INTERNAL_STATE, _A.NONE, "microphone", "POST /api/voice/wake/stop; opt-in and visible in the UI"),
    Capability("DELETE /api/computer/permissions/*", _S.SAFETY_CONTROL, _A.ALWAYS_ALLOWED, "computer", "grant again"),
    Capability("POST /api/computer/tasks/*/cancel", _S.SAFETY_CONTROL, _A.ALWAYS_ALLOWED, "computer", "create a new task"),
    Capability("POST /api/computer/terminal/executions/*/cancel", _S.SAFETY_CONTROL, _A.ALWAYS_ALLOWED, "computer", "re-run"),
    Capability("POST /api/workflows/*/cancel", _S.SAFETY_CONTROL, _A.ALWAYS_ALLOWED, "workflows", "compose a new workflow"),
    Capability("POST /api/workflows/*/pause", _S.SAFETY_CONTROL, _A.ALWAYS_ALLOWED, "workflows", "POST .../run"),
    Capability("POST /api/tasks/*/cancel", _S.SAFETY_CONTROL, _A.ALWAYS_ALLOWED, "agent tasks", "re-propose"),
    Capability("POST /api/tasks/*/reject", _S.SAFETY_CONTROL, _A.NONE, "agent tasks", "re-propose"),
    Capability("POST /api/approvals/*/reject", _S.SAFETY_CONTROL, _A.NONE, "approvals", "re-request"),
    Capability("POST /api/computer/tasks/*/pause", _S.SAFETY_CONTROL, _A.ALWAYS_ALLOWED, "computer", "POST .../run"),
    # Device control: every execute is preceded by a preview and an approval record.
    Capability("POST /api/*/execute", _S.DEVICE_CONTROL, _A.PREVIEW_THEN_EXECUTE, "device", "stop-all; per-action undo where the action supports it"),
    Capability("POST /api/computer/*/execute", _S.DEVICE_CONTROL, _A.PREVIEW_THEN_EXECUTE, "device", "stop-all"),
    Capability("POST /api/computer/approvals/*/approve", _S.DEVICE_CONTROL, _A.OWNER_CONFIRM, "device", "revoke permissions"),
    Capability("POST /api/computer/permissions", _S.DEVICE_CONTROL, _A.OWNER_CONFIRM, "device", "DELETE grant / revoke-all"),
    Capability("POST /api/browser/click/*", _S.DEVICE_CONTROL, _A.PREVIEW_THEN_EXECUTE, "browser", "POST /api/browser/close; stop-all"),
    Capability("POST /api/browser/actions/*", _S.DEVICE_CONTROL, _A.PREVIEW_THEN_EXECUTE, "browser", "POST /api/browser/close; stop-all"),
    Capability("POST /api/browser/close", _S.SAFETY_CONTROL, _A.ALWAYS_ALLOWED, "browser", "navigate again"),
    Capability("POST /api/browser/navigate", _S.EXTERNAL_NETWORK, _A.NONE, "browser", "low-risk read class; policy-checked; POST /api/browser/close"),
    Capability("POST /api/browser/*", _S.DEVICE_CONTROL, _A.GRANT_GATED, "browser", "revoke grant; POST /api/browser/close; stop-all"),
    Capability("POST /api/computer/*", _S.DEVICE_CONTROL, _A.PREVIEW_THEN_EXECUTE, "device", "stop-all"),
    Capability("POST /api/desktop/*", _S.DEVICE_CONTROL, _A.PREVIEW_THEN_EXECUTE, "desktop", "stop-all"),
    # Self-development and code.
    Capability("POST /api/capabilities*", _S.CODE_CHANGE, _A.PREVIEW_THEN_EXECUTE, "self-development", "POST /api/capabilities/*/rollback"),
    Capability("POST /api/experiments/*", _S.CODE_CHANGE, _A.PREVIEW_THEN_EXECUTE, "self-development", "snapshots allow restore"),
    Capability("POST /api/agent/discovery/*", _S.CODE_CHANGE, _A.NONE, "self-development", "proposal only; nothing runs"),
    Capability("POST /api/code-graph/refresh", _S.INTERNAL_STATE, _A.NONE, "code graph", "regenerated on demand"),
    Capability("POST /api/tasks*", _S.CODE_CHANGE, _A.PREVIEW_THEN_EXECUTE, "agent tasks", "reject/cancel"),
    Capability("POST /api/planning/*", _S.INTERNAL_STATE, _A.NONE, "planning", "proposal only"),
    Capability("POST /api/brain/plans/*", _S.INTERNAL_STATE, _A.NONE, "brain plans", "cancel plan"),
    Capability("POST /api/approvals/*/execute", _S.DEVICE_CONTROL, _A.PREVIEW_THEN_EXECUTE, "approvals", "stop-all"),
    # Outbound inference and web.
    Capability("POST /api/gateway/*", _S.EXTERNAL_NETWORK, _A.NONE, "gateway", "paid inference is off by default; budget limits apply"),
    Capability("POST /api/runtime/resolve", _S.INTERNAL_STATE, _A.NONE, "gateway", "read-like resolution"),
    Capability("POST /api/research/runs", _S.EXTERNAL_NETWORK, _A.NONE, "research", "read-only fetches of owner-allowlisted hosts; DELETE run; emergency stop honoured"),
    Capability("POST /api/research/*", _S.OWNER_DATA, _A.NONE, "research", "reject claim; DELETE run"),
    Capability("POST /api/knowledge/ingest", _S.EXTERNAL_NETWORK, _A.NONE, "knowledge", "delete ingested items"),
    Capability("*/api/life/sources*", _S.EXTERNAL_NETWORK, _A.NONE, "life", "DELETE source"),
    Capability("POST /api/life/cycle", _S.EXTERNAL_NETWORK, _A.NONE, "life", "pause/emergency stop"),
    # Wide or destructive owner-data operations.
    Capability("POST /api/workflows*", _S.OWNER_DATA, _A.NONE, "workflows", "internal non-destructive steps only; completed tasks can be reopened; cancel/pause"),
    Capability("POST /api/memory/purge", _S.OWNER_DATA, _A.OWNER_CONFIRM, "memory", "none after purge"),
    Capability("DELETE /api/*", _S.OWNER_DATA, _A.OWNER_CONFIRM, "owner data", "none; UI must confirm"),
    Capability("POST /api/identity/rollback", _S.OWNER_DATA, _A.NONE, "identity", "versioned"),
    Capability("PUT /api/identity", _S.OWNER_DATA, _A.NONE, "identity", "POST /api/identity/rollback"),
    Capability("*/api/memory/*", _S.OWNER_DATA, _A.NONE, "memory", "restore/outdated/retention controls"),
    Capability("*/api/personal/*", _S.OWNER_DATA, _A.NONE, "tasks/notes", "edit or reopen"),
    Capability("*/api/projects*", _S.OWNER_DATA, _A.NONE, "projects", "edit"),
    Capability("*/api/conversations/*", _S.OWNER_DATA, _A.NONE, "conversations", "edit"),
    # Mia's own state.
    Capability("*/api/evolution/gaps*", _S.INTERNAL_STATE, _A.NONE, "capability gaps",
               "records and ranks consented signals only, nothing runs; revoking a source deletes its evidence; dismiss or delete a gap"),
    Capability("*/api/notifications/*", _S.INTERNAL_STATE, _A.NONE, "notifications", "settings"),
    Capability("*/api/life/*", _S.INTERNAL_STATE, _A.NONE, "life", "settings"),
    Capability("POST /api/observatory/*", _S.INTERNAL_STATE, _A.NONE, "telemetry", "append-only"),
    Capability("POST /api/voice/*", _S.INTERNAL_STATE, _A.NONE, "voice", "audio is not retained"),
)


def classify(method: str, path: str) -> Capability | None:
    key = f"{method.upper()} {path}"
    for rule in _RULES:
        if fnmatchcase(key, rule.pattern):
            return rule
    return None


def all_rules() -> tuple[Capability, ...]:
    return _RULES
