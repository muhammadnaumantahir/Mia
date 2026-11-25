# Mia milestone task catalog (M1–M6)

**Canonical task detail:** this file defines the implementation-ready task scope, dependencies, specialist fit and acceptance criteria for the six product milestones. The live ownership/status of every task is maintained in `docs/AI_TASK_BOARD.md`.

**Repository policy:** `main` only; no feature branches, pull requests or CI gate. Six worker slots A1–A6; the coordinator is not a seventh worker. Preserve exactly ten canonical SPS layers. Mia owns its gateway; FreeLLMAPI and OmniRoute are not runtime dependencies.

## How agents select work

1. Refresh `main`, `docs/AI_TASK_BOARD.md`, `docs/AI_AGENT_REGISTRY.md` and the recent integration log. Resume your own active task first.
2. Pick the highest-priority task that is `OPEN`, matches your specialist role, and has all explicit dependencies `DONE`.
3. Do not duplicate a task owned by another agent.
4. **Milestone handoff rule:** a later milestone becomes eligible for parallel work when every task in the preceding milestone is either `DONE` or already `IN_PROGRESS` under another agent. If any preceding-milestone task remains `OPEN`, claim that first. A task marked `BLOCKED` counts as accounted for only when it has a named owner, a precise blocker and a next action; it does not satisfy any explicit dependency.
5. This handoff rule permits parallel development; it does **not** waive dependencies or the milestone acceptance gate. Do not mark a milestone complete, release-ready or accepted until its own development exit criteria are met. Later work must not destabilize or bypass earlier gates.
6. If a task's acceptance is already satisfied by current source/tests, record evidence and mark it `DONE` only after verifying the actual implementation. Split out any remaining gap rather than rewriting working functionality.
7. If no eligible task exists, perform a source-backed audit of the next milestone and create a small, non-duplicative task. Never invent work merely to keep busy.

## Task definition contract

Every task below is a bounded implementation slice. Before claiming it, the agent must re-audit the live source, tests and Code Graph, then record exact allowed paths in the board. A suggested specialist is a routing hint, not a claim. Keep one active task per agent.

A task is complete only when:
- acceptance criteria are implemented and relevant tests are added/updated;
- exact local commands and results are recorded (or clearly state why unavailable);
- security/privacy and failure paths are covered;
- relevant docs and task board are updated;
- the commit is verified on remote `main`;
- remaining physical/manual checks are labelled `OWNER_VERIFICATION_REQUIRED`.

---

## M1 — Conversation-first Mia

**Goal:** The owner can have natural, thoughtful, multilingual text and voice conversations with Mia; ask about Mia itself; refer to previous discussions; and receive truthful, source-grounded responses.

**Existing live tasks:** `MIA-BRAIN-06`, `MIA-BRAIN-07`, `MIA-EVAL-05` and any other P1 rows on the live task board. Do not recreate them under new IDs.

**Additional A2 queue:** `MIA-GW-05` is a focused gateway-readiness audit so the gateway specialist has a first-milestone task after the existing gateway slices.

