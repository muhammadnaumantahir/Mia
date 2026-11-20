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

## Implementation status: first autonomous cycle (2026-11-20)

Implemented in `mia.life` (`store.py`, `runner.py`) and `/api/life/*`; local tests only, no CI result, nothing exercised on the owner's Windows machine.

- **Durable curiosity backlog** (`mia-life.sqlite3`): items with a reason it matters, origin (`owner`, `goal_followup`, ...), expected value, novelty, state (`queued`, `in_progress`, `deferred`, `blocked`, `done`, `rejected`, `dropped`), attempt count and a deduplicating `source_key`. Duplicate open titles are not added twice. Owners add, requeue, reject, drop and delete items; only the runner marks items `done`.
- **Observation that is not invented:** each cycle derives a follow-up for every *active* goal record not updated for `stale_goal_days` (default 14), keyed on the goal and its last update so it is idempotent. Nothing else is derived yet.
- **Selection:** the highest score of `0.55*value + 0.2*novelty + 0.15*age - 0.15*attempts` runs next; the formula and numbers are written into the journal entry.
- **Exploration (permission level 0, read-only, no model, no network):** `MemoryReviewExplorer` checks what Mia's own memory (knowledge, structured records, past messages) already holds about the item. It writes nothing. If it finds related entries the item is `done` with those sources cited; if not, the cycle is `deferred` and the item `blocked` with reason `no_enabled_research_source` instead of fabricating a finding. A goal follow-up never "finds" the goal it came from. **No web research, model reasoning or reflection exists yet**, so the journal says "No external research was performed".
- **Activity journal** (`activity_journal`): per started cycle - trigger (`schedule`/`event`/`owner`) and detail, goal, item, selection rationale, permission level, policy/budget snapshot, sources, actions, resource record (`model_id`/`provider_id` null, `model_calls` 0, `cost_usd` 0.0 for this explorer), duration, outcome (`completed`, `no_op`, `deferred`, `failed`, `cancelled`), error type only, next step and `notification_decision` (always "none: proactive delivery is not implemented"). Cycles that are gated off are **not** journaled (the API returns the skip reason), so the journal only contains work that started. Identical consecutive no-op cycles coalesce into one entry with `repeat_count`.
- **Owner controls** (persisted, validated, re-read live): `paused`, `emergency_stop`, `max_cycles_per_day` (24), `min_interval_seconds` (300), `time_budget_seconds` (20), `stale_goal_days`, `max_attempts` (3), `max_cpu_percent` / `max_memory_percent` (85). Corrupted stored settings fall back to paused. Emergency stop also pauses, blocks owner-requested cycles, and clearing it leaves Mia paused until the owner resumes. A pause or stop arriving mid-cycle cancels it: findings are discarded and the item is requeued. The time budget is enforced with a deadline; an explorer exceeding it is recorded as `failed` (the worker thread cannot be force-killed; explorers must honour `context.deadline` / `should_stop`).
- **Gating:** background cycles need telemetry (`psutil` snapshot) and skip when it is missing, invalid or above the CPU/RAM limits; they also respect the daily cap and minimum interval. An explicit owner `POST /api/life/cycle` bypasses pause, cap, interval and resource gates but never the emergency stop.
- **Scheduling:** `LifeScheduler` wakes on a timer or an event (an owner-added curiosity item) and runs one cycle. It starts only when `MIA_LIFE_ENABLED=true`, the owner has not paused, and no emergency stop is set (`MIA_LIFE_INTERVAL_SECONDS`, 5-86400, default 300). It is off by default.
- **Recovery:** at startup, journal cycles left `running` by an earlier process are marked `failed` ("nothing was resumed"), their item is requeued with the attempt counted, and a `cycles_recovered` event is recorded. Leases are per instance; Mia is assumed to run as a single server process.
- **Audit:** owner actions and finished cycles append Observatory `life` events carrying ids only, never item text.

Endpoints: `GET /api/life/status`, `PUT /api/life/settings`, `POST /api/life/pause|resume|emergency-stop|emergency-stop/clear|cycle`, `GET/POST /api/life/curiosity`, `POST /api/life/curiosity/{id}/state`, `DELETE /api/life/curiosity/{id}`, `GET /api/life/journal[/{id}]`.

