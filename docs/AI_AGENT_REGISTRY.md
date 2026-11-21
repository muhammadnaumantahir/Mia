# Mia AI agent registry

Six specialist worker slots are available. Agents register their own sessions before claiming work. The coordinator is a separate orchestration role and is not a seventh worker.

| Agent ID | Specialization | Session / model label | Current task | Status | Last heartbeat (UTC) |
|---|---|---|---|---|---|
| A1 | Brain, identity & memory | Claude (Anthropic), claude.ai chat session | — | IDLE | 2026-11-21T15:50Z |
| A2 | AI Gateway & model routing | Claude (Anthropic), claude.ai chat session | — | IDLE | 2026-11-21T15:34Z |
| A3 | Reasoning quality & judge models | Claude (Anthropic), claude.ai chat session | — | BLOCKED (provider usage limit) | 2026-11-21T07:54Z |
| A4 | Voice & wake word | Claude (Anthropic), claude.ai chat session | MIA-UX-04 | ACTIVE | 2026-11-21T16:40Z |
| A5 | Code Graph & governed self-development | Claude (Anthropic), claude.ai chat session | MIA-BASE-05 | ACTIVE | 2026-11-21T15:45Z |
| A6 | Experience, UI & interaction | Claude (Anthropic), claude.ai chat session | - | IDLE (no unblocked A6 task) | 2026-11-21T15:39Z |

## Registration protocol

An agent updates only its own row with:
- a non-secret session label identifying the AI tool/vendor/model (do not expose tokens or account identifiers);
- task ID it has claimed;
- status `ACTIVE`, `IDLE` or `BLOCKED`;
- UTC heartbeat timestamp.

Before registering or updating, fetch the latest registry and SHA. Do not overwrite another agent's row from a stale copy. If the update conflicts, re-fetch and merge the changes.

## Lease and handoff

A registry heartbeat is informational, not a lock. A task board claim and verified remote commit are the source of truth. When a task is complete or a session pauses, clear its current task and set status to `IDLE` or `OFFLINE`. A coordinator may reassign an abandoned task only after checking the integration log and remote main history.

## Usage-limit behavior

A provider usage limit means the session is temporarily unavailable; it does not mean its task is complete or that it is working in the background. Preserve the active task and checkpoint in the integration log. When the owner can send another Continue to that same session, it must resume the task from the checkpoint. Do not register a second identity for the same continuing session.