| Task | Specialist | Dependencies | Bounded scope and acceptance criteria |
|---|---|---|---|
| MIA-M1-08 — Conversation-first acceptance traceability audit | A1 + A3 | Existing P1 tasks inspected | Map every M1 roadmap exit criterion to concrete source modules, automated tests and owner checks. Identify only demonstrable gaps; create child tasks with unique IDs and exact paths. Deliver a traceability matrix with no duplicate work and no unsupported completion claims. |
| MIA-GW-05 — Conversation gateway readiness audit | A2 | MIA-GW-01 through MIA-GW-04 reviewed | Trace the live gateway end-to-end against M1 requirements: local-first behavior, provider/model discovery, routing, fallback, cancellation, streaming, credential isolation, usage ledger, request/daily/monthly budgets and truthful capability reporting. Implement only the highest-impact source-backed gap not already covered by GW-03/GW-04; add fake-provider regression tests. Never add FreeLLMAPI or OmniRoute runtime dependencies. |
| MIA-M1-09 — Text/voice shared-Brain end-to-end contract | A1 + A4 | Existing text and voice routes available | Trace a text turn and each supported voice path through identity, context, Brain, gateway, verification, persistence and response. Add integration tests proving shared identity/memory/context and consistent permission/error handling. Fix only verified integration gaps. |
| MIA-M1-10 — Memory lifecycle owner controls audit | A1 + A6 | Memory store and existing retention controls | Audit inspect, correct, export and delete flows for conversation and structured memory. Implement missing owner-facing/API controls with provenance, confirmation for destructive actions, retention consistency and tests proving deleted data is not recalled. Do not duplicate existing retention work. |
| MIA-M1-11 — Multilingual voice quality and fallback audit | A4 + A3 | Existing voice pipeline and evaluation fixtures | Audit actual STT/TTS engines, streaming, English/Urdu/Roman Urdu handling, language selection, transcript correction, failure fallback and latency reporting. Add deterministic adapter tests and quality fixtures; do not claim real speech quality from synthetic audio. |
| MIA-M1-12 — Conversation interruption and recovery integration | A4 + A6 | Existing stop/retry and voice controls | Verify interruption/barge-in, stop, mute, microphone release, retry, provider timeout and partial-response persistence across text and voice. Add regression tests for race conditions and truthful UI state. Preserve manual-start fallback and explicit wake-word opt-in. |
| MIA-M1-13 — M1 release evidence and owner-check handoff | A1 + A3 + A4 + A6 | M1 development tasks complete | Run the repeatable M1 evaluation suite and relevant integration tests; produce a criterion-by-criterion evidence report. Separate automated development completion from real microphone, speaker, Windows, accessibility and live-provider owner checks. Do not mark M1 complete if any development criterion remains open. |

**M1 gate:** satisfy all Priority 1 exit criteria in `docs/ROADMAP.md`. Owner-only hardware/live-provider checks are reported separately and do not block code-side implementation.

---

## M2 — Immersive 3D holographic interaction

**Goal:** A polished Jarvis-style 3D interaction surface that visualizes actual Mia runtime state while keeping conversation fully usable without 3D.

| Task | Specialist | Dependencies | Bounded scope and acceptance criteria |
|---|---|---|---|
| MIA-HOLO-01 — Runtime-state and event contract | A6 + A1 | Existing conversation/voice state APIs | Define typed UI states/events for idle, listening, thinking, speaking, interrupted, tool-running, approval, failed and offline. Map every state to real API/event evidence; define stale/unknown handling. Add contract tests. No fabricated activity or new SPS layer. |
| MIA-HOLO-02 — 3D renderer spike and architecture decision | A6 | HOLO-01 | Compare maintainable browser 3D options against bundle size, WebGL support, Windows hardware, licensing and accessibility. Implement a minimal isolated renderer spike and document an evidence-based choice plus fallback. No full visual build before the decision. |
| MIA-HOLO-03 — Holographic scene and visual system | A6 | HOLO-02 | Implement responsive scene, Mia visual identity, idle/listening/thinking/speaking/error transitions and restrained motion. Keep scene components isolated from Brain/business logic. Add deterministic rendering/state tests. |
| MIA-HOLO-04 — Live interaction and multimodal bindings | A6 + A4 + A1 | HOLO-01, HOLO-03 | Bind scene to real conversation, voice, task, tool and approval events. Handle reconnect, stale events, cancellation and offline state. Verify state changes through mocked API/event streams; never infer a tool action from animation. |
| MIA-HOLO-05 — Accessible fallback and reduced motion | A6 | HOLO-03 | Provide non-3D mode, reduced-motion setting, keyboard controls, accessible names/status announcements, contrast and narrow-screen layout. Core chat/voice remains usable when WebGL is missing or disabled. Add browser tests. |
| MIA-HOLO-06 — Performance and graceful degradation | A6 + A5 | HOLO-04, HOLO-05 | Define frame-time, memory, CPU/GPU and bundle budgets; profile representative low-power configurations; throttle effects and stop rendering when hidden/idle where safe. Add performance smoke checks and document untested owner hardware. |

