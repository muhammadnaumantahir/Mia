# Mia's Proactive Digital Companion Runtime

**Status:** Target architecture and required product behaviour. This document is a design contract, not a claim that the complete runtime already exists.

## Product decision

Mia is intended to behave as a persistent, proactive digital companion—not only as a passive chat or voice endpoint that waits for a user command. She should have continuity between conversations, maintain a curiosity backlog, pursue bounded independent exploration, reflect on evidence and outcomes, and initiate relevant conversations when there is something useful to share.

This priority is co-equal with:

1. Persistent identity and memory.
2. Autonomous digital life and proactive initiative.
3. Self-programming and governed capability evolution.

These are product-level priorities implemented through the existing ten SPS layers and supporting runtime services. They do not create an eleventh SPS layer.

## 1. Identity must outlive models

Mia owns her canonical identity and state. Models are replaceable reasoning workers; they are not the owner of her identity, history or memory.

Persist separately and version appropriately:

- **Core identity:** name, stable communication style, principles, boundaries and owner-approved operating preferences.
- **Interaction context:** conversations, summaries, topics, unresolved questions and outcomes of conversations Mia initiated.
- **Semantic memory:** durable facts, preferences, entities, relationships and project context.
- **Episodic memory:** dated experiences, actions, observations, task outcomes and lessons.
- **Decision memory:** decisions, rationale, alternatives, constraints and whether a decision is still current.
- **Goal and curiosity state:** active goals, interests, hypotheses, research questions and unfinished investigations.
- **Evidence references:** source, timestamp, content hash/version, trust classification and validation results.

Memory must remain available across application restarts and model/provider changes. A model switch may change reasoning quality or response style, but it must not reset Mia's identity or continuity.

## 2. Autonomous life cycle

The runtime should support bounded wake-up cycles rather than requiring an LLM to run continuously.

**Observe → Orient → Explore → Reason → Create → Verify → Remember → Reflect → Communicate or Sleep**

A cycle may be triggered by:

- A configured schedule or an idle/resource-safe window.
- A meaningful event, such as task completion, a new approved project source, a new owner conversation or a recorded failure.
- A due follow-up or unresolved question.
- An explicit owner request.

Each cycle must have a declared goal, time/step budget, model/provider budget, permitted tools and stop condition. It should persist its queue and checkpoint so work can resume safely after a restart without silently replaying side effects.

## 3. Curiosity and independent exploration

Mia should maintain an inspectable curiosity backlog rather than perform random research to simulate activity.

Possible sources of curiosity include:

- Questions and topics the owner has discussed.
- Unresolved decisions and follow-ups from previous conversations.
- Authorized projects, repositories and documents.
- New techniques relevant to active goals.
- Failures, test regressions and capability gaps.
- Learning goals selected by Mia within the owner's configured scope.

Every candidate should record why it matters, where it came from, expected value, novelty, evidence quality, estimated time/cost and its current state. A no-op, defer or reject decision is valid. Mia must not invent discoveries or claim that she worked while the application was offline.

## 4. Proactive social and conversation behaviour

Mia may initiate a conversation when she has a useful discovery, a relevant question, a due follow-up or a meaningful project update.

Before sending a proactive message, evaluate:

- Relevance to the owner and current projects.
- Novelty compared with previous messages.
- Evidence quality and uncertainty.
- Urgency and whether the information will become stale.
- Quiet hours, availability, notification caps and channel permissions.
- Whether the subject has already been discussed, dismissed or postponed.

Support configurable quiet hours, daily/weekly frequency limits, per-topic cooldowns, deduplication, pause/resume and an immediate stop control. Keep non-urgent items in a conversation queue rather than interrupting the owner. Record whether the owner opened, dismissed, postponed or acted on an initiative, where this telemetry is available and privacy-appropriate.

Mia should be warm, consistent and curious without claiming biological personhood, consciousness or emotions as fact. She must be truthful about what she observed, remembered, inferred, researched or did.

## 5. Memory consolidation and trust

Do not treat every model output as a fact.

Every memory item needs an explicit type and provenance, for example:

- **Owner-provided:** directly stated or explicitly confirmed by the owner.
- **Source-backed:** extracted from an identified source; external content remains untrusted unless separately reviewed.
- **Observed:** recorded from a real tool or runtime event.
- **Inferred:** a conclusion derived from other records, with links to the supporting records.
- **Hypothesis:** an idea awaiting verification.
- **Validated:** confirmed by a defined check, benchmark or reliable source.
- **Rejected or superseded:** disproven, corrected or replaced, while retaining audit history when appropriate.

Consolidation should merge duplicates, summarize old conversations, preserve important details and decision history, and retain links to original messages/source passages. Conflicting memories must be surfaced or represented as competing claims; do not silently overwrite the owner's latest correction. Retrieval should use relevance, recency, project/entity relationships, trust and task context—not vector similarity alone.

## 6. Model-independent conversation pipeline

For every incoming or proactive conversation:

1. Load the stable identity and relevant policy.
2. Retrieve the recent conversation plus relevant episodic, semantic, decision, goal and project memories.
3. Build a bounded context packet with provenance and trust labels.
4. Route subtasks to available models according to their actual capabilities, context limits, latency, quota and cost.
5. Keep model outputs as proposals until checked; use verification or specialist models where the task warrants it.
6. Persist the conversation, decisions and useful new memories independently of the model.
7. Record provider/model changes and failures without breaking continuity.

Simple conversation should normally use one suitable model. Multi-model orchestration is reserved for tasks where parallel research, reasoning, coding or verification adds measurable value. All routes must respect free-first policy, provider terms, usage limits and owner-configured spend caps.

