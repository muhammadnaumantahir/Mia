# SPS canonical layer contract

The following ten layers are the canonical SPS framework. Their names, identities and order are preserved. Mia may implement additional modules and runtime services, but must not rename, merge, reorder or add to this layer list without an explicit thesis-framework decision by the owner.

| ID | Canonical layer | Responsibility in Mia |
|---|---|---|
| SPS-01 | Software DNA Core | Identity, design constraints, capability definitions and structural invariants |
| SPS-02 | Governance Core | Permissions, policy, risk, budgets, approvals and audit constraints |
| SPS-03 | Cognitive Core | Intent interpretation, reasoning, planning and decision support |
| SPS-04 | Knowledge Core | Research, documents, repositories, retrieval and source provenance |
| SPS-05 | Experience Core | Interaction history, task outcomes, observations and experience records |
| SPS-06 | Meta-Learning Core | Evaluate what worked, benchmark policies and learn from trace evidence |
| SPS-07 | Adaptation Core | Adjust strategies, model routing, workflows and capability composition |
| SPS-08 | Evolution Core | Propose, create, improve, version, register and retire capabilities |
| SPS-09 | Verification & Validation Core | Static checks, tests, security checks, benchmarks and regression gates |
| SPS-10 | Execution Core | Controlled tool execution, runtime orchestration and task lifecycle |

## Runtime boundary

The coordinator, API server, scheduler, user interface, provider gateway, computer adapters, memory stores and observatory are supporting runtime components. They connect to the SPS layers through explicit contracts; they are not additional SPS layers.

## Required traceability

For each meaningful decision or mutation, record:
- event ID and UTC timestamp;
- experiment ID and day index, when an experiment is active;
- source layer and capability ID/version, where applicable;
- trigger and rationale;
- inputs/outputs or privacy-safe references to them;
- decision and policy result;
- validation evidence;
- outcome and rollback reference.

## Current implementation status

The initial code contains immutable metadata definitions for the ten layers. This is a registry foundation, not a claim that the ten full layer implementations are complete.
