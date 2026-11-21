# Mia multi-agent task board

**Worker slots:** A1–A6  
**Coordinator:** master-prompt role (not a worker slot)  
**Branch policy:** direct commits to `main` only  
**Last reconciled:** 2026-11-21

This is the shared queue. Always refresh it from `main` before claiming or changing a task. The roadmap and acceptance criteria in `docs/ROADMAP.md` are authoritative. Follow `docs/AI_CONTINUE_WORKFLOW.md` on every Continue request.

## Active task queue

| Task | Priority | Suggested owner | Status | Scope / acceptance |
|---|---:|---|---|---|
| MIA-BASE-01 | P0 | A5 | DONE (commit bb01d32) | Inspect Code Graph freshness workflow and identify exact gap between watcher/full rebuild and incremental indexing. Source-backed gap report and safe implementation plan. |
| MIA-BASE-02 | P0 | A5 | DONE (commit 6c58fb6) | Implement per-file incremental Code Graph indexing with fact cache/global resolution. Incremental output must equal clean rebuild after add/modify/remove/rename. |
| MIA-BASE-03 | P0 | A5 | DONE (commit ffef797) | Fix browser automation end-to-end test permission setup without weakening route policy. |
| MIA-BRAIN-01 | P1 | A1 | DONE (commits 5887f86 + e9b15b7) | Audit cross-session/project/provider continuity and implement bounded regression-backed improvement. |
| MIA-BRAIN-02 | P1 | A1 | DONE (commit f61cdd3) | Provider-switch recall, remote-provider privacy withholding and owner-correction precedence tests. |
| MIA-BRAIN-03 | P1 | A1 | DONE (commit ba7f6cf) | Opt-in project-scoped conversation continuity, migration/API/recall and isolation tests. |
| MIA-BRAIN-04 | P1 | A1 | DONE (A1, commit ee6fe3f; retention slice only) | Owner-controlled retention for retired structured records: `structured_retention_days` setting (default keep), `MemoryStore.purge_retired_records` (dry-run default, retired statuses only, audited by id), `POST /api/memory/records/retention`. 11 tests in tests/test_structured_retention.py. Record-level project scoping split to MIA-BRAIN-06. |
| MIA-BRAIN-05 | P1 | A1 | DONE (A1, commit 0d22d02) | Wake-word self-description now derives from `offline_wake_word_status()` evidence, never route/tool names. Also fixed a defect that dropped the live runtime-evidence block from every chat (`ProviderRegistry.keys()` does not exist). Interface for A4 (MIA-VOICE-04): pass `transport_available=True` to `offline_wake_word_status()` in `current_identity()` in `src/mia/observatory/api.py` once the wake route is registered. |
| MIA-BRAIN-06 | P1 | A1 | OPEN | Record-level project scoping for structured memory: records and retrieval have no project association, so a project conversation can retrieve another project's decisions/facts. Needs a short design note first (column vs relationship to a `project` record; opt-in; temporary/private isolation; migration). Paths: `src/mia/memory/**`, `src/mia/brain/context.py`, tests. |
| MIA-BRAIN-07 | P1 | A1 | OPEN | Bound the live runtime-evidence prompt block. Once it started appearing (MIA-BRAIN-05 fix) the system prompt grew from about 1.1 KB to about 4.1 KB with no tools registered, and it grows with the tool list. Measure with real tool registrations, cap or compact the block, and make sure the context budget accounts for it, without losing truthful capability statements. Paths: `src/mia/brain/self_description.py`, `src/mia/brain/context.py`, tests. |
| MIA-UX-01 | P1 | A6 | DONE (commit 5ba0e2f) | Audit conversation UX and implement one isolated, testable improvement. |
| MIA-UX-02 | P1 | A6 | DONE (commit 4ae4a32) | Owner-visible project control: show current conversation project, set/clear through existing project API, and show it in conversation list. Opt-in, keyboard accessible and truthful. Paths: `src/mia/assistant.html`, matching tests. |
| MIA-GW-01 | P1 | A2 | DONE (commits de4f185 + 84d43a7) | Trace verifier confidence and gateway routing; implement bounded quality escalation only where a real gap exists. |
| MIA-GW-02 | P1 | A2 | DONE (commit 24e591e) | Expose read-only escalation settings and per-turn counters; document streaming decision. Fail-closed and opt-in. |
| MIA-GW-03 | P1 | A2 | DONE (commit 21a8e6d on main, 2026-11-21T15:34Z) | Audit current main against the AI Gateway roadmap and trace end-to-end routing, model capability selection, fallback, budgets, cancellation and observability. Implement the single highest-impact missing conversation-first behavior with fake-provider regression tests. Paths: `src/mia/gateway/**`, gateway API/tests/docs. Do not add FreeLLMAPI/OmniRoute. |
| MIA-GW-04 | P1 | A2 | OPEN | From the MIA-GW-03 audit: non-streaming `ModelGateway.complete()` has no gateway-level timeout (real adapters rely on httpx timeouts; `stream_idle_timeout` is opt-in and unset by default). Add a bounded, configurable per-attempt timeout that counts as a transient failure (fallback + breaker + reservation handling) with fake-hung-provider regression tests, and make streaming idle timeout default-on with a safe value. Paths: `src/mia/gateway/service.py`, factory/config, tests, docs. |
| MIA-EVAL-01 | P1 | A3 | DONE (commit 186f5d0) | Expand deterministic reasoning-quality scenarios to 23 and fix verifier failure reporting. |
| MIA-EVAL-02 | P1 | A3 | DONE (commit 387420f) | Add opt-in bounded parallel judge orchestration with privacy, concurrency, timeout, cost and fallback controls. |
| MIA-EVAL-03 | P1 | A3 | DONE (commit 9c29972 on main, 2026-11-21) | Judges wired into `Brain._execute` after verification (opt-in via `MIA_JUDGES_*`, off by default): A2's `make_judge_call` pins one candidate through the gateway, advisory report recorded in trace + verification, confidence can only be lowered, reply text untouched, privacy inherited (memory-using or local-only turns use local judges only), failures fall back silently. Read-only `GET /api/brain/judges`. 8 end-to-end + 20 unit tests with fake providers. Not wired: streamed turns. No live-model quality evidence. |
| MIA-EVAL-04 | P2 | A3 | IN_PROGRESS (A3, claimed 2026-11-21T15:40Z) | Judge streamed turns (after the stream completes, advisory only) and extend `mia.brain.evaluation` with scripted judge scenarios (split/insufficient/privacy) so judge behavior is regression-tested in the scenario runner. |
| MIA-UX-06 | P2 | A6 | OPEN | Owner-visible read-only judges panel: show `/api/brain/judges` policy + counters next to escalation (off by default, advisory-only wording, no write controls). Coordinate on the shared UI file. |
| MIA-EVAL-03a | P1 | A2 | DONE (commit a1609a9 on main, 2026-11-21T15:29Z) | Split from MIA-EVAL-03 (gateway side only): `src/mia/gateway/judge_call.py` `make_judge_call` pins one candidate through the normal gateway (budget reservations apply). Does NOT touch orchestrator.py; the Brain hook, trace recording and owner-visible settings stay in MIA-EVAL-03 (A1/A3, serialized). |
| MIA-VOICE-01 | P1 | A4 | DONE (commit ee0daa0) | Add offline wake-word detector contract, safe session lifecycle, status evidence and integration plan. Audio transport/UI/speech remain separate tasks. |
| MIA-VOICE-02 | P1 | A4 | DONE (transport module + tests; route wiring deferred to MIA-VOICE-04) | Loopback-only audio transport: `src/mia/voice/wake_transport.py`, `tests/test_voice_wake_transport.py` (31 tests incl. real uvicorn + websockets client). **Not wired into `create_app`**; no wake route exists in a running Mia yet. |
| MIA-VOICE-04 | P1 | A4 | OPEN (blocked on MIA-BRAIN-05) | One-line `register_wake_routes(app, record_event=observatory.record_event)` wiring in `src/mia/observatory/api.py`, a test that the route set and `offline_wake_word` status stay truthful, then update Plan section 6a. |
| MIA-VOICE-03 | P1 | A4 | DONE (A4, 2026-11-21) | English, Roman Urdu and Urdu-script end/stop-speaking phrases in `assistant.html` (data-only change to `END_PHRASES` / `STOP_SPEAKING_PHRASES`; the short-utterance guard is unchanged) with 26 new Chromium tests incl. long-question negatives. Not verified with real microphone speech or real Whisper/browser transcripts (spelling variants will differ). |
| MIA-UX-03 | P1 | A6 | DONE (commit 2915e57) | Conversation error/recovery UX: error-aware provider/network/permission/budget messages, retry failed reply, assess accessible live announcements. Existing API contracts only; add contract and Node harness tests. |
| MIA-UX-04 | P1 | A6 | OPEN (blocked on MIA-VOICE-02 + MIA-BRAIN-05) | Opt-in offline wake-word UI: persistent listening badge, visible Stop, 4-hour auto-stop/license notice, AudioWorklet 16 kHz int16 frames, activation handoff. Follow `docs/voice/WAKE_WORD_PLAN.md`; microphone remains off by default. |
| MIA-UX-05 | P1 | A6 | DONE (commit 14d748b) | Microphone visibility and stop safety for the one-shot Record button in `assistant.html` (A6 audit): the always-visible mic badge stays "Mic off" while recording; mic tracks are released only after transcription finishes; Esc does not stop recording; `pagehide` does not release an active recording. Fix with tests; no backend or voice-session changes; does not touch wake-word code (MIA-UX-04). |
| MIA-BASE-04 | P1 | A5 | DONE (A5, commit ef935a2 on main, 2026-11-21) | Audit Code Graph freshness and impact analysis against current main after the recent multi-agent changes. Verify graph/test provenance, stale detection and source-to-test impact; fix one concrete gap with deterministic tests. Record indexed commit and local refresh limitations. Paths: `src/mia/codegraph/**`, `scripts/update_code_graph.py`, graph tests/docs. Code Graph maintenance remains cross-cutting. |
| MIA-BASE-05 | P1 | A5 | IN_PROGRESS (claimed 2026-11-21T15:45Z) | Report stored-graph staleness in `GET /api/code-graph/status` (graph commit/tree state, HEAD, source-digest match) and show it in the Code Graph explorer; include old-path dependents for renamed/deleted files by consulting the previously stored graph. Paths: `src/mia/codegraph/**`, status route only in `observatory/api.py`, graph tests/docs. |
| MIA-FIX-01 | P1 | A5 | DONE (duplicate of MIA-BASE-03; commit ffef797) | Duplicate browser permission test report; resolved by MIA-BASE-03. |
 