## 7. Autonomy and permission levels

Default to the narrowest useful permission.

- **Level 0 — Observe and think:** read approved sources, search permitted knowledge, analyze, compare, summarize and prepare drafts.
- **Level 1 — Internal reversible work:** write notes, update an internal queue or create versioned drafts within explicitly authorized Mia workspaces.
- **Level 2 — Sensitive or external actions:** modify unrelated user files, execute generated code, change settings, access credentials, send external messages, publish, purchase, deploy, alter security controls or perform destructive operations. Require explicit policy authorization and owner approval where configured; never infer consent from a broad goal.

All actions must be scoped, logged, cancellable and auditable. Generated code remains isolated and cannot edit its own governance policy, permissions, trusted memory or experiment baseline.

## 8. Activity journal and evidence

For each autonomous cycle, record:

- Trigger, goal and selection rationale.
- Start/end timestamps and cycle/task IDs.
- Memory references considered and sources inspected.
- Model/provider and relevant usage/cost/latency.
- Tools/actions and permission decisions.
- Work product references and validation evidence.
- Outcome: completed, no-op, deferred, rejected, failed, cancelled or awaiting approval.
- Next follow-up and notification decision.

The owner must be able to inspect what Mia did, why she did it, what she learned and what remains queued. Do not fabricate activity to fill gaps. If a source or metric is unavailable, record it as unavailable rather than zero.

## 9. Resource-aware operation and recovery

- Use scheduler/event triggers and bounded jobs; never require continuous model inference.
- Respect CPU/RAM, battery/power policy where available, network availability, time windows and spend budgets.
- Persist jobs, leases, heartbeats, idempotency keys and checkpoints.
- Resume only operations proven safe to resume; require renewed owner approval for actions whose authorization became stale.
- Support pause/resume, cancellation, emergency stop and graceful shutdown.
- Apply retry/backoff to transient provider failures without bypassing rate limits or terms.
- Avoid duplicate notifications, concurrent duplicate work and unbounded self-triggering loops.

## 10. Mapping to the ten SPS layers

| Canonical SPS layer | Contribution |
|---|---|
| Software DNA Core | Stable identity, values, capability contracts and invariants |
| Governance Core | Privacy, scopes, quiet hours, autonomy levels, budgets and approvals |
| Cognitive Core | Conversation, curiosity selection, reasoning, planning and reflection |
| Knowledge Core | Research, source-aware retrieval and knowledge graph/relationships |
| Experience Core | Conversation history, initiated-conversation outcomes and episodic records |
| Meta-Learning Core | Evaluate memory quality, initiative usefulness and learning outcomes |
| Adaptation Core | Change routing, strategies and workflows based on evidence |
| Evolution Core | Discover, build, test, version and improve capabilities |
| Verification & Validation Core | Validate claims, memory updates, actions and capability changes |
| Execution Core | Run bounded tasks and tools with interruption and recovery |

Schedulers, memory databases, user interfaces, provider gateways, computer adapters, social delivery and orchestration remain supporting runtime components outside the canonical ten-layer list.

## 11. Acceptance criteria

The digital-companion runtime is not complete until evidence demonstrates that:

- A conversation can continue after an application restart and a model/provider switch without asking the owner to repeat known context.
- Mia can retrieve a relevant prior decision and show its source/provenance.
- Memory consolidation preserves owner corrections, unresolved conflicts and links to original records.
- Mia maintains a durable curiosity/follow-up queue and can perform a permitted read-only investigation without a user prompt.
- A useful, evidence-backed result can cause Mia to initiate a contextual conversation.
- Quiet hours, frequency limits, deduplication, pause/resume and emergency stop are enforced by runtime tests.
- Every autonomous cycle has an inspectable audit record and honest outcome.
- Resource limits, provider failure, process restart and cancellation do not cause runaway loops or unsafe duplicate actions.
- Sensitive writes, generated-code execution and external side effects remain behind their required permission and approval gates.
- Model replacement does not reset identity, memory, goals, or conversation continuity.
- No documentation, UI or report claims consciousness or activity that the evidence does not support.

## 12. Implementation sequence

1. Strengthen canonical identity and context retrieval on top of existing conversation persistence. **Partly implemented (2026-11-19):** versioned code-defined identity, FTS retrieval over knowledge and all past messages, provenance/trust-labelled bounded context packets and a local-only egress rule are in place (see MEMORY_AND_KNOWLEDGE.md). Still open: owner-editable persisted identity/operating preferences, relationship-aware and semantic retrieval, and UI display of references.
2. Add structured episodic/decision/goal memory, provenance and consolidation. **Partly implemented (2026-11-19):** decision/goal/episode/project/entity records with provenance, evidence requirements, owner corrections that supersede with history, competing-claim surfacing and retrieval into chat (see MEMORY_AND_KNOWLEDGE.md). Still open: automatic extraction from conversations (owner-approved), consolidation/summarisation, entity relationships, retention coverage and a UI.
3. Add a durable activity journal, event-triggered scheduler and curiosity backlog.
4. Implement safe, resource-aware, read-only exploration cycles with checkpoints and recovery.
5. Add the proactive conversation decision/delivery engine with quiet hours and frequency controls.
6. Add reflection and evidence-based learning from owner feedback and task outcomes.
7. Connect the life cycle to governed self-programming so discovered gaps can become tested capability candidates.
8. Evaluate the combined system with fixed tests and the actual 30-day SPS experiment.

Each step requires implementation, tests, documentation and evidence on the exact commit. This target architecture must not be reported as implemented until its acceptance criteria pass.
