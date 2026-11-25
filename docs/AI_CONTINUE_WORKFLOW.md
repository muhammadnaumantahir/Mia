# Mia — persistent Continue workflow

This document defines how every specialist session registers, selects work and resumes after chat, context or usage limits.

## Fresh session startup

1. Refresh remote main; read AGENTS.md, roadmap, status, development system, this workflow, task board, 55-task catalog, registry, integration log and specialization prompts.
2. A newly launched session must register itself in an available A1–A6 slot. Do not inherit an old session identity or heartbeat. Update only your own registry row after fetching its latest SHA.
3. Select exactly one highest-priority dependency-ready OPEN task matching your specialization. Check task ownership, integration-log checkpoints and owned paths before claiming. Milestone number alone is not a dependency. Update the task board and registry using fresh content/SHA.
4. Never claim work already owned by another live session. A stale heartbeat alone does not establish that a session is live; inspect its checkpoint and recent repository evidence. If uncertain, avoid duplicate claims and report the conflict.

## Continue behavior

A Continue request is a development instruction, not a status-only report.

1. Refresh main, task board, registry and integration log.
2. If this is the same registered session, resume its own IN_PROGRESS task first.
3. If this is a newly launched session, register afresh and inspect any old task checkpoint. Resume an abandoned task only after confirming no live session owns it; otherwise select a fresh eligible OPEN task.
4. Inspect current source and tests against acceptance criteria. Do not repeat completed work.
5. Implement a complete, bounded vertical slice: source, regression tests, docs and required Code Graph impact review.
6. Run relevant local checks where available. Record exact commands and outcomes. CI is optional and never a development gate.
7. Commit directly to main, verify the remote commit, then update task board, registry, integration log and status.
8. After completing a task, immediately claim the next highest-priority eligible task in the same specialization and continue if the turn budget permits.
9. Before stopping for a context/usage limit, save a durable checkpoint in the integration log: current task, completed work, changed paths, latest commit, tests run/not run, remaining acceptance criteria, blockers and exact next action. Leave incomplete implementation IN_PROGRESS; set the registry row OFFLINE with task retained. Never mark partial implementation DONE. If the owner explicitly defers test execution, completed implementation with regression tests added may be marked DONE while test execution is separately recorded as OWNER_VERIFICATION_REQUIRED; never claim those tests passed.

## Task completion definition

A task is complete only when:
- every acceptance criterion is satisfied or an explicitly separable remainder has its own new task;
- implementation and regression tests are committed;
- local verification is truthfully recorded;
- the resulting commit is confirmed on remote main;
- the task board and integration log contain evidence and the commit SHA;
- the Code Graph has been refreshed locally where a checkout/runtime is available, or the refresh limitation is explicitly recorded.

Documentation-only progress, a plan, a generated patch, an attempted push or a claimed agent heartbeat is not task completion.

## Queue discipline

- The task board is the single source of truth for task ownership and status; the roadmap determines priority; the 55-task catalog defines scope and acceptance.
- Keep one active task per agent. Do not create duplicate task IDs. If new work is discovered, create a unique ID with priority, dependencies, owned paths, acceptance criteria and test expectations.
- Choose the highest-priority eligible OPEN task. Do not cherry-pick lower-priority work.
- Milestones are roadmap/release ordering, not a global development lock. Work across milestones may proceed in parallel whenever the task's explicit dependencies, security invariants and required interfaces are satisfied. Do not block an independent task because an unrelated earlier-milestone task is OPEN, PARTIAL or awaiting owner-only checks. Explicit dependencies and final acceptance gates remain mandatory.
- A task may be marked BLOCKED only when a named, explicit dependency or security invariant prevents all meaningful progress. State the exact dependency, its current status, evidence and next unblock condition. If only one slice is blocked, split it and continue independent slices.
- Keep tasks small enough to finish in one or a few Continue cycles. Split broad tasks into ordered tasks with explicit dependencies.
- A session at its provider usage limit cannot run in the background. Repository checkpoints preserve work but do not launch agents.

## Shared-main safety

- All changes go directly to main; no branches, PRs, force pushes or history rewrites.
- GitHub task claims are advisory, not atomic locks. Re-fetch the latest board/registry and file SHA immediately before every shared-file write.
- Serialize writes to shared files and all remote pushes. Agents may research or edit disjoint owned source files in parallel.
- If main advances during work, reconcile the new commit before writing. Never overwrite a concurrent change with stale full-file content.
- If safe integration is not possible, stop and checkpoint the blocker rather than claiming a push succeeded.

## Owner-facing reporting

After each Continue cycle, report: task ID, status, meaningful implementation changes, commit SHA/URL if committed, tests actually run, tests not run, remaining work and the next queued task. Report progress from repository evidence only.
