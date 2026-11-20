# Mia implementation roadmap

**Product direction:** Mia is being built first as an intelligent, natural, voice-enabled conversational companion. The longer-term destination is a personal AI operating system that understands the owner, helps with daily life, controls the computer safely and improves its own capabilities under governance.

**Execution rule:** Work strictly from the highest-priority incomplete phase downward. An AI coding agent must not begin lower-priority feature work while a higher-priority phase still has unfinished development acceptance criteria, except to resolve a direct prerequisite, an integration defect or a security issue that blocks the current phase. CI and owner-only physical testing are not development gates; they are reported separately.

The roadmap is sequenced by product priority, dependency and evidence—not by artificial release dates. The canonical ten-layer SPS architecture is preserved throughout.

## Priority 0 — Integration and development baseline

**Goal:** Establish what is actually implemented on the active branch and remove integration friction before extending the product.

Work:
- Read AGENTS.md, this roadmap and docs/STATUS.md at the start of every coding session.
- Inspect main, the current working branch, open pull requests and their actual diffs. Review the previously referenced PR #3 if it exists; do not assume its state or contents.
- Resolve application-level defects and merge/integration conflicts in existing Brain, chat, voice, memory, gateway, life and permission work.
- Reconcile stale documentation with the source and tests; never mark a capability implemented based only on a roadmap or README.
- Keep Mia's gateway independent: remove FreeLLMAPI/OmniRoute runtime dependencies, sidecars and required control planes. Preserve them only as research references where appropriate.
- Establish targeted local verification and record commands/results. Do not spend development time repeatedly diagnosing GitHub runner-allocation or CI infrastructure failures.
- Build/refresh the initial Mia Code Graph and make incremental local updates part of the normal coding workflow.

Exit criteria:
- The active development baseline and unresolved application defects are documented from source-level inspection.
- Existing implemented conversation, Brain, voice, memory and gateway pieces have a coherent integration path.
- No required FreeLLMAPI or OmniRoute runtime service remains.
- Code Graph can index the real repository, show source-backed relationships and record the indexed commit.
- Local checks are run where practical; CI infrastructure availability is recorded separately.

## Priority 1 — Conversation-first Mia (first product milestone)

**Goal:** The owner can talk naturally with Mia, discuss open-ended and personal topics, ask Mia about itself, continue previous discussions and receive thoughtful, context-aware answers in text and voice. This is the first milestone to complete. Do not prioritize computer-control expansion until this phase is complete.

### Work

**Identity and self-understanding**
- Maintain one canonical Mia identity, purpose, personality, operating instructions and truthful capability inventory independent of the selected model. The persistent owner-editable profile, version history, rollback, audit and shared prompt path are implemented; the owner-facing editor UI is in a separate draft integration change.
- Let Mia answer questions about who it is, how it works, its ten-layer SPS architecture, enabled tools, current state, limits and unavailable features using live, maintained application evidence.
- Separate known facts, inferred facts, unknowns and unavailable runtime information. Never invent memories, tools, actions, emotions or system status.
- Make personality and response style configurable without allowing personality settings to override security or factuality.

**Natural conversation and reasoning**
- Unify text, streaming, voice and future modalities through the same model-independent AI Brain and conversation pipeline.
- Improve intent understanding, context management, reasoning, clarification, planning, answer verification, uncertainty handling, correction and recovery.
- Preserve multi-turn context and conversation continuity across sessions, restarts, projects, providers and model changes.
- Support English, Urdu and Roman Urdu in understanding and responses; use language-aware tests rather than relying on UI labels.
- Add a conversation/task workspace that shows active intent, plan, progress, completion, pending work and truthful failure reasons without overwhelming normal conversation.
- Use task-aware model selection, escalation and fallback through the Mia-owned gateway.
- Add configurable parallel execution of multiple judge/evaluator models for suitable tasks. Bound concurrency, latency, data sharing and spend; simple conversations should not trigger an unnecessary expensive ensemble.