---

## M3 — Personal digital life and everyday assistance

**Goal:** Give Mia useful personal workflows without prematurely granting unrestricted computer control.

| Task | Specialist | Dependencies | Bounded scope and acceptance criteria |
|---|---|---|---|
| MIA-LIFE-01 — Personal-data model and privacy contract | A1 + A5 | M1 persistence and identity contracts | Define owner-scoped records, project association, source/provenance, retention, export/delete and encryption boundaries for tasks, reminders and personal context. Add migration, isolation and deletion tests. |
| MIA-LIFE-02 — Unified task manager API and persistence | A1 | LIFE-01 | Implement task capture, title/details, status, priority, due date, recurrence policy, project link and audit history. Validate inputs, timezone semantics, duplicate handling and migrations. Add API/store tests. |
| MIA-LIFE-03 — Task workspace UI and conversational actions | A6 + A1 | LIFE-02 | Build accessible task list/detail/filter/complete/edit UI and Brain intents for task operations. Require confirmation for destructive bulk changes; ensure UI and conversational views share one source of truth. Add browser/API integration tests. |
| MIA-LIFE-04 — Project workspace and project continuity | A1 + A6 | LIFE-01, LIFE-02 | Provide project goals, status, milestones, decisions, linked tasks and project-scoped conversation context. Enforce isolation between projects and unassigned/private context. Add migration and cross-project leakage tests. |
| MIA-LIFE-05 — Daily briefing and goal follow-up | A1 + A3 | LIFE-02, LIFE-04 | Generate configurable daily/weekly briefings only from available owner-approved task/project/reminder sources. Cite source records, timezone, freshness and missing data; respect quiet hours and never invent calendar events. Add deterministic fixtures. |
| MIA-LIFE-06 — Selected-file indexing and source-grounded answers | A5 + A1 | LIFE-01; M4 filesystem permission contract available | Index only explicitly selected roots; track file identity/version, access scope and source locations. Support search/summaries with citations, deletion/re-index and path isolation tests. Treat file content as untrusted. |
| MIA-LIFE-07 — Scoped browser research and saved findings | A5 + A6 | LIFE-01; safe browser/research boundary | Research bounded questions, compare source quality/date, cite claims and save owner-reviewed findings. Add source provenance, prompt-injection handling and tests for untrusted page content. Do not execute page instructions. |
| MIA-LIFE-08 — Workflow composer and checkpoint runtime | A1 + A5 | LIFE-02, LIFE-04; permission contracts | Compose bounded task/project workflows with typed steps, preview, checkpoints, timeout, cancellation and audit. Start with non-destructive internal actions; require approval for external/destructive steps. Add failure/retry tests. |
| MIA-LIFE-09 — Notification center and quiet-hours policy | A2 + A6 | LIFE-01, LIFE-02 | Add owner-configurable channels, quiet hours, relevance/frequency limits, deduplication, expiry and snooze. Show why each notification exists and its source; test timezone/DST, duplicate and offline cases. |
| MIA-LIFE-10 — Personal knowledge graph | A1 + A5 | LIFE-04, LIFE-06, LIFE-07 | Link people, projects, topics, decisions, tasks and evidence with typed provenance and confidence. Distinguish confirmed from inferred links; support inspect/correct/delete and project isolation. Add graph integrity tests. |
| MIA-LIFE-11 — Encrypted backup and restore | A2 + A5 | LIFE-01; key-management design | Implement configurable encrypted backup, integrity verification, restore preview, conflict policy and recovery tests. Keep keys outside repository/logs; fail closed on invalid or partial archives. |
| MIA-LIFE-12 — Usage, resource and storage dashboard | A2 + A6 | Gateway and runtime telemetry available | Show real provider/model usage, spend, latency, CPU/RAM/storage and background workload with source, timestamp and unavailable states. Unknown values remain unknown, not zero. Add API/UI fixture tests. |
| MIA-LIFE-13 — Authenticated device sync and remote access | A2 + A5 | LIFE-01; existing COMP-02 device identity/session implementation | Define device enrollment, revocation, encrypted transport, scoped sync and conflict resolution. Bind remote access securely; no public unauthenticated endpoint. Add replay/revocation/isolation tests. |
| MIA-LIFE-14 — Proactive companion policy | A1 + A3 | LIFE-05, LIFE-09, LIFE-12 | Add bounded proactive suggestions based on real unfinished tasks/goals with relevance, novelty, quiet hours, frequency caps, opt-out and emergency stop. Explain rationale and evidence; test duplicate/stale/low-confidence suppression. |

