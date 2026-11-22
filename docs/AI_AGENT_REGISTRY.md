# Mia AI agent registry

Six specialist worker slots are available: A1–A6. The coordinator is not a worker.
This registry is a live-session directory, not a historical record. Previous session registrations have been reset. Each newly launched agent must register itself before claiming a task.

| Agent ID | Specialization | Session / model label | Current task | Status | Last heartbeat (UTC) |
|---|---|---|---|---|---|
| A1 | Brain, identity & memory | Anthropic Claude Sonnet 5.5 (claude.ai chat, second concurrent session, 2026-11-21) | — | IDLE | 2026-11-22T16:00Z |
| A2 | AI Gateway & model routing | Anthropic Claude Sonnet 5.5 (claude.ai chat session, 2026-11-22) | MIA-OBS-03 | ACTIVE | 2026-11-22T17:06Z |
| A3 | Reasoning quality & judge models | Anthropic Claude Sonnet 5.5 (claude.ai chat session, 2026-11-21) | MIA-OBS-07 | ACTIVE | 2026-11-22T17:06Z |
| A4 | Voice & wake word | Anthropic Claude Sonnet 5.5 (claude.ai chat session, fresh, 2026-11-22) | — | IDLE | 2026-11-22T10:40Z |
| A5 | Code Graph & governed self-development | Anthropic Claude Sonnet 5.5 (claude.ai chat session, fresh, 2026-11-22) | MIA-EVOL-02 | ACTIVE | 2026-11-21T20:33Z |
| A6 | Experience, UI & interaction | Anthropic Claude Sonnet 5.5 (claude.ai chat session, fresh, 2026-11-21) | MIA-HOLO-05 | ACTIVE | 2026-11-22T17:15Z |
| A7 | Multilingual language layer | Anthropic Claude Sonnet 5.5 (claude.ai chat session, fresh, 2026-11-22; owner-approved seventh role) | — | IDLE | 2026-11-22T18:10Z |

## Fresh-session registration

Every newly launched agent must:
1. Read the master prompt, specialization guide, task board, task catalog, Continue workflow and current integration-log checkpoints.
2. Select an available worker slot matching its specialization. If that slot belongs to a live session, do not overwrite it. Choose another unregistered slot only if its specialization is a reasonable fit; otherwise report the conflict.
3. Register only its own row with a non-secret session/model label, task initially —, status ACTIVE and current UTC heartbeat. Fetch the latest registry and SHA immediately before writing.
4. Claim exactly one eligible OPEN task from the 55-task M1–M6 catalog/board, respecting priority, dependencies, file ownership and milestone handoff rules. Update the task board and registry; refresh both files immediately before writes.
5. If no task is eligible, report the exact dependency/blocker. Do not fabricate work or mark another agent's task complete.

## Session lifecycle

- Heartbeats are informational, not locks. A stale timestamp does not prove a session is running.
- On pause/usage limit, save a checkpoint to docs/AI_INTEGRATION_LOG.md, keep the task IN_PROGRESS, and set the registry row OFFLINE with task retained.
- On task completion, verify the remote main commit, update the task board and log, then set the row IDLE and clear the task before claiming another.
- A newly launched session must register fresh and inspect any previous task checkpoint before resuming it.
- Never claim an agent is running in the background.

## History policy

The registry intentionally contains no previous-session history. Preserve docs/AI_INTEGRATION_LOG.md and Git commit history as the durable development audit trail; do not erase implementation evidence, completed task records or owner checkpoints when resetting live registrations.
