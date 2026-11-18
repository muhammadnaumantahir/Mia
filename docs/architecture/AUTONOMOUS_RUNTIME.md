# Mia autonomous task runtime

## Current task lifecycle

Every task is stored with an ID, title, goal, source, priority, current status and structured metadata. State transitions are appended to a separate event ledger.

Allowed sources:
- interactive request;
- scheduled trigger;
- self-assessment;
- internal test;
- benchmark;
- research discovery;
- authorized environment observation;
- experience analysis.

Task states:
- proposed;
- approved;
- running;
- waiting;
- completed;
- failed;
- rejected;
- cancelled.

Transitions are checked centrally. Completed, rejected and cancelled tasks are terminal. Failed work must be explicitly re-approved before another attempt.

## Evidence-backed workload discovery

The current WorkloadDiscovery component accepts findings from self-assessment, internal tests, benchmarks, research, environment observations and experience analysis. It writes proposed tasks with an evidence ID, risk label and a stable deduplication key. Repeated observations map to the existing task rather than flooding the queue. Discovery never approves a task automatically.

The local API exposes task proposals, task history, approval, rejection and cancellation. The Evolution Observatory dashboard includes a task-control panel. There is no autonomous scheduler or planner connected yet.

## Autonomy gate

The dispatcher is a single-cycle primitive and does not start a background worker. It only claims approved tasks when all of these checks pass:
- autonomy is explicitly enabled;
- emergency stop is not active;
- runtime load metrics are available;
- CPU and memory are below configured ceilings.

Unknown resource usage means pause, not assume the machine is idle. The default policy is disabled.

## Current boundary

The task executor is injected as a callback. The current HTTP API does not expose automatic dispatch. Mia does not yet autonomously plan tool calls, run arbitrary shell commands, create schedules or execute generated code. Those require the permissioned tool catalog, runtime monitor, sandbox and task cancellation/recovery paths.

## Planned next integrations

1. Deterministic task planner with explicit goal, constraints, expected effects and acceptance tests.
2. Scheduler for owner-approved recurring maintenance and internal health checks.
3. Workload discovery agents that produce proposed tasks with evidence, never execute on discovery alone.
4. Tool dispatcher that checks SPS-02 Governance and uses only registered contracts.
5. Runtime telemetry adapter that supplies real CPU/RAM readings on Windows and Linux.
6. Resume, cancellation, timeout and retry policies for long-running work.

## Autonomy experiment rule

A self-assessment can propose work without user input, but a proposal is not permission to mutate the computer. Low-risk internal read-only tests may later be auto-approved under a narrow owner policy. File writes, application launches, browser interactions, external communication, system changes and capability activation retain their own permission and approval requirements.


## Current system-resource tools

The optional `computer` extra installs psutil. The local API and model tool registry can read CPU percentage, RAM percentage/availability and a bounded process list containing only PID, process name and status. They do not expose command lines, process environments, executable paths or credentials. This is observational only; process termination and process launching outside the curated application allowlist are not implemented.

The autonomy dispatcher still does not run a background loop. Real resource snapshots are available to the control plane, but no automatic tasks are dispatched yet.