---

## M4 — Secure computer intelligence and control

**Goal:** Allow useful local and remote computer operations through a paired, auditable, least-privilege execution boundary.

| Task | Specialist | Dependencies | Bounded scope and acceptance criteria |
|---|---|---|---|
| MIA-COMP-01 — Threat model and capability contract | A5 + A1 | Existing governance APIs and COMP-02 device identity/session implementation; coordinate with LIFE-13 for transport/sync boundaries (no circular wait) | Document attacker model, trust boundaries, capability schema, device identity, approval classes, revocation and emergency stop. Map every tool to side effects, permissions, resource bounds and recovery. |
| MIA-COMP-02 — Outbound-only local companion pairing | A2 + A5 | COMP-01 capability/trust-boundary contract | Implement authenticated outbound-only pairing for a local Windows agent; device-scoped credentials, challenge/replay protection, renewal and revocation. No public inbound desktop-control port. Add protocol security tests. |
| MIA-COMP-03 — Windows companion service lifecycle | A5 | COMP-02 | Implement install/run/stop/update/health lifecycle with least privilege, visible owner status and safe reconnect. No silent persistence or privilege escalation. Add mocked Windows adapter tests and owner verification notes. |
| MIA-COMP-04 — Central permission broker enforcement | A1 + A5 | COMP-01 | Enforce authorization at the final execution boundary for Brain, voice, browser, filesystem, desktop and background tasks. Deny by default; test forged UI approval, stale grants, scope mismatch and revocation races. |
| MIA-COMP-05 — Disposable execution sandbox | A5 | COMP-01, COMP-04 | Implement isolated process/workspace execution with filesystem/network/process/resource restrictions and teardown. Prove generated/untrusted code cannot access secrets or trusted governance files. Add adversarial escape tests. |
| MIA-COMP-06 — Scoped filesystem and document adapter | A5 | COMP-04, COMP-05 | Add list/read/search/write/move operations only inside granted roots; block traversal, symlink escapes, device paths and unauthorized extensions. Writes require preview, exact approval and recoverable backup. |
| MIA-COMP-07 — Isolated browser automation adapter | A5 + A6 | COMP-04, COMP-05 | Provide isolated browser sessions with navigation/read actions and separately approved click/form/download actions. Defend against page prompt injection and cross-origin/session leakage. Add browser integration tests. |
| MIA-COMP-08 — Desktop application and process adapter | A5 | COMP-04, COMP-05 | Implement allowlisted application launch, window/process inspection and safe close operations. Destructive process termination needs approval; prevent arbitrary shell execution. Add fake adapter tests. |
| MIA-COMP-09 — Approval, audit, cancellation and recovery UX | A1 + A6 | COMP-04, COMP-06, COMP-07, COMP-08 | Show exact action, target, data and side effects before approval; persist audit; support cancellation and rollback/undo where possible. Test timeout, disconnect, duplicate submit and partial completion. |
| MIA-COMP-10 — Voice-driven computer task flow | A4 + A6 | COMP-09; M1 voice pipeline complete | Route voice commands through the same Brain planner and broker as text. Read back ambiguous targets and confirm sensitive actions; interruption must cancel safely. Test with fake adapters. |
| MIA-COMP-11 — Security regression and owner handoff | A5 + A3 | COMP-02 through COMP-10 | Run adversarial tests for sandbox escape, permission bypass, credential leakage, replay, prompt injection, cancellation and audit integrity. Record exact test evidence and target-Windows owner checks. |

