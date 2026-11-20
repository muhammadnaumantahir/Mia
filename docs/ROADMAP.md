# Mia implementation roadmap

The roadmap is sequenced by dependencies and quality gates, not by artificial release dates. Each phase must produce working, tested software before dependent phases are marked complete.

## Three co-equal core product priorities

The owner's product direction is that Mia must become a persistent, proactive digital companion—not only a passive voice/chat assistant. Three priorities are co-equal and permanent; sequence their implementation by dependency, but do not defer one until the others are complete:

1. **Persistent identity and memory:** one canonical Mia identity and structured, source-aware memory that survives sessions, restarts and model/provider changes. This includes conversations, preferences, people/entities, projects, decisions, goals, episodic experience and validated lessons.
2. **Autonomous digital life and proactive initiative:** Mia can maintain curiosity and follow-up queues, independently explore permitted topics/projects, reflect on evidence, create internal work products and initiate useful conversations without waiting for a command. This runs through bounded scheduled/event-driven cycles, not an always-running model.
3. **Self-programming and governed evolution:** discover capability gaps, reuse/adapt/create capabilities, test in isolation, record lineage/evidence, promote through governance and roll back regressions.

These are cross-cutting product priorities implemented through the existing ten SPS layers and supporting runtime components. They do not change the canonical SPS layer count. See [Proactive Digital Companion Runtime](architecture/DIGITAL_PERSON_RUNTIME.md) for the target behaviour and acceptance criteria.

## Phase 0 — Research and open-source reuse audit

**Goal:** Decide what Mia should adopt, integrate, adapt, study or reject.

Work:
- Inspect current source, docs, license, dependency graph, release activity and tests of candidate repositories.
- Compare Windows support, Python support, hardware footprint, offline operation and deployment complexity.
- Map candidate capabilities to the ten SPS layers without changing the layer model.
- Record code, package, container, model-weight, voice and data licenses separately.
- Identify paid/hosted service dependencies and provider terms-of-service constraints.

Exit criteria:
- Every high-priority capability has a selected implementation strategy.
- The integration matrix has source URLs, current license, version/ref, risks and test plan.
- No unreviewed GPL/AGPL source is copied into Mia.
- No free-tier quota is used in a way that circumvents provider limits or terms.

## Phase 1 — Foundation and observability

**Goal:** Establish a stable local application and evidence trail.

Work:
- Python package, configuration, logging and local service entry point.
- Immutable definitions for the ten SPS layers.
- SQLite store for experiment metadata, events, metric samples, capability versions and snapshots.
- Local-only observatory API and dashboard.
- Tests, CI, schema versioning and export/import of experiment records.

Exit criteria:
- Clean install works on Windows with Python 3.13.
- Tests pass on a clean environment.
- Dashboard displays actual records and shows an honest empty state when no data exists.
- Every event includes timestamp, experiment/day context, event type, outcome and structured payload.
- The experiment baseline can be exported and cryptographically hashed.

> **2026-11-20 implementation update:** Ollama plus generic direct OpenAI-compatible provider registration, persistent model metadata/owner settings, persisted routing preferences, provider health/connection checks, a local `/gateway` console, append-only usage records and fail-closed request/daily/monthly budget checks are implemented in the current development branch. No FreeLLMAPI or OmniRoute sidecar is required. Atomic in-flight reservations are now implemented in the branch using a serialized SQLite write transaction; regression tests cover concurrent overbooking, usage settlement, failed-call release, unknown-cost rejection and cancelled-stream reservation retention, but have not yet been executed here. Remaining gateway work includes quota-header ingestion, persistent health/decision history, semantic caching, benchmark-based quality routing and full multimodal/embedding paths. Paid routing remains disabled by default and must not be enabled until the local regression suite has been run. See [Gateway audit](gateway/GATEWAY_AUDIT.md).

## Phase 2 — Universal AI gateway

**Goal:** One independently owned, policy-controlled Mia gateway for all model providers. FreeLLMAPI and OmniRoute are not runtime dependencies or required sidecars. Follow [Independent AI Gateway direction](architecture/AI_GATEWAY_DIRECTION.md).

Work:
- Define provider and model contracts.
- Add local Ollama adapter first.
- Build Mia-owned provider/model registry, direct provider adapters and a generic OpenAI-compatible adapter. Study gateway projects only for isolated patterns; do not depend on FreeLLMAPI or OmniRoute.
- Add model discovery, capability metadata, context limits, sourced pricing/quota metadata, health checks, latency and reliability statistics.
- Implement task-aware routing, escalation, load balancing, fallback, circuit breakers and provider-aware retry policy.
- Add secure credential references, data-egress policy, request/daily/monthly budgets, append-only usage tracking, observability and benchmark-based route evaluation.

