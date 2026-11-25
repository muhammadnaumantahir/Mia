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


## Runtime route-to-handler binding

Every mutating FastAPI route is bound at app construction to its live callable handler identity
(module and qualified name) plus the declared capability classification. If a route has no
classification, its handler is missing/non-Mia, or the binding cannot be recorded, app construction
fails closed. `tests/test_route_capabilities.py` checks the binding for every mutating route and
covers unclassified/non-Mia handlers. This inventory proves registration and classification only;
the separate COMP-04 enforcement layer must still enforce the declared approval class at execution.
