# Deferred milestone backlog (M2–M6)

**Purpose:** Prepare implementation-ready work for the roadmap phases after Conversation-first Mia without diverting the six specialists from the current highest-priority milestone.

**State:** All tasks below are **DEFERRED / NOT ASSIGNED**. Suggested specialization is planning guidance only; it is not a task claim, agent registration, or evidence of background work.

**Gate:** No M2–M6 feature task may start until Priority 1 / M1 development exit criteria in `docs/ROADMAP.md` are met. Exceptions are limited to a direct M1 prerequisite, a blocking integration/security defect, or work explicitly approved by the owner. Code Graph maintenance and the ten-layer SPS architecture remain cross-cutting.

## M2 — Immersive 3D holographic interaction

| Task | Suggested specialist | Dependencies | Scope and acceptance criteria |
|---|---|---|---|
| MIA-HOLO-01 — Runtime-state and interaction contract | A6 | M1 exit gate | Define typed UI states/events for idle, listening, thinking, speaking, interrupted, tool-running, awaiting approval, failed and offline. Map each state to real backend evidence; add contract tests proving impossible/stale states are not shown. No visual-only fake activity. |
| MIA-HOLO-02 — 3D scene and accessible fallback | A6 | HOLO-01 | Implement responsive 3D scene with reduced-motion, low-power and no-WebGL fallbacks. Core conversation and all controls remain usable without 3D. Add deterministic UI tests for fallback and responsive states. |
| MIA-HOLO-03 — Holographic interaction, performance and accessibility | A6 | HOLO-02 | Bind scene transitions to actual conversation/voice/task events; support keyboard, screen-reader labels, contrast and reduced motion. Profile representative devices; document frame/resource budgets and verify graceful degradation. |

## M3 — Personal digital life and everyday assistance

| Task | Suggested specialist | Dependencies | Scope and acceptance criteria |
|---|---|---|---|
| MIA-LIFE-01 — Personal data and permission foundations | A1 + A5 | M1 exit gate | Define owner-scoped data models, source/provenance, retention, export/delete, encryption boundaries and permission scopes for tasks, projects, reminders and personal context. Add isolation, migration and deletion tests before adding integrations. |
| MIA-LIFE-02 — Unified tasks, projects and daily briefing | A1 + A6 | LIFE-01 | Deliver task capture, priorities, due dates, project association and owner-configurable daily briefing. Briefings must cite their source records, respect quiet hours and never invent calendar/task facts. Add API, persistence and UI tests. |
| MIA-LIFE-03 — Selected-file intelligence and browser research | A5 + A6 | LIFE-01; permission broker and sandbox available | Index only owner-selected files; support source-located search/summaries and scoped browser research with citations. Treat file/web content as untrusted input. Test path isolation, permission denial, citation provenance and deletion. |
| MIA-LIFE-04 — Notifications, backup and multi-device foundations | A2 + A5 | LIFE-01; secure identity/session model | Add deduplicated configurable notifications, encrypted backup/restore preview and integrity checks; design sync conflict handling and authenticated device registration. Test restore, revocation, duplicate suppression and offline/resource-pressure behavior. Do not expose unauthenticated remote services. |

## M4 — Secure computer intelligence and control

| Task | Suggested specialist | Dependencies | Scope and acceptance criteria |
|---|---|---|---|
| MIA-COMP-01 — Local companion and pairing protocol | A2 + A5 | M1 exit gate; permission broker design | Specify outbound-only authenticated pairing between cloud Mia and the owner's local Windows agent, device identity, scoped capability grants, revocation and emergency stop. Add protocol/security tests; no inbound public desktop-control port. |
| MIA-COMP-02 — Windows sandbox and capability adapters | A5 | COMP-01; sandbox threat model | Implement isolated filesystem/process/browser adapters with explicit schemas, resource limits and secret separation. Test traversal, symlink, process, network and sandbox escape boundaries; deny by default. |
| MIA-COMP-03 — Approval, audit, cancellation and recovery | A1 + A5 | COMP-02 | Enforce permission checks at execution time; require approval for destructive/external actions; support dry-run, cancellation, audit and reversible undo where possible. Test bypass attempts, revocation races and recovery paths. |
| MIA-COMP-04 — Voice-driven computer task flow | A4 + A6 | COMP-03; M1 voice pipeline complete | Route voice requests through the same Brain planner and permission broker as text. Confirm target/action before sensitive execution; support interruption and stop. Automated tests use fake desktop adapters; physical Windows checks remain owner verification. |