Routing principles:
1. Local models first when quality and latency are acceptable.
2. Eligible free tier next, subject to the upstream provider's terms and current quota.
3. Paid models only when explicitly enabled and within per-request, daily and monthly budgets.
4. Never retry a request in a way that bypasses upstream restrictions.
5. Never treat a catalog's listed free status as a guarantee of current availability.

Exit criteria:
- Local inference works without cloud keys.
- Multiple provider adapters pass contract tests.
- Paid access is denied by default.
- Fallback works for transient errors without leaking credentials or duplicating unsafe tool actions.
- Cost and routing decisions are recorded in the observatory.

## Phase 3 — Voice interface

**Goal:** Natural, low-cost voice interaction with a selectable feminine voice.

Work:
- Wake-word detection and push-to-talk fallback.
- Streaming microphone capture and voice activity detection.
- Speech-to-text adapter; benchmark Faster-Whisper and whisper.cpp on the target CPU.
- TTS adapter; evaluate voice quality, English/Urdu pronunciation, latency, offline operation and model-weight license.
- Interruptions, barge-in, mute, audio-device selection and transcripts.
- Keep voice optional; all features remain usable through text.

Exit criteria:
- Voice works offline with a documented model and license.
- Wake-word false activations and missed activations are measured.
- The selected voice has an explicitly verified redistributable license if distributed.
- The application can stop listening immediately and visibly indicate microphone state.

## Phase 4 — Computer intelligence

**Goal:** Operate the user's Windows computer through narrow, inspectable tools.

Work:
- Browser automation through Playwright.
- Desktop UI automation through a Windows adapter.
- Filesystem search, read, write, move and versioned edit operations.
- Document creation and analysis.
- Application launch and process inspection.
- Action plans, previews, permission scopes, audit logs and cancellation.

Exit criteria:
- Every tool declares input/output schema, permissions, side effects and timeout.
- Destructive or externally visible actions require approval by default.
- Browser and generated-code execution are isolated from credentials and unrelated files.
- All actions are traceable and recoverable where possible.

## Phase 5 — Memory and knowledge

**Goal:** Give Mia persistent, source-aware memory and a searchable knowledge library.

Work:
- SQLite as the zero-service baseline.
- Conversations, facts, tasks, preferences, goals, decisions and episodic experiences stored separately.
- Stable identity and contextual continuity across sessions, restarts and model/provider changes.
- Memory consolidation that summarizes older conversations while preserving key details, corrections, decision history and source links.
- Contextual retrieval that combines recent turns with relevant semantic, episodic, entity, project and decision memory; retrieval must include provenance and trust labels.
- Document ingestion for PDF, Markdown, code and office formats through replaceable parsers.
- Full-text retrieval first; optional embeddings only when resource and license checks pass.
- Source citations, conflict handling, deletion, retention, export and user-controlled memory policies.

Exit criteria:
- Every imported item records source, timestamp, parser/version and provenance.
- Retrieved claims can be traced to source passages.
- Memory does not silently elevate untrusted document instructions into system policy.

## Phase 6 — Autonomous digital life and agent runtime

**Goal:** Give Mia a persistent life between conversations: independently pursue bounded, useful work, reflect on evidence, and initiate relevant conversations while safely executing approved tasks.

This phase is not just a task queue or a voice wake-word feature. Its target architecture is specified in [Proactive Digital Companion Runtime](architecture/DIGITAL_PERSON_RUNTIME.md).

Work:
- Task state machine: proposed, approved, running, waiting, completed, failed, cancelled.
- Planner, tool dispatcher, task queue, scheduler and retry policy.
- Task Desk UI for proposal generation/review, plus explicit manual execution of one registered read-only tool or a bounded 2-5 step read-only sequence after owner approval and CPU/RAM checks; model-generated task descriptions are never interpreted as commands.
- Workload sources: self-assessment, internal tests, benchmarks, research discoveries, authorized environment signals, curiosity backlog, unresolved conversations and experience analysis.
- Autonomous life cycle: observe, orient, explore, reason, create, verify, remember, reflect, then communicate or sleep.
- Durable curiosity/interest/follow-up backlog with rationale, evidence quality, novelty, expected value, cost and state; no random work to simulate activity.
- Proactive social/conversation engine that initiates useful, contextual discussions, remembers their outcomes and avoids stale/duplicate interruptions.
- Configurable quiet hours, notification caps, per-topic cooldowns, deduplication, pause/resume, emergency stop and per-tool/source scopes.
- Resource-aware scheduling and sleep/idle policies; bounded wake-up cycles rather than continuous model inference.
- Resume, cancellation, timeout, rate limit, idempotency, durable lease/heartbeat, checkpoint and recovery.
- A user-visible activity ledger that records triggers, goals, sources, model/provider use, actions, decisions, resource costs and outcomes.
- Internal/read-only research and analysis are the default autonomous scope. Sensitive writes, generated-code execution, external communication and irreversible actions remain separately permission- and approval-gated.

