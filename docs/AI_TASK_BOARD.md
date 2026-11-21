# Mia multi-agent task board

**Worker slots:** A1–A6  
**Coordinator:** master-prompt role (not a worker slot)  
**Branch policy:** direct commits to `main` only  
**Last initialized:** 2026-11-21

This is the shared queue. Always refresh it from `main` before claiming or changing a task. The roadmap and acceptance criteria in `docs/ROADMAP.md` are authoritative.

## Active task queue

| Task | Priority | Suggested owner | Status | Scope / acceptance |
|---|---:|---|---|---|
| MIA-BASE-01 | P0 | A5 | DONE (A5, commit bb01d32 on main, 2026-11-21) | Inspect Code Graph freshness workflow and identify exact gap between watcher/full rebuild and incremental indexing. Deliver a source-backed gap report and safe implementation plan; do not invent graph edges. |
| MIA-BASE-02 | P0 | A5 | OPEN | Implement true per-file incremental Code Graph indexing per the plan in docs/code-graph/README.md (per-file fact cache + global resolution pass); must include a test that incremental output equals a clean full rebuild after add/modify/remove/rename. Paths: src/mia/codegraph/**, scripts/update_code_graph.py, tests/test_code_graph*.py. |
| MIA-BASE-03 | P0 | UNASSIGNED | OPEN | tests/test_browser_automation.py::test_browser_api_end_to_end fails with KeyError 'verified' on current main (also with A5 changes stashed). Diagnose and fix the application defect; do not weaken the test. Paths: browser/computer adapters and that test only. |
| MIA-BRAIN-01 | P1 | A1 | DONE (A1, commits 5887f86 + e9b15b7 on main, 2026-11-21) | Audit cross-session/project/provider continuity end-to-end. Identify concrete missing behavior from code/tests and implement the next bounded regression-backed improvement. |
| MIA-BRAIN-02 | P1 | A1 | DONE (A1, commit f61cdd3 on main, 2026-11-21) | Provider-switch recall slice: tests/test_brain_continuity.py covers cross-session memory recall after a provider switch, remote-provider privacy withholding after a switch (mutation-checked) and owner-correction precedence. Project scoping split to MIA-BRAIN-03. |
| MIA-BRAIN-03 | P1 | A1 | IN_PROGRESS (A1, claimed 2026-11-21T08:40Z; owner approved project-scoped continuity: opt-in, visible, temporary-chat isolation preserved) | Conversations carry no project association (none found in src/mia/brain/context.py); recall is 'latest other persistent chat' plus structured project records. Decide whether project-scoped continuity should exist and be opt-in/visible before designing it. Temporary-chat isolation must be preserved. Paths: src/mia/brain/**, src/mia/memory/**, tests. |
| MIA-GW-01 | P1 | A2 | DONE (A2, commit de4f185 + fix 84d43a7 on main, 2026-11-21T07:51Z) | Trace verifier confidence and gateway routing; determine whether quality-based escalation is actually wired. Implement a bounded policy only if a real gap exists, with fake-provider tests and spend/privacy/latency limits. |
| MIA-EVAL-01 | P1 | A3 | DONE (A3, commit 186f5d0 on main, 2026-11-21) | Reasoning-quality scenarios expanded 10→23 (memory conflicts via real supersede, real tool failures, en/roman-urdu/ur continuity, uncertainty); verifier failure-report fix; summarize() labelled live_model_quality=not_measured. |
| MIA-EVAL-02 | P1 | A3 | IN_PROGRESS (A3, claimed 2026-11-21T07:54Z) | Opt-in parallel judge orchestration in new `src/mia/brain/judges.py` + tests: strict concurrency cap, per-judge timeout and cancellation, privacy eligibility (local-only unless owner allows), cost ceiling (unknown price fail-closed), deterministic fallback, skipped for simple turns; judges never override permission policy and consensus is not truth. Fake providers only; no gateway/orchestrator edits without A2/A1 agreement. |
| MIA-VOICE-01 | P1 | A4 | DONE (A4, commit ee0daa0 on main, 2026-11-21) | Audit offline wake-word options and current voice session lifecycle. Produce a tested, privacy-first integration plan or implement a small prerequisite with tests; never enable background microphone capture by default. **Delivered:** `src/mia/voice/wake.py` (detector contract, opt-in/time-bounded/immediately-stoppable session, false-activation guards, openWakeWord adapter), truthful `offline_wake_word` in `/api/voice/status`, audit + plan in `docs/voice/WAKE_WORD_PLAN.md`. **Not delivered:** audio transport, UI, real-speech detection (OWNER_VERIFICATION_REQUIRED). |
| MIA-VOICE-02 | P1 | A4 | IN_PROGRESS (A4, claimed 2026-11-21T07:44Z) | Loopback-only audio transport for the offline wake word (WebSocket, 16 kHz int16, loopback client + same-origin check, one session, socket close or `stop` message calls `WakeWordSession.stop()`). Paths: `src/mia/voice/wake_transport.py`, `tests/test_voice_wake_transport.py`, `docs/voice/**`. Wiring into `src/mia/observatory/api.py` is one `register_wake_routes(...)` call and is **held back until MIA-BRAIN-04 lands**. |
| MIA-BRAIN-04 | P1 | A1 | OPEN | `src/mia/brain/self_description.py` treats any route whose path contains "wake" as a registered wake word (`wake_word_registered`, ~lines 46-49). Derive wake capability from operational evidence instead (`mia.voice.wake.offline_wake_word_status(...)["operational"]`), so a registered route is never described as a working wake word. Required before any `/api/voice/wake/*` route is wired. Paths: `src/mia/brain/self_description.py`, `tests/test_self_description.py`, `tests/test_conversation_eval.py`. |
| MIA-UX-02 | P1 | A6 | OPEN (blocked on MIA-VOICE-02 + MIA-BRAIN-04) | Opt-in offline wake-word UI in `assistant.html`: persistent Listening badge, always-visible Stop, notice of the 4 h auto-stop and model license, AudioWorklet capture (16 kHz int16 frames of 1280 samples) to the transport, hand-off to the existing voice session on activation. Contract and controls: `docs/voice/WAKE_WORD_PLAN.md` sections 4 and 6. |
| MIA-VOICE-03 | P1 | A4 | OPEN (needs `assistant.html`; A6 currently owns it - coordinate before editing) | Urdu-script and Roman Urdu voice commands: `END_PHRASES` / `STOP_SPEAKING_PHRASES` (`assistant.html` ~lines 1085-1086) are English-only. Add language-aware phrases with the existing short-utterance guard (a long question containing the words stays a normal prompt) and tests for English, Roman Urdu and Urdu script. |
| MIA-UX-01 | P1 | A6 | DONE (A6, commit 5ba0e2f on main, verified remote 2026-11-21T07:50Z) | Audit existing Assistant/chat UX against conversation-first acceptance: streaming, interruption, listening state, errors, context continuity and accessibility. Implement one isolated, testable improvement without starting the later 3D phase. |
| MIA-UX-03 | P1 | A6 | IN_PROGRESS (A6, claimed 2026-11-21T07:50Z) | Conversation error/recovery UX in `assistant.html`: replace the unconditional "Check that Ollama…" hint with an error-aware message (permission, budget, provider-unavailable, network), add a Retry action for a failed reply, and assess whether `#messages` `aria-live` should announce streamed tokens. Tests: contract + Node harness. No changes to gateway/Brain contracts; error classes are read from existing HTTP status/detail only. |
| MIA-FIX-01 | P1 | unassigned | OPEN | `tests/test_browser_automation.py::test_browser_api_end_to_end` fails locally (A6, 2026-11-21): `POST /api/browser/fill` returns 409 "needs an active owner grant; grant browser.fill" since the shared permission-policy change; the test never grants it. Decide whether the test should grant `browser.fill` (likely) or the route default is wrong — do not weaken the permission check. Owner of computer/permission code to confirm. A1 reported a clean full suite, so also check whether this is environment-dependent. |
| MIA-GW-02 | P1 | A2 | IN_PROGRESS (A2, claimed 2026-11-21T07:51Z) | Follow-up from MIA-GW-01: expose escalation settings (read-only first) in the gateway dashboard/API, add streaming-turn handling decision (document or implement), and add per-turn escalation counters to the usage ledger. Paths: src/mia/gateway/**, gateway API routes, matching tests. Must keep escalation opt-in and fail-closed. |

## Rules for task selection

- These are initial work packets, not guaranteed assignments. The first successful registry/task-board claim determines ownership.
- Before implementation, each agent must inspect current source and the latest roadmap. If the task is already complete, update the board with evidence and select the next unfinished task in the same priority.
- Do not duplicate work. Search task IDs, recent commits and integration log first.
- Split tasks into smaller IDs if they touch the same files or require separate ownership. The coordinator must add the split before agents claim it.
- Tasks in lower roadmap phases must remain deferred until the earlier phase's development gate is met.
- A task may be marked `DONE` only after a remote commit SHA and acceptance evidence are recorded.

## Task update format

Append a concise entry to `docs/AI_INTEGRATION_LOG.md` for each claim, handoff, blocker and completion. Include timestamp (UTC), task ID, agent ID, status, changed paths and commit SHA when available.

## Adding tasks

Use IDs `MIA-[AREA]-NN`. Every task must state priority, dependency, allowed paths or ownership boundary, acceptance criteria, test expectations and any security/privacy constraints. Do not add vague tasks such as “improve AI”.
