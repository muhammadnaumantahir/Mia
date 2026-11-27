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
| dependencies | Exact capability ID + semantic version references; dependencies must already exist and remain registered/active/deprecated (retired or rolled-back dependencies block registration and execution) |\n| provenance | Author, upstream URL, license, parent capability and source commit |
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

## Immutable capability versions and dependencies

Each saved candidate version is immutable and stores source/test hashes, provenance,
declared permissions, an optional parent version, exact version-pinned dependencies,
validation evidence and append-only lifecycle events. A later version never overwrites an
earlier one. The Capability Lab can display a hash-verified unified diff from the declared
parent version; the diff is generated from the stored sources, not from a mutable checkout.

Dependencies are exact `{capability_id, version}` references. A dependency must already
exist in a registered, active or deprecated state when the candidate is proposed. Mia
rechecks every dependency before registration, activation and one-shot execution. A missing,
retired or rolled-back dependency blocks the transition or execution. This prevents a
candidate from silently following a moving "latest" dependency.

Dependencies are provenance/contract metadata for pure-data candidates; they do not grant
host permissions or provide runtime imports. Host actions remain declared separately in
`permissions` and are converted to ordinary owner-reviewed computer tasks.