Exit criteria:
- Tasks left running by a process restart are atomically marked failed with an append-only recovery event; no task is silently replayed, and retry requires owner re-approval.
- Durable leases/heartbeats and expired-lease recovery prevent one live worker from being mistaken for abandoned work; process-restart continuation still requires safe checkpoints and action-specific idempotency.
- The system can explain why each task or curiosity item was selected and what evidence changed its state.
- Read-only execution validates every step, stops on first failure, shares one overall timeout and records an append-only lifecycle trace.
- Autonomous exploration can complete a permitted investigation without a user prompt, persist its sources/findings and decide whether a proactive message is justified.
- Proactive messaging enforces quiet hours, rate/frequency limits, deduplication, pause/resume and emergency stop in runtime tests.
- Background workload pauses under resource pressure; offline periods are recorded honestly and never filled with fabricated activity.
- The autonomy switch, per-scope permissions and emergency stop are reliable.
- Sensitive or externally visible actions require the configured approval path; cancelling or pausing prevents new steps from starting.

## Phase 7 — Self-programming and capability evolution

**Goal:** Let Mia discover justified gaps and evolve capabilities without corrupting its trusted runtime.

Lifecycle:
1. Detect a capability gap and record evidence.
2. Decide reuse, adapt, improve, compose, create, defer or reject.
3. Produce a capability contract and implementation plan.
4. Generate code in an isolated workspace.
5. Run static checks, unit tests, security checks and regression benchmarks.
6. Review permissions and provenance.
7. Persist an immutable version, diff, parent lineage and validation evidence.
8. Register and activate through a controlled plugin boundary.
9. Monitor; rollback or retire on regression.
10. Record the outcome as experience.

Exit criteria:
- Generated code never runs in the privileged host process.
- Capability versions and lineage are reproducible.
- Failed validation prevents activation.
- Activation can be rolled back without restarting the entire assistant where the plugin boundary allows it.
- No-op is a valid evolution decision; no change is forced merely to create activity.

## Phase 8 — Thirty-day autonomous experiment

**Goal:** Evaluate whether Mia improves from Day 0 to Day 30 under reproducible conditions.

Prerequisites:
- Phases 1–7 have the required stable capabilities.
- Immutable source/configuration/model baseline is captured.
- Benchmark suite, environment and task distribution are fixed.
- Consent, computer-access scope, spend ceiling and emergency stop are configured.
- Experiment database and snapshots are backed up.

Experiment:
- Day 0: baseline snapshot and benchmark run.
- Days 1–5: capability-gap discovery and low-risk improvements.
- Days 6–10: capability development and adapter integration.
- Days 11–15: adaptation and experience-based policy updates.
- Days 16–20: capability composition and reusable workflows.
- Days 21–25: reliability, efficiency and regression optimization.
- Days 26–30: final fixed benchmarks, independent task evaluation and report.

No stage forces new code. Preserve no-op, rejected, failed and rolled-back decisions as first-class results.

Exit criteria:
- All 30 daily records or explicit offline/paused days are accounted for.
- Day 0 and final results use the same benchmark definitions and environment notes.
- Every claimed improvement is reproducible and linked to raw evidence.
- Final report distinguishes capability growth from measured quality improvement.


## Memory semantic retrieval addendum (2026-11-20)

An opt-in local-only Ollama embedding reranker has been added on the development branch. It expands a bounded candidate pool of recent structured records and knowledge items, ranks them with local embeddings, and preserves existing provenance, trust, relationship expansion and context budgets. Disabled by default; no cloud API or model download. Remaining: persistent embedding cache/index, semantic retrieval across past conversation messages, multilingual quality evaluation, model benchmarking and owner verification. See docs/memory/SEMANTIC_RETRIEVAL.md.