**Persistent conversation memory**
- Extend existing persistence and FTS5 retrieval with semantic and relationship-aware retrieval when it is resource- and license-appropriate.
- Add owner-controlled automatic memory extraction, confidence/provenance, correction, conflict handling, consolidation and retention. Include English, Roman Urdu and Urdu-script capture/command tests; the current deterministic rules are a baseline, not a substitute for future model-assisted extraction.
- Preserve important context, decisions, project state, preferences, goals and episodic history across model/provider switches. Recognize common English, Roman Urdu and Urdu-script references such as asking what was decided, what the owner's goal is, or where a project was left.
- Keep retrieved memories source-labelled and distinguish owner-confirmed facts from model inferences and untrusted document content.
- Provide transparent memory inspection, correction, export and deletion controls.

**Natural voice and hands-free conversation**
- Complete streaming speech input/output, natural turn-taking, interruption/barge-in, mute, stop, audio-device selection, transcript handling and voice settings.
- Support Urdu/English speech recognition and spoken output with measurable quality and clear fallback behaviour.
- Include an always-on wake-word mode with visible listening state, immediate mute/stop, configurable activation phrase where supported, false-activation controls, resource limits and clear privacy disclosure.
- Keep push-to-talk/manual start available as a fallback. No concealed recording or silent microphone operation.
- Preserve the same memory, identity, reasoning and tool-result verification across voice and text.

**AI gateway required by conversation**
- Follow architecture/AI_GATEWAY_DIRECTION.md (relative to docs/): Mia owns its provider/model registry, direct adapters, generic OpenAI-compatible adapter, local Ollama adapter, task-aware routing, escalation, fallback, circuit breakers, health, budgets, credential security, usage ledger, evaluation and observability.
- FreeLLMAPI and OmniRoute must not be runtime dependencies, sidecars or required control planes.
- Cloud-hosted open-weight models may be used through providers without downloading model weights onto the owner's PC or Mia's server.
- Local-only operation must remain possible without cloud credentials. Unknown pricing is not zero; paid inference remains off until explicitly enabled with tested limits.

### Exit criteria

- A repeatable conversation evaluation suite covers Mia identity/self-description, real capability inventory, memory recall, follow-up continuity, cross-session/provider continuity, open-ended reasoning, ambiguity, uncertainty, corrections, English/Urdu/Roman Urdu, voice turn-taking, interruption and recovery.
- Mia gives accurate, evidence-grounded answers about itself and does not claim features that are not implemented or enabled.
- Relevant memory can be retrieved across conversations and is traceable to source/provenance; owner corrections and retention controls work.
- Text and voice share the same Brain, identity, context and verification path.
- Natural voice, wake-word controls and failure states have automated coverage where possible. Physical microphone/speaker/target-device checks are explicitly labelled OWNER_VERIFICATION_REQUIRED.
- Parallel judges and model escalation obey privacy, resource and budget policy and are testable with deterministic provider fakes.
- The owner can sustain a meaningful multi-turn discussion, change topics, refer back to previous context and ask Mia about itself without restarting or re-explaining the conversation.

## Priority 2 — Immersive 3D holographic interaction

**Goal:** Make the completed conversational companion feel like a polished, responsive Jarvis-style assistant.

Work:
- Build the full 3D holographic interface as a real interaction surface, not a static visual mockup.
- Visualize listening, thinking, speaking, interruption, tool activity, memory references and task progress with clear state semantics.
- Add responsive layout, accessible controls, reduced-motion support, performance fallbacks and a practical non-3D mode.
- Integrate voice activation and conversation controls without hiding microphone/privacy state.
- Support multimodal input/output where useful and supported by the active providers.

Exit criteria:
- The UI reflects actual runtime state and never fabricates activity.
- Core conversation remains usable when 3D rendering is unavailable or disabled.
- Performance and accessibility have repeatable checks; resource-heavy effects can be reduced or switched off.

