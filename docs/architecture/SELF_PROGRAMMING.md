# Mia self-programming architecture

## Capability evolution lifecycle

Mia must preserve the ten-layer SPS model. Capability evolution uses existing layers:
- Software DNA defines capability identity and contracts.
- Governance checks permission, risk and owner policy.
- Cognition and Knowledge identify a gap and compare available solutions.
- Experience and Meta-Learning provide outcome evidence.
- Adaptation selects reuse, adaptation, composition or new implementation.
- Evolution persists a versioned candidate.
- Verification & Validation runs tests and regression checks.
- Execution activates only through a governed, isolated runtime.

## Implemented foundation

The current CapabilityStore:
- records stable capability IDs and semantic versions;
- records the parent version, reason, source kind, upstream URI, license and declared permissions;
- persists source code as immutable version files under the ignored generated-code workspace;
- records a SHA-256 hash and verifies it when source is read;
- tracks proposed, validating, validated, rejected, registered and retired states;
- records validation evidence as structured, append-only events;
- prevents a previously persisted capability version from being overwritten.

The store persists candidate source only. It does not execute or import generated code. The local API exposes candidate proposal, validation start/report, registration, lineage and activation-status routes. Activation requests return a conflict explaining that the OS sandbox is missing.

## Validation contract

A candidate cannot be registered unless all these checks are recorded as passing:
- static_checks;
- unit_tests;
- security_checks;
- regression_tests.

The current API accepts a validation report from an external runner; it does not itself run tests. The future validation worker must run in a separate constrained environment and attach logs, command/version, environment, benchmark results and code hash to the report.

## Activation is blocked

Dynamic activation is intentionally unavailable. A database state change is not a sandbox. Before activation can exist, Mia needs:
- an OS-enforced isolated worker (container or restricted OS account);
- filesystem and network allowlists;
- no inherited provider keys, browser cookies or user secrets;
- CPU, memory, time and output limits;
- a capability host protocol that does not import untrusted code into the privileged process;
- signed/hashed validation evidence;
- health monitoring, version pinning and rollback.

Windows-specific process/job isolation has not yet been implemented or verified. Therefore candidate code cannot be activated by this foundation build.

## Decision choices

For each discovered capability gap, the system must consider:
- reuse an already registered capability;
- adapt a compatible upstream implementation;
- improve an existing capability;
- compose multiple capabilities;
- create new source;
- defer or reject.

No-op is a valid result. Mia must never create code merely to increase its capability count or create a misleading Day N growth curve.

## Promotion (activation) and rollback

Activation is a governance record, not code loading. It is disabled unless the owner sets `MIA_CAPABILITY_ACTIVATION_ENABLED=true`; without it every attempt returns 409 and nothing changes.

- `POST /api/capabilities/{id}/{version}/activate` needs `approved_by` and a `reason`. Only a `registered` version with a passing validation report, no requested permissions (the permission broker does not exist) and a source file that still matches its stored hash can be promoted.
- At most one version per capability is `active`, enforced by a unique database index. Promotion moves the previous active version to `deprecated` in the same transaction and records it in the event payload.
- `POST /api/capabilities/{id}/rollback` marks the active version `rolled_back` (terminal, cannot return) and restores the `deprecated` version it replaced. If it replaced nothing, the capability is left with no active version. Both actions write capability events and Observatory events (SPS-08).
- `GET /api/capabilities/{id}/active` returns the current pointer.
- Nothing imports, hot-loads or runs the active version in the Mia process. Generated code still runs only through the existing isolated single-call container, so the verified-Windows-sandbox and permission-broker requirements for real use are unchanged and not met.

