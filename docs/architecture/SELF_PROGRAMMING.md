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

The store persists candidate source only. It does not execute or import generated code.

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
