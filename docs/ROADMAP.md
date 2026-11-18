# Mia implementation roadmap

The roadmap is sequenced by dependencies and quality gates, not by artificial release dates. Each phase must produce working, tested software before dependent phases are marked complete.

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

## Phase 2 — Universal AI gateway

**Goal:** One policy-controlled interface for all model providers.

Work:
- Define provider and model contracts.
- Add local Ollama adapter first.
- Evaluate FreeLLMAPI as the self-hosted gateway candidate; compare LiteLLM as an alternative.
- Add provider/model discovery, health checks, capability metadata, context limits, pricing/quota metadata, latency and reliability statistics.
- Implement task-aware routing and fallback.
- Add secure credential storage, provider-specific policy checks, request budgets and usage tracking.

Routing principles:
1. Local models first when quality and latency are acceptable.
2. Eligible free tier next, subject to the upstream provider's terms and current quota.
3. Paid models only when explicitly enabled and within both per-request and monthly budgets.
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
- Conversations, facts, tasks, preferences and episodic experiences stored separately.
- Document ingestion for PDF, Markdown, code and office formats through replaceable parsers.
- Full-text retrieval first; optional embeddings only when resource and license checks pass.
- Source citations, deletion, retention, export and user-controlled memory policies.

Exit criteria:
- Every imported item records source, timestamp, parser/version and provenance.
- Retrieved claims can be traced to source passages.
- Memory does not silently elevate untrusted document instructions into system policy.

## Phase 6 — Autonomous agent runtime

**Goal:** Execute multi-step tasks and self-directed maintenance within a controlled budget.

Work:
- Task state machine: proposed, approved, running, waiting, completed, failed, cancelled.
- Planner, tool dispatcher, task queue, scheduler and retry policy.
- Task Desk UI for proposal generation/review, plus explicit manual execution of one owner-approved registered read-only tool after CPU/RAM checks; model-generated task descriptions are never interpreted as commands.
- Workload sources: self-assessment, internal tests, benchmarks, research discoveries, authorized environment signals and experience analysis.
- Resource-aware scheduling and sleep/idle policies.
- Resume, cancellation, timeout, rate limit and recovery.
- A user-visible activity ledger.

Exit criteria:
- Tasks left running by a process restart are atomically marked failed with an append-only recovery event; no task is silently replayed, and retry requires owner re-approval.
- Automatic continuation across restart requires a future durable lease/heartbeat design and remains a separate exit gate.
- The system can explain why a task was selected and what evidence changed its state.
- A manually invoked read-only execution has an append-only lifecycle trace; background scheduler execution, restart recovery and cancellable isolated workers remain separate exit gates.
- Background workload pauses under resource pressure.
- The autonomy switch and emergency stop are reliable.

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
