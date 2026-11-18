# Capability Contract

Every tool or self-developed capability must publish a machine-readable contract before it is registered or activated.

## Required contract fields

| Field | Meaning |
|---|---|
| capability_id | Stable, unique identifier |
| name / version | Human-readable name and semantic version |
| description | What the capability does and does not do |
| input_schema / output_schema | Validated data contract |
| permissions | Explicit filesystem, network, application and system scopes |
| side_effects | Files changed, messages sent, processes started or other effects |
| resource_limits | CPU, memory, time, network and output ceilings |
| provenance | Author, upstream URL, license, parent capability and source commit |
| validation | Static checks, tests, security checks and regression cases |
| activation | Entry point, isolation boundary and health check |
| rollback | Previous version and restoration procedure |

## Lifecycle states

proposed → specified → generated → validating → validated → registered → active → deprecated/retired

Failure states include rejected, validation_failed, activation_failed and rolled_back. State transitions must be recorded as append-only events.

## Permission principles

- Default to read-only.
- Ask for the narrowest permission that supports the task.
- Do not give generated code the assistant's credentials or full host access.
- Treat network, shell, registry, startup persistence, system settings and file deletion as separate permissions.
- Require explicit approval for destructive or externally visible effects unless a narrow owner-defined policy exists.