---

## M5 — Self-programming, custom models and governed evolution

**Goal:** Let Mia identify capability gaps and improve safely; research custom model development without making costly pretraining a prerequisite.

| Task | Specialist | Dependencies | Bounded scope and acceptance criteria |
|---|---|---|---|
| MIA-EVOL-01 — Evidence-based capability-gap discovery | A1 + A3 | M1 evaluation evidence; owner-consented signals | Detect gaps from real failed tasks, corrections, evaluations and owner goals. Store evidence, confidence, impact, consent scope and duplicate links. Never infer need from code volume or fabricate demand. |
| MIA-EVOL-02 — Existing generation/validation pipeline audit and safeguard | A5 | Existing evolution implementation; owner-approved current claim | Continue the existing A5 task: audit source against this catalog, identify the highest-impact missing safeguard, implement it with regression tests. Generated code must not run in the privileged process; failed validation blocks activation. Do not expand beyond the active task without a new claim. |
| MIA-EVOL-03 — Candidate generation workspace | A5 | EVOL-01; COMP-05 sandbox | Generate proposed code/tests in disposable workspaces with bounded model calls, file allowlists and immutable input snapshots. Record model/provider, prompt template version, source provenance and diff. No direct writes to trusted runtime. |
| MIA-EVOL-04 — Validation, security and reproducibility pipeline | A5 + A3 | EVOL-03 | Run format/static/type checks, unit/integration/regression/security tests and resource limits. Store reproducible logs and artifact hashes; validation failures block registration. Test malicious and malformed candidates. |
| MIA-EVOL-05 — Capability contract and lineage registry | A1 + A5 | EVOL-03, EVOL-04 | Define versioned capability schema, inputs/outputs, permissions, dependencies, side effects, parent lineage, validation evidence and retirement state. Reject undeclared permissions and broken lineage. |
| MIA-EVOL-06 — Governed activation, monitoring and rollback | A1 + A5 | EVOL-04, EVOL-05; COMP-04 | Require owner approval for privileged capabilities, staged activation, health monitoring, immutable decision records and tested rollback. Candidate code cannot modify governance or approval policy. |
| MIA-EVOL-07 — Disposable autonomous evolution research mode | A5 + A3 | EVOL-04, EVOL-06; security review | Explore autonomous edits only in disposable clones with no production credentials, live owner data, host privileges, external side effects or governance access. Verify containment and teardown. Production promotion remains governed; unrestricted live self-modification is out of scope. |
| MIA-EVOL-08 — Custom foundation-model feasibility study | A3 + A2 | Stable evaluation suite and telemetry | Compare open-weight inference, adapters/LoRA, task-specific fine-tuning and pretraining. Assess data rights, compute, storage, cost, benchmark, deployment and maintenance. Deliver go/no-go; no expensive training without owner approval. |
| MIA-EVOL-09 — Small-model adaptation experiment | A3 + A2 | EVOL-08 go decision; lawful dataset available | If feasible, run a reproducible small-scale adaptation experiment with licensed data, held-out evaluation, baseline comparison and rollback. If not feasible, document a justified no-go rather than fabricate results or spend money. |

---

## M6 — Evolution Observatory and 30-day experiment

**Goal:** Measure real capability, quality, reliability, safety and resource changes with traceable evidence.