## M5 — Self-programming, custom models and governed evolution

| Task | Suggested specialist | Dependencies | Scope and acceptance criteria |
|---|---|---|---|
| MIA-EVOL-01 — Capability-gap discovery and provenance | A1 + A3 | M1 exit gate; evaluation evidence available | Detect candidate capability gaps from consented conversations, failures and evaluations. Attach source evidence, confidence, owner goal and duplicate detection. Do not infer demand from code volume or fabricate usage. |
| MIA-EVOL-02 — Isolated code generation and validation pipeline | A5 | EVOL-01; sandbox and Code Graph reliable | Generate candidate changes in disposable workspaces; run static, unit, integration, security and regression checks; produce diffs, provenance, license review and reproducible evidence. No generated code runs in privileged host process. |
| MIA-EVOL-03 — Governed promotion, rollback and capability lifecycle | A1 + A5 | EVOL-02; permission broker and audit trail | Implement capability contracts, least-privilege declarations, owner approval, staged activation, monitoring, immutable lineage, rollback and retirement. Validation failure must block promotion; governance boundary is not modifiable by candidates. |
| MIA-EVOL-04 — Custom foundation-model feasibility study | A3 + A2 | Stable evaluation suite and usage/resource telemetry | Produce benchmark, use-case and data-rights assessment, compute/storage/cost estimate and deployment options for open-weight hosting, fine-tuning and pretraining. Deliver an evidence-based go/no-go; do not start costly pretraining by default. |
| MIA-EVOL-05 — Disposable autonomous evolution research mode | A5 + A3 | EVOL-02 and EVOL-03; security review | Research autonomous exploration only inside a disposable clone with no production credentials, live owner data, host privileges, external side effects or access to trusted governance controls. Validate containment and teardown; promotion remains governed. Unrestricted live self-modification is out of scope. |

## M6 — Evolution Observatory and 30-day experiment

| Task | Suggested specialist | Dependencies | Scope and acceptance criteria |
|---|---|---|---|
| MIA-OBS-01 — Immutable baseline and evidence schema | A3 + A5 | M1 exit gate; stable evaluation definitions | Define Day 0 snapshot schema for software/model/config versions, environment, benchmark outputs, cost, latency, resource use and failure states. Preserve raw evidence and distinguish missing data from zero. |
| MIA-OBS-02 — Daily measurements and change attribution | A3 + A2 | OBS-01; telemetry and evaluation suites | Collect opt-in daily snapshots and link changes to exact commits, model/provider versions and configuration. Handle offline, paused, failed, no-op and rollback days honestly; add deterministic fixture tests. |
| MIA-OBS-03 — Trend dashboard and experiment report | A6 + A3 | OBS-02 | Present before/after comparisons, quality/reliability/safety/resource/cost trends and evidence links. No line-count growth claims or fabricated activity. Implement experiment infrastructure; starting and completing the real 30-day run remains owner activity. |

## Assignment and sequencing policy

1. Finish all M1 development acceptance criteria before assigning any task above.
2. After the M1 gate, reconcile the live source, tests, Code Graph and `docs/STATUS.md` against each task before claiming it; existing implementations may satisfy part or all of a task.
3. Assign only the next dependency-ready task in roadmap order. A suggested specialist is not a pre-assigned owner.
4. Keep the six existing worker slots; do not create extra agents for these phases. The coordinator serializes claims and shared-file/main commits.
5. Each task must be narrowed to source-backed gaps, with tests and exact evidence recorded in `docs/AI_INTEGRATION_LOG.md`.
6. Owner-only physical checks, credentials, deployment and the real 30-day experiment must be labelled `OWNER_VERIFICATION_REQUIRED`; they do not excuse unfinished code-side implementation.