## Deferred roadmap milestones (not assigned)

The implementation-ready M2–M6 backlog is in [`docs/DEFERRED_MILESTONE_BACKLOG.md`](DEFERRED_MILESTONE_BACKLOG.md). It contains scoped tasks, dependencies, suggested specialist roles and acceptance criteria for:
- M2 — Immersive 3D holographic interaction
- M3 — Personal digital life and everyday assistance
- M4 — Secure computer intelligence and control
- M5 — Self-programming, custom models and governed evolution
- M6 — Evolution Observatory and 30-day experiment

**All M2–M6 tasks are DEFERRED / NOT ASSIGNED.** Suggested specialist roles are not claims or active assignments. Do not divert agents from M1. Re-audit source and tests after M1 before assigning, because some future-phase foundations may already exist.

## Continue and queue rules

- On every owner **Continue**, each available agent resumes its own `IN_PROGRESS` task before taking new work. A status-only response is not sufficient when development is possible.
- Finish the current bounded task, verify and commit it, then claim the next highest-priority eligible task for that specialization. Continue through additional tasks while the turn budget permits.
- If interrupted by a usage/context limit, checkpoint exact progress in `docs/AI_INTEGRATION_LOG.md`; leave the task `IN_PROGRESS`. The next Continue must resume from that checkpoint, not restart.
- When a specialist has no eligible task, inspect the highest-priority incomplete roadmap gate and add a unique, scoped task. Do not go idle while meaningful work remains.
- Do not claim lower roadmap phases until the conversation-first development gate is met, except direct prerequisites or blocking security/integration fixes.
- Each agent owns at most one active task. The task board is advisory, not an atomic lock; re-fetch the latest board and SHA immediately before every write.
- Never use duplicate task IDs. If a task is already complete, record evidence and select the next eligible task rather than repeating it.
- A task is `DONE` only after its commit is verified on remote `main` and acceptance evidence is recorded.

## Task update format

Append a concise UTC entry to `docs/AI_INTEGRATION_LOG.md` for every claim, checkpoint, handoff, blocker and completion. Include task ID, agent ID, status, changed paths, exact tests and commit SHA when available.

## Adding tasks

Use unique IDs `MIA-[AREA]-NN`. Every task must state priority, dependencies, allowed paths/ownership boundary, acceptance criteria, test expectations and security/privacy constraints. Avoid vague tasks such as “improve AI”.
