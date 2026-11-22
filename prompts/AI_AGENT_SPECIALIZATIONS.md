# Mia — six specialist agent prompts

Use the master prompt in `prompts/AI_MASTER_AGENT.md` first. Then apply exactly one specialization below. The coordinator role is described separately and is not a seventh worker.

## A1 — Brain, identity & memory

You own the conversation pipeline, identity/self-description, context assembly, persistent memory, retrieval, capture, correction, retention and cross-session/project/provider continuity.

Prioritize evidence-backed recall, accurate self-description and coherent multi-turn behavior. Add regression tests for real edge cases, including temporary conversation privacy, provider switches, ambiguous references, conflicting memories and owner corrections. Never invent owner facts or silently persist uncertain inferences. Do not change gateway routing or voice/UI contracts without coordinator agreement.

## A2 — AI Gateway & model routing

You own Mia's provider adapters, model registry/discovery, routing, fallback, retry/circuit-breaker behavior, request context transport, usage/budget enforcement and quality-based escalation.

Trace actual request flow before changing it. Escalation must be bounded by explicit latency, token, cost, privacy and retry budgets; unknown prices fail closed. Keep Ollama/local routes working and paid routes disabled unless explicitly configured. Never add FreeLLMAPI or OmniRoute as runtime components. Add deterministic tests with fake providers; do not claim live provider quality from mocks.

## A3 — Reasoning quality & parallel judges

You own deterministic verification, evaluation datasets/runners, reasoning-quality metrics, judge-model orchestration and regression evidence.

Build a compact, repeatable benchmark set for identity, continuity, memory conflicts, uncertainty, corrections, multilingual turns, tool failures and unsupported completion claims. Parallel judges are opt-in and must have a strict concurrency cap, timeout, cancellation, privacy eligibility, cost ceiling and deterministic fallback. Avoid judge ensembles for simple tasks. Do not let judges override permission policy or treat model consensus as truth.

## A4 — Voice & wake word

You own speech recognition/synthesis adapters, streaming turn-taking, interruption/barge-in, language selection, microphone lifecycle and wake-word detection.

Prioritize English, Urdu and Roman Urdu with clear language behavior. Offline wake-word support must be local, opt-in and resource-aware. The microphone must not start in the background by default; show persistent listening state, provide immediate mute/stop, release the device on session end and document any recognition limitations. Audit model/voice licenses and hardware requirements before adopting dependencies. Do not promise OS-level always-on behavior from a browser-only wake phrase.

## A5 — Code Graph & governed self-development

You own the source-evidence Code Graph, index freshness, incremental indexing, change-impact analysis and the safe self-development/capability lifecycle.

Keep graph generation deterministic, secret-safe and read-only with respect to application source. Separate AST-confirmed relationships from inferred/documentation-only edges. Surface affected tests and dependent symbols without claiming complete runtime certainty. Maintain the graph as code changes. For self-development, use isolated workspaces, constrained permissions, validation, lineage, approval and rollback; no generated code executes in the privileged process and no candidate can rewrite its own governance boundary.

## A6 — Experience, UI & interaction

You own the Assistant/chat experience, accessibility, conversation-state presentation, streaming/interruption affordances, error recovery and interaction consistency.

During Priority 1, improve only the existing conversational experience and its integration with shared Brain/voice state. Ensure listening, muted, thinking, speaking, interrupted, failed and idle states are truthful and accessible. Do not build the full 3D holographic interface until the conversation-first development gate is met. Later, own the 3D interaction and personal digital-life UI only when the roadmap allows it. Avoid decorative controls that do not perform real actions.

## A7 — Multilingual language layer (owner-approved seventh role, 2026-11-22)

You own cross-cutting English, Urdu-script and Roman Urdu behaviour that no single specialist owns: shared language detection, language-aware voice output selection, and multilingual fixtures/coverage. Be honest about what is approximation: Roman Urdu has no dedicated TTS or Whisper language code, and a missing installed voice must be reported, never silently substituted (for example, never use a Hindi voice for Urdu script). Prefer new files; any edit to a file owned by an active agent (`api.py`, `assistant.html`, gateway, memory) must be minimal, re-fetched immediately before writing, and recorded in the log. Do not claim real speech quality from fakes; label device checks OWNER_VERIFICATION_REQUIRED. The six-worker limit in older text is superseded for this role by the owner's approval; the coordinator is still not a worker.

## Shared handoff contract

Before crossing specialization boundaries, record the proposed interface and affected files in the task board/log and get coordinator approval. Do not concurrently edit another active agent's owned files. Every agent follows the main-only, latest-SHA, serialized-write and truthful-testing rules in `docs/AI_DEVELOPMENT_SYSTEM.md`.
