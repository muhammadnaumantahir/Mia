# Autonomous runtime

Mia's task dispatcher is deliberately opt-in. Importing the application or
creating an AutonomyRuntime never starts a worker. Background dispatch starts
only when MIA_AUTONOMY_ENABLED=true and the emergency stop is not active.
The default in .env.example is false.

## Runtime contract

- One background worker; dispatch cycles do not overlap.
- Every cycle samples local CPU and RAM before any task is claimed.
- Missing or invalid telemetry pauses the cycle and leaves tasks untouched.
- A task must already be in the owner-approved approved state.
- The automatic claim filter validates the stored metadata.execution contract
  before claiming a task. Unsupported tasks remain approved and queued.
- The current automatic allowlist is deliberately narrow:
  system.read_resources, system.list_processes and applications.list.
  These are registered read-only system/application inventory tools.
- Filesystem reads, browser navigation/search, desktop inspection, application
  launch, text entry, previews and all mutations remain outside automatic
  dispatch. They use their separate manual/approval-bound flows.
- Free-form task titles and goals are never interpreted as commands.
- The executor uses TaskToolExecutor; the tool registry validates its schema
  and read-only mode again immediately before invocation.
- Each cycle dispatches at most one task. The default interval is 30 seconds;
  MIA_AUTONOMY_INTERVAL_SECONDS can set an interval from 1 to 3600 seconds.
- Task Desk exposes pause/resume controls. API routes are
  POST /api/autonomy/pause and POST /api/autonomy/resume. The owner pause flag
  is stored in the local task database and survives Mia restarts. A saved pause
  prevents startup dispatch even when MIA_AUTONOMY_ENABLED=true. Resume is
  rejected unless environment policy enables autonomy and emergency stop is off.
- Completed/failed background runs append a minimal Observatory event. Owner
  pause/resume actions also append explicit Observatory audit events. Full task
  lifecycle transitions remain in the task ledger.
- pause() prevents future cycles but does not kill an in-flight call.
  Shutdown waits up to 35 seconds and records a timeout event if needed.
- Emergency stop blocks new claims immediately, but is not an OS-level kill
  switch for an in-flight native operation.

## Important boundaries

The background worker does not approve proposals, run model-generated code,
perform file writes, click controls, launch programs, send messages or make
purchases. The planner remains separate from the executor; a task needs a
structured execution contract and must pass the automatic tool allowlist.

This is an incremental Phase 6 capability, not arbitrary multi-step autonomy.
Interrupted-task recovery is implemented as a failed state plus owner-approved
retry; automatic continuation across restart, broader task workflows,
independently cancellable workers, and target-device tests remain open gates.

## Tool handler scheduling

The registry invokes synchronous handlers through asyncio.to_thread so they
do not block the FastAPI event loop. Awaitable results are awaited on the
event loop. Cancelling an awaiting request cannot forcibly terminate a
synchronous function already running in its worker thread; handlers must
remain read-only, bounded and internally timeout-aware. Untrusted generated
source continues to run only through the Docker sandbox.


## Restart recovery

On application startup, any task still in the running state is treated as
interrupted by the previous Mia process. It is atomically moved to failed and
gets an append-only task event identifying startup recovery. Mia does not
silently replay it: the Task Desk offers an explicit **Re-approve retry**
action. Tasks waiting for an owner decision and tasks still approved are left
unchanged. Run a single local Mia server process against the task database;
multi-process lease coordination is not implemented.


## Persistent owner pause

Pause is an owner control, not just an in-memory stop signal. The state is
stored in the local task database. After Mia restarts, a saved pause prevents
the background dispatcher from starting even when environment policy remains
enabled. The owner must press **Resume** in Task Desk or call the resume API.
Changing `MIA_AUTONOMY_ENABLED` to false still disables dispatch regardless of
the saved pause state.