| Task | Specialist | Dependencies | Bounded scope and acceptance criteria |
|---|---|---|---|
| MIA-OBS-01 — Measurement definitions and immutable evidence schema | A3 + A5 | Stable M1/M5 evaluation definitions | Define metric names, units, sampling, provenance, missing/zero semantics and version fields for software, model, provider, config and environment. Preserve raw evidence and schema versioning. |
| MIA-OBS-02 — Day 0 baseline capture | A3 + A5 | OBS-01; clean reproducible environment | Capture immutable baseline commit/tree, dependency/model/config versions, benchmark outputs, latency, cost and resource samples. Refuse to overwrite baseline; missing measurements remain missing. |
| MIA-OBS-03 — Daily telemetry and snapshot scheduler | A2 + A5 | OBS-01, OBS-02; resource telemetry | Capture opt-in daily snapshots with bounded CPU/RAM/network use, idempotency and pause/resume. Record offline, paused, failed and no-op days explicitly. |
| MIA-OBS-04 — Commit/model/config change attribution | A3 + A5 | OBS-02, OBS-03 | Link each metric change to exact commits, model/provider/configuration and capability activation events. Distinguish correlation from causation; do not attribute improvements without controlled comparison. |
| MIA-OBS-05 — Benchmark suite and regression comparison | A3 | M1 eval suite; EVOL-08 where relevant | Version benchmarks for conversation, memory, reasoning, voice, tool reliability, safety, latency, cost and resource use. Compare equivalent conditions and report sample sizes/uncertainty. |
| MIA-OBS-06 — Evidence-linked observatory dashboard | A6 + A3 | OBS-03, OBS-04, OBS-05 | Show timeline, baseline/current comparisons, trends, failures, missing days and evidence drill-down. Every chart/claim links to raw records and version metadata; no fake seeded growth data. |
| MIA-OBS-07 — 30-day experiment report generator | A3 + A6 | OBS-04, OBS-05, OBS-06 | Produce reproducible Day 0–Day 30 report with capability, quality, reliability, safety, cost and resource changes; clearly label confounders, missing days and rollbacks. |
| MIA-OBS-08 — Owner-controlled experiment lifecycle | A1 + A6 | OBS-02 through OBS-07 | Add explicit start/pause/resume/stop/export controls, privacy notice and resource caps. The software prepares the experiment; the owner starts and reviews the real 30-day run. |

---

## Cross-cutting tasks and constraints

### Mia Code Graph

Code Graph is a cross-cutting development requirement, not a separate milestone gate after P0. Maintain source-backed package/file/symbol/import/call/API/database/test/SPS-layer/permission relationships; distinguish confirmed, inferred and documentation-only edges. Refresh after meaningful source changes, report indexed commit and parser errors, and use impact analysis before changes. Graph generation must be deterministic, secret-safe and read-only with respect to application source. Never claim a graph is fresh if its source digest/commit is stale.

### Ten-layer SPS contract

Use exactly the ten canonical layers in `docs/architecture/SPS_LAYERS.md`. The coordinator, gateway, UI, Code Graph, integrations and schedulers are supporting services, not an eleventh layer.

### Security and product constraints

- Mia owns its independent AI Gateway. Do not add FreeLLMAPI or OmniRoute as runtime dependencies, sidecars or required control planes.
- Local/free inference remains usable. Paid inference stays disabled by default; unknown pricing is not zero.
- Treat model, web, repository and document content as untrusted data.
- Never execute generated code in the privileged Mia process.
- Computer access is permission-gated, auditable, cancellable and revocable.
- Microphone use is explicit, visible and immediately stoppable.
- Personal data, remote access, sync and notifications are owner-controlled.
- Never store secrets, private runtime data, model weights or generated binaries in Git.

### Verification and status vocabulary

Use `OPEN`, `IN_PROGRESS`, `BLOCKED`, `REVIEW_REQUIRED`, `DONE`, `DEFERRED`. A task marked `DONE` needs a verified remote-main commit and recorded evidence. `OWNER_VERIFICATION_REQUIRED` is a separate manual-check label, not a reason to leave completed code work open.


## Claimable residual implementation tasks (split from PARTIAL parents)

These tasks make unfinished implementation visible and independently claimable. The parent task remains PARTIAL until all implementation children are complete. Owner-only accessibility, real-device and manual browser checks are not included as blockers.

