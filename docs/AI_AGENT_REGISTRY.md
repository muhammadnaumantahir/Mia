# Mia AI agent registry

Six specialist worker slots are available. Agents must register themselves before claiming work. The coordinator is a separate orchestration role and is not counted as a worker.

| Agent ID | Specialization | Session / model label | Current task | Status | Last heartbeat (UTC) |
|---|---|---|---|---|---|
| A1 | Brain, identity & memory | UNREGISTERED | — | OFFLINE | — |
| A2 | AI Gateway & model routing | UNREGISTERED | — | OFFLINE | — |
| A3 | Reasoning quality & judge models | UNREGISTERED | — | OFFLINE | — |
| A4 | Voice & wake word | UNREGISTERED | — | OFFLINE | — |
| A5 | Code Graph & governed self-development | UNREGISTERED | — | OFFLINE | — |
| A6 | Experience, UI & interaction | UNREGISTERED | — | OFFLINE | — |

## Registration protocol

An agent updates only its own row with:
- a non-secret session label identifying the AI tool/vendor/model (do not expose tokens or account identifiers);
- task ID it has claimed;
- status `ACTIVE`, `IDLE` or `BLOCKED`;
- UTC heartbeat timestamp.

Before registering or updating, fetch the latest registry and SHA. Do not overwrite another agent's row from a stale copy. If the update conflicts, re-fetch and merge the changes.

## Lease and handoff

A registry heartbeat is informational, not a lock. Task ownership is controlled by the task board and confirmed by remote commits. On completion or pause, clear the current task and set status to `IDLE` or `OFFLINE`. A coordinator may reassign an abandoned task only after checking the integration log and remote main history.
