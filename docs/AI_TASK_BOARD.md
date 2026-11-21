# Mia multi-agent task board

**Worker slots:** A1–A6  
**Coordinator:** master-prompt role (not a worker slot)  
**Branch policy:** direct commits to `main` only  
**Last initialized:** 2026-11-21

This is the shared queue. Always refresh it from `main` before claiming or changing a task. The roadmap and acceptance criteria in `docs/ROADMAP.md` are authoritative.

## Active task queue

| Task | Priority | Suggested owner | Status | Scope / acceptance |
|---|---:|---|---|---|
| MIA-BASE-01 | P0 | A5 | IN_PROGRESS (A5, claimed 2026-11-21T07:40Z) | Inspect Code Graph freshness workflow and identify exact gap between watcher/full rebuild and incremental indexing. Deliver a source-backed gap report and safe implementation plan; do not invent graph edges. |
| MIA-BRAIN-01 | P1 | A1 | IN_PROGRESS (A1, claimed 2026-11-21T07:27Z) | Audit cross-session/project/provider continuity end-to-end. Identify concrete missing behavior from code/tests and implement the next bounded regression-backed improvement. |
| MIA-GW-01 | P1 | A2 | IN_PROGRESS (A2, claimed 2026-11-21T07:26Z) | Trace verifier confidence and gateway routing; determine whether quality-based escalation is actually wired. Implement a bounded policy only if a real gap exists, with fake-provider tests and spend/privacy/latency limits. |
| MIA-EVAL-01 | P1 | A3 | IN_PROGRESS (A3, claimed 2026-11-21T07:28Z) | Expand repeatable reasoning-quality scenarios for memory conflicts, failed tools/providers, multilingual follow-ups and calibrated uncertainty. Ensure metrics are evidence-based and do not claim live model quality from mocks. |
| MIA-VOICE-01 | P1 | A4 | IN_PROGRESS (A4, claimed 2026-11-21T07:29Z) | Audit offline wake-word options and current voice session lifecycle. Produce a tested, privacy-first integration plan or implement a small prerequisite with tests; never enable background microphone capture by default. |
| MIA-UX-01 | P1 | A6 | OPEN | Audit existing Assistant/chat UX against conversation-first acceptance: streaming, interruption, listening state, errors, context continuity and accessibility. Implement one isolated, testable improvement without starting the later 3D phase. |

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
