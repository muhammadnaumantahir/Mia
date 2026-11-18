# Autonomous runtime

Mia's scheduler is deliberately opt-in. Importing the application or creating an
AutonomyRuntime does not start a thread. The owner must explicitly enable an
AutonomyPolicy and call start().

## Runtime contract

- One background worker; no overlapping dispatch cycles.
- Every cycle samples local CPU and RAM before claiming an approved task.
- Missing or invalid telemetry pauses the cycle and leaves queued tasks untouched.
- Only tasks already in the 'approved' state can be claimed.
- The executor is injected by the host and must be a governed, bounded executor.
- A task that returns status=waiting remains waiting for its separate approval.
- pause() prevents future cycles. It does not forcibly terminate an in-flight
  executor call; executors must enforce their own operation timeouts.
- Emergency stop blocks new claims immediately. It is not a substitute for
- operating-system process isolation or a killable worker.

## Current limitation

This module is a scheduler primitive, not yet a complete autonomous planner.
The API does not start it automatically and the production governed executor
has not yet been connected. Do not enable background autonomy until that
executor, OS sandbox, authentication boundary, and Windows/Linux tests are ready.