## Priority 3 — Personal digital life and everyday assistance

**Goal:** Extend Mia from a conversational companion into a useful, proactive personal assistant without prematurely granting broad computer control.

Work:
- **Personal Daily Briefing:** owner-approved summaries of priorities, reminders, calendar/task context and relevant updates.
- **Unified Task Manager:** capture, organize, prioritize, track and follow up on personal and project tasks.
- **Project Workspace:** project-specific context, goals, decisions, documents, milestones and progress.
- **File Intelligence:** securely index and search owner-selected files, explain and summarize documents, and cite source locations.
- **Browser Research Assistant:** conduct scoped research, compare sources, cite evidence and save owner-reviewed findings.
- **Workflow Automation:** compose bounded multi-step workflows with previews, permissions, checkpoints and logs.
- **Notification Center:** quiet, configurable, deduplicated and owner-controlled reminders and proactive messages.
- **Personal Knowledge Graph:** connect people, projects, topics, decisions, tasks and source evidence.
- **Backup and Restore:** encrypted/configurable backup, integrity checking, restore preview and recovery tests.
- **Usage and Resource Dashboard:** show model/provider usage, spend, latency, CPU/RAM, storage and background workload from real measurements.
- **Multi-device Access:** synchronize approved state and conversation continuity across devices with conflict handling.
- **Secure Remote Mia Access:** authenticated, encrypted remote access with least-privilege scopes; no public unauthenticated service.
- Add goal tracking, unfinished-task detection, useful follow-up suggestions, weekly progress and evidence-backed reflection.
- Keep proactive work bounded by quiet hours, relevance, novelty, frequency caps, opt-out, privacy, resource budgets and emergency stop.

Exit criteria:
- Each feature has an explicit data source, permission scope, retention policy and user-visible control.
- Proactive messages explain why they were generated and avoid duplicate or stale interruptions.
- Backups can be restored and verified; remote/multi-device access is authenticated and scoped.
- Background work pauses safely under resource pressure and never invents activity during offline periods.

## Priority 4 — Secure computer intelligence and control

**Goal:** After the conversation-first milestone and personal-assistant foundations, allow Mia to operate the owner's computer through controlled, observable and recoverable actions.

Work:
- Browser automation, desktop UI interaction, application launch and process inspection.
- Scoped filesystem search/read/write/move, versioned edits and document operations.
- A central permission broker enforced at every execution boundary, including Brain, Voice, browser, desktop, filesystem, plugins and background tasks.
- A Windows-capable runtime and verified sandbox with filesystem, process, network and resource restrictions.
- Action plans, dry-run/previews, explicit approval for destructive or externally visible actions, audit history, cancellation and recovery.
- Task recording/replay and undo for reversible actions.
- Voice control of computer tasks using the same Brain and permissions as text.
- Keep owner-approved scopes narrow and revocable. Never turn conversation into blanket computer permission.

Exit criteria:
- Every tool declares schema, permissions, side effects, time/resource bounds and recovery behaviour.
- Permission checks occur at execution time, not only in the UI or planner.
- Generated code and untrusted browser/document content cannot access secrets or escape the sandbox.
- Sensitive/destructive actions require explicit owner approval by default.
- Actions are auditable, cancellable and recoverable where technically possible; target Windows verification is separately identified as owner verification.

## Priority 5 — Self-programming, custom models and governed evolution

**Goal:** Let Mia discover and improve capabilities, and research the long-term possibility of building a custom foundation model.

