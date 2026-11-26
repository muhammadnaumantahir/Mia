# Sandbox validation report contract

The host-side `DockerSandboxRunner` accepts a validation result only when the worker's
structured evidence agrees with the immutable candidate and test files mounted read-only into
the container.

## Required worker evidence

The worker report contains four boolean gates:

- `static_checks`
- `unit_tests`
- `security_checks`
- `regression_tests`

It also carries evidence for the exact source and test SHA-256 digests, parser errors, separate
candidate/test security findings, and detailed `static`, `unit` and `regression` stage
records. Each stage record includes a boolean result, process exit code, and bounded stdout/stderr.
The security-policy identifier must match the policy implemented by the worker.

The host verifies all of the following before decorating the report with trusted Docker image and
container metadata:

1. The worker's source and test digests exactly match the files that the host staged and mounted.
2. Each stage record has a valid and internally consistent shape; a passing stage must have exit
   code zero, and its diagnostic strings stay within the worker's 8,000-character bound.
3. The top-level static/unit/regression flags agree with their detailed stage records.
4. Static checks cannot pass when parser errors are present; unit tests cannot pass without an
   immutable candidate test file.
5. The security gate is derived from both candidate and test findings; a report with findings
   cannot claim that security checks passed.
6. Missing evidence, unknown policy identifiers, malformed values, hash mismatches and
   contradictions raise `SandboxExecutionError`. The API therefore cannot persist a passing
   validation state from an incomplete worker result.

The API does not accept a client-supplied validation report. It reads the immutable candidate and
tests, runs the pinned Docker image, checks the worker evidence, and only then records the result.
Timeouts and non-zero container exits produce failed reports, so registration and activation remain
blocked.

## Execution boundary

Candidate source and candidate tests run only in the disposable Docker container. The host process
stages the files, invokes Docker, validates JSON evidence and stores the result; it does not import
or execute generated candidate code. The container continues to use no network, a read-only
root filesystem, a read-only candidate mount, no added Linux capabilities, no-new-privileges,
a non-root UID, and bounded CPU, memory, PIDs and wall-clock time.

This report contract improves evidence integrity; the static AST denylist is still defense in depth,
not a proof that arbitrary Python is safe. Docker isolation remains the execution boundary.
