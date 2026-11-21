"""Budgeted context construction and reference resolution (SPS-04/SPS-05).

Uses the existing memory APIs (``assemble_memory_context``, ``MemoryStore``); it
does not create a second memory database. Memory is retrieved only when the
analysed intent warrants it, and the context is trimmed to a token budget in
priority order: identity > current request > recent turns > retrieved memory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from mia.gateway.contracts import ModelCandidate
from mia.memory.context import ContextBudget, assemble_memory_context
from mia.memory.semantic import LocalOllamaEmbeddingReranker
from mia.memory.store import MemoryStore, project_key

from .intent import IntentAnalysis

_CHARS_PER_TOKEN = 4


def estimate_tokens(messages: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> int:
    return max(1, sum(len(str(m.get("content") or "")) for m in messages) // _CHARS_PER_TOKEN)


@dataclass(frozen=True, slots=True)
class BuiltContext:
    messages: tuple[dict[str, Any], ...]
    references: tuple[dict[str, Any], ...]
    candidates: tuple[ModelCandidate, ...]
    estimated_tokens: int
    memory_retrieved: bool
    memory_withheld: bool
    resolved_references: tuple[str, ...] = ()
    trimmed_messages: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)


def _remote_context_allowed() -> bool:
    return os.getenv("MIA_ALLOW_REMOTE_FILE_CONTEXT", "false").strip().lower() == "true"


def _budget_for(analysis: IntentAnalysis) -> ContextBudget:
    if analysis.complexity == "complex":
        return ContextBudget(max_knowledge=6, max_messages=5, max_records=5, max_chars=7000)
    return ContextBudget()


# How many of the most recently updated conversations are searched for recall. The
# eligibility check and the tail retrieval below share this, so "is there a prior
# session?" can never disagree with "what was in it?".
_PRIOR_CONVERSATION_SCAN = 50


def _prior_persistent_conversation_ids(store: MemoryStore, conversation_id: str):
    """Yield ids of other, non-temporary conversations that have messages, best first.

    Temporary chats are deliberately excluded from all cross-session recall, and the
    current conversation is never its own predecessor.

    Project scoping (owner opt-in): when the current conversation belongs to a project,
    that project's conversations come first (newest first), then unassigned ones. A
    different project's conversations are never recalled into it. A conversation with
    no project keeps the original behaviour (newest eligible, any project).
    """
    rows = store.list_conversations(limit=_PRIOR_CONVERSATION_SCAN)
    current = next(
        (c for c in rows if (c.get("id") or c.get("conversation_id")) == conversation_id), None
    )
    if current is None:  # current chat fell outside the scan window; look it up directly
        current = store.get_conversation(conversation_id) or {}
    wanted = project_key(current.get("project"))

    same: list[str] = []
    unassigned: list[str] = []
    for convo in rows:
        if convo.get("temporary") or not convo.get("message_count", 0):
            continue
        cid = convo.get("id") or convo.get("conversation_id")
        if not cid or cid == conversation_id:
            continue
        key = project_key(convo.get("project"))
        if wanted is None:
            same.append(cid)  # original behaviour: any eligible conversation
        elif key == wanted:
            same.append(cid)
        elif key is None:
            unassigned.append(cid)
        # else: another project's conversation, never recalled here
    yield from same
    yield from unassigned


def _prior_conversation_tail(
    store: MemoryStore, conversation_id: str, limit: int = 6
) -> list[dict[str, Any]]:
    """Most recent turns from the latest *other* conversation (for 'continue where we stopped')."""
    for cid in _prior_persistent_conversation_ids(store, conversation_id):
        msgs = store.get_messages(cid, limit=limit)
        if msgs:
            return msgs
    return []


def has_persistent_prior_conversation(store: MemoryStore, conversation_id: str) -> bool:
    """Whether intent analysis may safely assume a prior persistent session exists."""
    return next(_prior_persistent_conversation_ids(store, conversation_id), None) is not None


def build_context(
    store: MemoryStore,
    *,
    identity_text: str,
    prompt: str,
    conversation_id: str,
    analysis: IntentAnalysis,
    candidates: list[ModelCandidate],
    required_capabilities: set[str] | frozenset[str],
    recent_messages: list[dict[str, Any]],
    token_budget: int = 6000,
) -> BuiltContext:
    notes: list[str] = []
    refs: list[dict[str, Any]] = []
    routed = list(candidates)
    memory_block: list[dict[str, Any]] = []
    retrieved = withheld = False
    resolved: list[str] = []

    enabled = os.getenv("MIA_CONTEXT_RETRIEVAL", "true").strip().lower() != "false"
    # Retrieval is skipped only for trivial turns (greetings/acknowledgements); the
    # retriever itself still requires distinctive term matches before injecting anything.
    wants_memory = enabled and (analysis.needs_memory or analysis.category != "simple")

    extra_turns: list[dict[str, Any]] = []
    if "continuation" in analysis.references and len(recent_messages) <= 1:
        tail = _prior_conversation_tail(store, conversation_id)
        if tail:
            extra_turns = [
                {"role": m["role"], "content": m["content"]} for m in tail
                if m.get("role") in {"user", "assistant"}
            ]
            resolved.append("continuation:previous_conversation")

    if wants_memory:
        query = prompt  # the retriever interprets contextual references itself
        try:
            packet = assemble_memory_context(
                store, query, conversation_id, budget=_budget_for(analysis),
                reranker=LocalOllamaEmbeddingReranker.from_env(cache=store),
            )
        except Exception:  # retrieval must never block a reply
            packet = None
            notes.append("memory retrieval failed; continuing without it")
        if packet is not None and not packet.empty and packet.message is not None:
            refs = [r.to_dict() for r in packet.references]
            required = set(required_capabilities)
            if not _remote_context_allowed():
                local_ok = [
                    c for c in candidates
                    if c.data_local and c.enabled and c.healthy and required <= set(c.capabilities)
                ]
                if local_ok:
                    routed = [c for c in candidates if c.data_local]
                    notes.append("memory present: routing restricted to local models")
                else:
                    withheld = True
                    refs = []
                    notes.append("memory withheld: no local model can serve this request")
            if not withheld:
                memory_block = [packet.message]
                retrieved = True

    system = {"role": "system", "content": identity_text}
    fixed = [system, *memory_block]
    budget_left = token_budget - estimate_tokens(fixed)
    # Newest turns first; always keep the current request (last message).
    history = [{"role": m["role"], "content": m["content"]} for m in recent_messages]
    kept: list[dict[str, Any]] = []
    trimmed = 0
    for message in reversed(history):
        cost = estimate_tokens([message])
        if kept and budget_left - cost < 0:
            trimmed += 1
            continue
        kept.append(message)
        budget_left -= cost
    kept.reverse()
    if extra_turns and budget_left > 0:
        # Recent turns in the active conversation take precedence. Add only the
        # newest contiguous suffix of the prior conversation that fits the budget.
        selected: list[dict[str, Any]] = []
        for message in reversed(extra_turns):
            cost = estimate_tokens([message])
            if cost > budget_left:
                break
            selected.append(message)
            budget_left -= cost
        if selected:
            kept = [*reversed(selected), *kept]
    if trimmed:
        notes.append(f"trimmed {trimmed} older message(s) to fit the context budget")
    messages = tuple([system, *memory_block, *kept])
    return BuiltContext(
        messages=messages,
        references=tuple(refs),
        candidates=tuple(routed),
        estimated_tokens=estimate_tokens(messages),
        memory_retrieved=retrieved,
        memory_withheld=withheld,
        resolved_references=tuple(resolved),
        trimmed_messages=trimmed,
        notes=tuple(notes),
    )