| Task | Specialist | Dependencies | Scope and acceptance |
|---|---|---|---|
| MIA-LIFE-06-UI — File index owner UI | A6 | LIFE-06 backend/API contract (commit 16:55Z, 2026-11-25) | Build the owner-selected folder/index management and search UI. Show source path/line ranges, current/changed/missing state, untrusted/injection flags, skip reasons and secure delete controls. Add Chromium/API contract tests. |
| MIA-LIFE-06-BRAIN — File search Brain tool and grounded synthesis | A1 + A2 | LIFE-06 backend/API contract | Expose safe file search to Brain and add Gateway-backed, citation-grounded summaries from retrieved chunks. Never treat indexed text as instructions; enforce project/privacy scope and source verification. Add fake-provider and isolation tests. |
| MIA-LIFE-06-WATCH — File index refresh and document extraction | A5 | LIFE-06 backend/API contract | Add bounded background refresh/watch behavior and optional PDF/Office extraction through the documents extra. Preserve root confinement, deletion semantics, stop-all and provenance. Add deterministic watcher/extraction tests. |
| MIA-LIFE-07-SEARCH — Research search and model synthesis | A2 | Existing LIFE-07 research API/backend (9668d84) | Add bounded search discovery and Gateway-backed synthesis with source-linked claims; preserve owner allowlists, exact-quote citation validation, budgets and stop controls. Add fake-provider/search tests. |
| MIA-LIFE-07-MEMORY — Research finding memory integration | A1 | Existing LIFE-07 findings store; BRAIN-06 project-scoped memory | Allow owner-confirmed findings to be stored/recalled as structured memory with provenance, project scope, correction and deletion. No unreviewed claim becomes memory. Add isolation and lifecycle tests. |
| MIA-LIFE-07-INJECTION — Research content hardening | A5 + A3 | Existing LIFE-07 fetch/citation boundary | Strengthen injection handling beyond phrase patterns using structural isolation and adversarial tests. Untrusted page text must never become system/developer instructions; citations remain exact and owner-reviewed. |
| MIA-LIFE-08-BRAIN — Brain and voice workflow entry | A1 + A4 | Existing LIFE-08 workflow API/engine (4510573) | Let Brain create/preview/run safe internal workflows and route voice through the same planner. Keep external/destructive steps refused until the broker-gated child is complete. Add deterministic Brain/voice tests. |
| MIA-LIFE-08-APPROVAL — Approval-gated external workflow steps | A1 + A5 | COMP-04 human/device approval enforcement | Add typed external/destructive workflow capabilities only after final-boundary approval is enforced. Exact preview, scoped approval, cancellation, audit and no side effect before approval. |
| MIA-LIFE-08-TIMEOUT — Workflow timeout and unknown-outcome recovery | A5 | Existing LIFE-08 workflow engine | Make timeout semantics explicit and recoverable: distinguish failed-before-start from unknown-after-start, prevent unsafe automatic retries, expose reconciliation and owner-safe retry. Add deterministic timeout/restart tests. |
| MIA-COMP-01-HANDLERS — Handler-level capability proof | A5 | COMP-01 inventory and current COMP-04 enforcement | Prove every mutating route/handler matches its declared capability and that unclassified handlers fail closed. Add exhaustive route-to-handler tests and keep inventory current. |
| MIA-COMP-01-IDENTITY — Device identity and remote trust model | A1 + A5 | COMP-02 device identity/session implementation; LIFE-13 transport/sync contract | Replace the labelled identity assumption with a source-backed trust model covering owner vs device identity, remote transport, scopes, revocation and threat boundaries. No unauthenticated public endpoint. |
| MIA-COMP-04-OWNER — Authenticated owner approval | A1 + A5 | COMP-02 device identity/session implementation; LIFE-13 owner/device trust contract | Bind approval creation/decision to authenticated owner authority, not merely a local caller or payload card. Deny forged/local-process approval, enforce revocation and race safety; add adversarial tests. |