Work:
- Detect capability gaps from real conversations, failures, evaluation results and owner goals.
- Reuse, adapt, compose or create tools/capabilities with documented provenance and license review.
- Generate candidate implementations in isolated workspaces; run static checks, unit, integration, security and regression tests.
- Maintain immutable versions, diffs, lineage, capability contracts, permission declarations and evidence.
- Provide a secure plugin host and permission-scoped activation, owner approval for privileged capabilities, monitoring, rollback and retirement.
- Include the requested **automatic unrestricted self-modification** concept as an isolated research mode: Mia may autonomously explore and modify a disposable clone without per-edit owner approval, but that sandbox must have no production credentials, host-level privileges, live owner data, external side effects or ability to alter the trusted governance boundary. Any candidate entering the real Mia runtime must pass validation and governed promotion. Unrestricted live modification of the privileged production runtime, security policy, secrets or approval boundaries is not an acceptable deployment mode.
- Research **building or fine-tuning a custom foundation model**: define use cases, data rights, compute, evaluation, cost and deployment feasibility. Start with open-weight/cloud-hosted models and smaller task-specific adaptation where appropriate; do not make expensive pretraining a prerequisite for Mia's usefulness.
- Allow Mia to explain proposed changes, evidence, expected effects, risks and rollback plan before activation.

Exit criteria:
- Generated code never executes in the privileged host process.
- Failed validation prevents activation; every change has immutable lineage and reproducible evidence.
- Permission-scoped activation and rollback work without weakening the security boundary.
- Model research has a benchmark, data/license assessment, compute/cost estimate and a go/no-go decision rather than an unsupported claim that a foundation model has been trained.
- No-op, deferred, rejected and rolled-back changes are recorded as legitimate outcomes.

## Priority 6 — Evolution Observatory and 30-day experiment

**Goal:** Measure real improvements rather than code volume or simulated activity.

Work:
- Maintain an immutable Day 0 baseline and daily snapshots from actual owner-machine/runtime evidence.
- Attribute quality and capability changes to commits, model/provider versions, configuration and capability activations.
- Benchmark conversational quality, memory recall, reasoning, voice latency/quality, tool reliability, safety, resource usage and cost.
- Provide before/after comparisons, trends, failure analysis and evidence-linked reports.
- Preserve offline, paused, failed, no-op and rolled-back days honestly.
- Implement the infrastructure for a 30-day experiment; starting, running and reviewing the real experiment is owner activity.

Exit criteria:
- Baseline and final benchmarks use consistent definitions and environment notes.
- Every claimed improvement links to raw evidence and exact software/model/configuration versions.
- No growth metric is fabricated or inferred from code-line counts alone.
- The 30-day report distinguishes implementation progress from measured quality, reliability and autonomy.

## Cross-cutting requirements

### Mia Code Graph

The Code Graph is mandatory and begins in Priority 0; it remains incrementally updated in every phase. It must represent actual source evidence for packages, files, classes, functions, imports, calls, APIs, database models, tests, SPS layers, gateway, Brain, Memory, Voice, Computer, permissions, scheduler, workflows and self-programming.

Use typed relationships including IMPORTS, CALLS, DEPENDS_ON, IMPLEMENTS, EXTENDS, USES, EXPOSES_API, READS_FROM, WRITES_TO, TESTED_BY, PROTECTED_BY, BELONGS_TO_LAYER, ROUTES_TO, ACTIVATES and TRIGGERS. Mark confirmed/inferred/documented edges separately; never invent links. Provide search, filters, node details, dependency/execution paths, impact analysis, circular/orphan detection, test/permission mapping, architecture-drift indicators and graph history. Full and incremental indexing must work in the local Git/development workflow without relying on CI. Graph generation is deterministic, testable, secret-safe and read-only with respect to source files. Maintain schema, data and changelog under docs/code-graph/ or the established equivalent.

### Ten-layer SPS architecture

All phases use the existing ten canonical SPS layers in architecture/SPS_LAYERS.md. The gateway, holographic interface, Code Graph, task runtime and integrations are supporting services/features, not new SPS layers.

### Development and verification policy

