# Mia — persistent Continue workflow

This document defines what every specialist session must do whenever the owner says **Continue**. It is the operating contract for resuming work after chat, context or usage limits.

## What Continue means

A Continue request is a development instruction, not a request for a status-only report.

1. Refresh `main`; read `AGENTS.md`, `docs/ROADMAP.md`, `docs/STATUS.md`, this file, the task board, registry and integration log.
2. Resume the agent's own existing `IN_PROGRESS` task if it is still assigned to that agent. Do not abandon partially completed work just because the session or usage limit interrupted it.
3. If no task is owned, choose the highest-priority eligible task for that specialization. Never take another active agent's task.
4. Inspect current source and tests; compare them with the task's acceptance criteria. Do not repeat already completed work.
5. Implement a complete, bounded vertical slice: source, regression tests, docs and required Code Graph impact review.
6. Run relevant local checks where available. Record exact commands and outcomes. CI is optional and must never be a development gate.
7. Commit directly to `main`, verify the remote commit, then update task board, registry, integration log and status.
8. After completing a task, immediately claim the next highest-priority eligible task in the same roadmap phase and continue if the current turn has sufficient budget.
9. Before stopping for a context/usage limit, save a durable checkpoint in the integration log: current task, completed work, changed paths, latest commit, tests run/not run, remaining acceptance criteria, blockers and exact next action. Leave the task `IN_PROGRESS`; never mark partial work `DONE`.

## Task completion definition

A task is complete only when:
- every acceptance criterion is satisfied or an explicitly separable remainder has its own new task;
- implementation and regression tests are committed;
- local verification is truthfully recorded;
- the resulting commit is confirmed on remote `main`;
- the task board and integration log contain evidence and the commit SHA;
- the Code Graph has been refreshed locally where a checkout/runtime is available, or the refresh limitation is explicitly recorded.

Documentation-only progress, a plan, a generated patch, an attempted push or a claimed agent heartbeat is not task completion.

## Queue discipline

- The task board is the single source of truth for task ownership and status. The roadmap determines priority.
- Keep one active task per agent. Do not create duplicate task IDs. If new work is discovered, create a unique ID with priority, dependencies, owned paths, acceptance criteria and test expectations.
- When the specialist queue is empty, inspect the current highest-priority roadmap gate and create a concrete next task for that specialization rather than going idle.
- Do not start lower-priority milestones while the conversation-first gate remains open, except direct prerequisites and blocking security/integration fixes.
- A task blocked by another agent must state the exact dependency and may be split at a stable interface. Do independent work that does not touch the blocked files.
- Keep tasks small enough to finish in one or a few Continue cycles. Split broad work into ordered sub-tasks with explicit dependencies.
- A session that has reached its provider usage limit cannot run in the background. Resume it when the provider allows another turn; repository files preserve the checkpoint but do not launch agents.

## Shared-main safety

- All changes go directly to `main`; no branches, PRs, force pushes or history rewrites.
- GitHub task claims are advisory, not atomic locks. Re-fetch the latest board/registry and file SHA immediately before every shared-file write.
- Serialize writes to shared files and all remote pushes. Agents may research or edit disjoint owned source files in parallel.
- If main advances during work, reconcile the new commit before writing. Never overwrite a concurrent change with stale full-file content.
- If safe integration is not possible, stop and checkpoint the blocker rather than claiming a push succeeded.

## Owner-facing reporting

After each Continue cycle, report: task ID, status, meaningful implementation changes, commit SHA/URL if committed, tests actually run, tests not run, remaining work and the next queued task. Report progress from repository evidence only. Do not inflate milestone percentages based on task counts.