### Scoped web research and quiet hours (2026-11-20)

- **Source allowlist** (`/api/life/sources`, `life_sources`): empty by default, so no external research happens until the owner adds a domain. Entries are exact lowercase hostnames only - no wildcards, no implicit parent or sub-domains, no IP literals, no local/reserved names.
- **Items with a URL** (`url` on `POST /api/life/curiosity`; https only, default port, no credentials, fragment dropped) go to `WebExplorer` (permission level 1, journaled as such); items without a URL still use the level-0 memory review. An unlisted host is blocked with `source_not_permitted` and **no request is made**.
- **Fetcher** (`HttpxFetcher`): GET only, no cookies/auth/proxy environment, fresh client per hop, redirects handled manually and every hop re-checked against the allowlist, https-only and the existing public-network guard (`validate_public_url`, which blocks loopback/private/reserved targets), content types limited to HTML/plain text, 500 KB streamed cap, timeout bounded by the cycle's remaining budget, owner stop honoured between chunks. Errors are stable reason codes (never response content). Permanent failures (403/404/410, wrong type, off-list redirect, no readable text) block the item; timeouts, network errors and 5xx defer it for retry within the attempt cap.
- **Result:** markup, scripts and styles are stripped and the text capped at 20,000 characters, then stored as a `research` knowledge item with `trust_level="untrusted"`, source URL, content hash and `reviewed: false`. An unchanged re-fetch stores nothing new. The page text is never interpreted: it cannot change settings, queue work or create records, and when retrieved into a prompt it is labelled "untrusted external content" and defanged like any stored text. The journal records the host, bytes, hash, stored note id and the actions `http.get:<host>` and `memory.add_item:research(untrusted)`; resource use is 0 model calls.
- **Quiet hours** (`quiet_hours_enabled`, `quiet_start_minute`, `quiet_end_minute`, `utc_offset_minutes`; default off, 22:00-07:00 when enabled): background and event cycles are skipped (not journaled) inside the window, including windows that wrap past midnight; an explicit owner request still runs, an emergency stop still blocks everything. The window must have different start and end times.

Known limits of the web explorer: it reads one page per item (no crawling, search or link following); it does not summarise or reason about the page (no model call), so a note is raw extracted text; DNS is resolved by the guard and again by the HTTP client, so a hostile DNS rebinding race is not excluded; robots.txt and rate limits per site are not implemented; no PDF or JavaScript-rendered pages; the owner is trusted to allowlist reputable domains.

Not implemented: reflection, interest model, per-tool permission scopes, daily spend limits (no paid model is used), proactive conversation delivery and outcome tracking, UI for any of this, automatic creation of curiosity items from conversations or capability gaps, and idle-aware scheduling beyond the CPU/RAM gate.

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

## Implemented: reflection and inbox outreach (2026-11-20)

`mia.life.reflect.Reflector` writes a deterministic digest after every 5 new real cycles (counts, hosts, failures, message decisions, cited cycle ids). `mia.life.outreach` builds candidates from journal facts only: completed owner research with a stored untrusted note, blocked owner items, and stale-goal check-ins. Housekeeping items never contact the owner and page text is never echoed. Delivery is opt-in and inbox-only, evaluated in this order: emergency stop, outreach disabled, pause, quiet hours, daily cap, minimum gap (the last three bypassed for owner-requested cycles), unanswered backpressure, 30-day dismissed-topic memory, per-topic cooldown. Each cycle's `notification_decision` records `sent`, `suppressed: <reason>` or `none`. API: `GET /api/life/outreach`, `POST /api/life/outreach/{id}/state` (read, acted, dismissed), `GET /api/life/reflections`.

## Implemented: curiosity from conversation (2026-11-20)

`mia.life.capture` examines only the owner's chat message for explicit invitations to explore later and queues at most one `conversation`-origin backlog item per message (quote as reason, conversation id as source). It never queues URLs or secret-like topics, respects `conversation_capture_enabled` and the emergency stop, and cannot break a chat turn. Queued items are explored by the normal gated runner; any finding reaches the owner only through the opt-in inbox.