- Development is mandatory; CI is optional. Do not let GitHub Actions runner allocation, workflow failures or CI configuration block feature development.
- Run local tests, formatters, type/static checks and focused integration checks when practical. Record what ran and what did not. Never claim a pass without evidence and never weaken tests to manufacture a pass.
- Do not repeatedly spend development time on CI infrastructure. Record CI status separately.
- Physical microphone/speaker checks, real Windows desktop tests, live cloud credentials/providers, Oracle deployment, owner acceptance and the actual 30-day experiment are OWNER_VERIFICATION_REQUIRED; implement their supporting software and automated tests without making those owner actions development blockers.
- Update docs/STATUS.md with implementation truth, exact evidence and the next highest-priority unfinished item after meaningful work.
- Keep security, source/license review, privacy, resource budgets and rollback requirements active in every phase.

## How AI coding agents must choose work

At each session start, read AGENTS.md, docs/ROADMAP.md and docs/STATUS.md, inspect the live repository/branch/PR state, and select the first unfinished development acceptance criterion from the highest-priority incomplete phase. Work it through code, tests and documentation; then continue with the next unfinished criterion in that same phase without asking the owner to say "continue". Do not jump to computer control, 3D polish, custom model training or self-modification while the conversation-first milestone remains incomplete, except when the work is a direct prerequisite or fixes a blocking security/integration defect.

The actual owner-run voice/device acceptance and final approval do not prevent the AI from continuing to the next development task once all code-side acceptance work for the phase is complete. Label those owner checks explicitly and do not claim they were completed.



## 2026-11-20 development evidence addendum

The following implementation work is present on draft PR #3 (`development/complete-product-gaps`) and is not yet merged into `main`:

- Gateway: atomic per-request/daily/monthly paid-budget reservations; fail-closed handling of unknown prices and historical NULL-cost paid rows; cancellation-safe reservations; provider health and routing audit improvements.
- Memory: opt-in loopback-only Ollama embedding reranking over a bounded pool of structured records, knowledge items and recent non-temporary conversation messages; content-addressed SQLite vector cache with deletion/temporary-mode invalidation; lexical retrieval remains the fallback.
- Permissions: shared policy enforcement across TaskEngine, direct computer API routes and Brain broker; unregistered capabilities fail closed, including idempotent reads.
- Regression tests and documentation were added for these changes, but the latest branch tests have not been run locally.

Runtime self-description is now injected into Brain context from live registries (registered tools/providers, memory, permission broker, voice routes and Code Graph availability), with explicit caveats that registration does not prove health or permission. An opt-in browser-based “Hey Mia” wake phrase now has visible listening state, mute/stop and re-arm behavior; it is not offline detection and awaits owner device/privacy verification. A deterministic English/Urdu/Roman Urdu conversation-memory evaluation fixture and runner have been added (not yet run). Remaining Priority 1 work includes full-history semantic indexing/evaluation, model-assisted extraction and consolidation, richer architecture/limitations evidence, model escalation, reasoning-quality evaluation and complete text/voice pipeline verification.


### Code Graph — continuous development requirement

Code Graph is a cross-cutting Priority 0 developer capability, not a later product phase. It must remain active throughout every feature implementation:
- Keep a deterministic, source-backed map of Python modules, symbols, imports, routes and tests, with provenance and parser errors visible.
- Refresh automatically after repository source changes. The current watcher uses content digests (not only file timestamps) and safely rebuilds the complete graph; true file-level incremental indexing is a later optimization, not a reason to delay reliable freshness.
- Before changing a component, inspect its graph relationships and relevant tests; after changing it, refresh the graph and run Git-diff impact analysis to identify dependent modules and candidate regression tests.
- Keep static facts separate from inferred relationships. Dynamic imports, runtime injection and indirect dependencies must not be presented as proven edges.
- The graph API and browser explorer are local-only by default. Remote access requires explicit opt-in and a strong owner token.
- Code Graph acceptance requires deterministic indexing, stale/change detection, access-control tests, impact-analysis tests, and documentation of the indexed commit and parser limitations.
