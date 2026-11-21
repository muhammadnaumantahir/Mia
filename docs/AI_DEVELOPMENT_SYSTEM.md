# Mia multi-agent development system

**Worker count:** 6 specialist agents  
**Coordinator:** one orchestration role defined by the master prompt; it is not a seventh worker.  
**Git policy:** all development lands directly on `main`; no feature branches, pull requests or CI dependency.

## Purpose

Run six independent AI coding agents against one shared roadmap and task board. Parallelize repository inspection, design, tests and implementation only where file ownership and dependencies allow. The system must increase throughput without sacrificing integration safety, the conversation-first roadmap, privacy or evidence quality.

## Six specialist agents

| ID | Specialization | Owns |
|---|---|---|
| A1 | Brain, identity & memory | Conversation pipeline, self-description, context, memory retrieval/capture, continuity |
| A2 | AI Gateway & model routing | Provider contracts/adapters, model registry, routing, escalation policy, fallback, budgets and usage |
| A3 | Reasoning quality & judge models | Evaluation scenarios, verifier, benchmarks, bounded parallel judges, quality evidence |
| A4 | Voice & wake word | STT/TTS, streaming turn-taking, interruption, language support, wake-word lifecycle and privacy |
| A5 | Code Graph & governed self-development | Code Graph indexing/impact analysis, capability evolution, sandboxing, lineage and rollback |
| A6 | Experience, UI & interaction | Assistant/chat UX, accessibility, conversation UI integration; 3D and later digital-life screens only when roadmap gates permit |

A specialist may help another domain only when its primary queue is empty or a coordinator-approved dependency requires it. It must not take over another agent's active task.

## Coordinator responsibilities

The coordinator:
- maintains the canonical task board and resolves task overlap;
- assigns work by priority, dependencies, risk and file ownership;
- makes architecture decisions and resolves cross-agent interface questions;
- serializes task claims and Git writes when multiple agents share the same remote branch;
- checks that each completed task has implementation evidence, tests or a clear verification limitation;
- reviews integration conflicts and keeps `docs/STATUS.md` truthful;
- never marks a task complete merely because documentation was written.

The coordinator does not count as one of the six specialist workers.

## Shared workflow

1. Read `AGENTS.md`, `docs/ROADMAP.md`, `docs/STATUS.md`, this document, the task board and the integration log.
2. Inspect the current `main` head and the actual files before planning. Do not trust cached repository state.
3. Register using the agent registry. Use a stable agent ID (A1–A6), tool/vendor/model label, session identifier, timestamp, task ID and current status. Do not publish API keys or personal secrets.
4. Claim exactly one task by changing its status from `OPEN` to `IN_PROGRESS`, adding the agent ID and a short lease note. Re-read the latest task board immediately before writing. If another agent has claimed it, choose another eligible task.
5. Work only within the task's scope and declared file ownership. Before editing, inspect the latest version of every target file and relevant tests.
6. If a task needs files owned by another active task, coordinate through the board/log and wait or split the work at a stable interface. Do not concurrently edit the same file.
7. Implement the smallest complete vertical slice. Add/adjust tests, docs and source/license records where applicable.
8. Verify locally where the environment permits. Record exact commands and outcomes. If tests cannot be run, say so; never imply they passed.
9. Fetch the latest `main`, integrate any intervening commits without overwriting them, and re-check the diff. Never force-push or rewrite history.
10. Commit directly to `main`, push, then verify the resulting commit is on remote `main`.
11. Update the task board, agent registry and integration log with the commit SHA and verification evidence. Mark `DONE` only after the remote commit is confirmed.
12. Re-read the roadmap and claim the next highest-priority eligible task. Do not wait for the owner to say “continue”.

## Main-only Git safety

- All work is committed directly to `main`. No branches, PRs, forks or merge queues.
- Parallelize analysis and coding only on disjoint files or clearly separated interfaces. Serialize pushes and all shared-file writes.
- Never assume a task claim is atomic. GitHub's ordinary file-contents update is not a transactional lock. For shared-board updates, re-fetch the current file and SHA immediately before each write; if the update conflicts or the file has changed, re-fetch and reconcile rather than retrying stale content.
- Do not use force push, reset, history rewriting, blind overwrite or a stale full-file replacement.
- If the remote head changes during work, fetch the new head and integrate it before pushing. If the tool cannot safely reconcile, stop the push, record the blocker and ask the coordinator to serialize the integration.
- A task lease is advisory, not a technical lock. The registry and task board are coordination records; the remote commit history is the source of truth.
- Do not commit generated models, databases, runtime data, credentials, machine-specific paths or large build artifacts.

## Priority and dependency policy

The canonical execution order in `docs/ROADMAP.md` always wins:
1. Priority 0 integration baseline and Code Graph prerequisite.
2. Priority 1 conversation-first Brain, memory, independent gateway, reasoning/evaluation, multilingual voice and wake-word.
3. Priority 2 full 3D holographic interaction.
4. Priority 3 personal digital life.
5. Priority 4 computer intelligence/control.
6. Priority 5 governed self-programming and model evolution.
7. Priority 6 evolution measurement.

Use the task-board milestone handoff rule: a later milestone can begin parallel implementation when every implementation task in the preceding milestone is DONE or already IN_PROGRESS under another agent. Any earlier OPEN task must be claimed first; an agent must not abandon its own earlier task. Explicit dependencies remain mandatory, and milestone completion still requires its full exit criteria. A6 may start dependency-ready M2 tasks under this rule. A5 maintains the Code Graph throughout all phases and must keep governed self-development behind its explicit dependencies and safety gates.

## Conflict and escalation rules

- If two agents identify the same task, the first valid board claim wins; the other agent must select another task.
- If an agent discovers a shared contract change, it records a short proposal and waits for coordinator approval before changing the contract.
- If a task exceeds its lease, the coordinator may mark it `BLOCKED` or reassign it after checking the remote commit history. Do not silently take over.
- If tests reveal a defect outside the task, create a new task rather than broadening scope without coordination, unless the defect blocks safe integration.
- Security, data loss, secret exposure, broken main or corrupted user data are stop-and-escalate conditions.
- Never weaken governance, privacy, permission checks or tests to accelerate delivery.

## Task status vocabulary

- `OPEN`: available to claim.
- `IN_PROGRESS`: actively claimed by one agent.
- `BLOCKED`: specific dependency or environment blocker recorded.
- `REVIEW_REQUIRED`: implementation committed; coordinator integration review remains.
- `DONE`: remote main commit verified and acceptance evidence recorded.
- `DEFERRED`: intentionally postponed by roadmap priority or owner decision.

## Completion record

Every completed task must include:
- task ID and specialist agent ID;
- concise implementation summary;
- touched paths;
- commit SHA and remote URL;
- tests/commands actually run and their results;
- known limitations and owner-only verification still required;
- whether Code Graph refresh/impact analysis was performed or why it was unavailable.

## Important limitation

Six AI agents do not automatically mean six safe simultaneous pushes. The best throughput comes from parallel analysis and disjoint implementation, followed by serialized direct-main commits. If the connected GitHub tool or agent environment cannot safely coordinate concurrent pushes, agents must serialize the final write rather than risk losing work.
