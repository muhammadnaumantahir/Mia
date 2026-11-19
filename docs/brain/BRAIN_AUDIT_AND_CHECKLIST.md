# AI Brain (Feature 3) - audit and implementation checklist

Audited against `main` @ 8b624a6. Baseline: full `pytest` suite green before any Brain change.

## 1. Actual flow today

```
User input (text: /api/gateway/chat, /api/gateway/chat/stream; voice: browser STT -> same endpoints)
  -> memory_store.append_message
  -> prepare_chat_context (assemble_memory_context + data-egress policy)        [api.py closure]
  -> ModelGateway.complete / .stream (rank_candidates -> health order -> fallback)
  -> tool loop (<=4 rounds) inside the chat handler, ToolRegistry.execute       [api.py closure]
  -> assistant message stored, ledger + observatory event
```

Planning is a separate path: `TaskPlanner.propose` -> `WorkloadDiscovery` -> `TaskStore`
(approval, leases, `TaskToolExecutor`, `AutonomyRuntime`). It is not connected to chat.

## 2. What already exists and is reused unchanged

| Area | Module | Notes |
|---|---|---|
| Provider protocol, normalised responses, errors | `gateway/contracts.py`, `providers.py` | Ollama + OpenAI-compatible (FreeLLMAPI/Omniroute-style) |
| Cost/context/capability eligibility | `gateway/routing.py` | free-first, paid disabled by default |
| Fallback, rate-limit waits, degraded ordering | `gateway/service.py`, `health.py` | stream falls back only before first token |
| Memory retrieval and egress policy | `memory/context.py`, `prepare_chat_context` | real API, no duplicate DB |
| Identity | `memory/identity.py` | versioned |
| Tool contracts, schema validation, read/preview only | `agent/tools.py` | no shell, no direct mutation |
| Approvals, task lifecycle, leases | `computer/approvals.py`, `agent/tasks.py` | |
| Observatory events/layers | `observatory/store.py`, `sps/layers.py` | |

## 3. Gaps found

1. Chat logic is duplicated and embedded in `api.py` closures; the stream path has no tools, no verification.
2. Routing is task-blind: `task_type` is only a label. No task profile, no explanation of why a model was chosen,
   no refusal when only an unsuitable model exists, no user preference, no latency/reliability trade-off per task.
3. Declared capabilities are trusted as-is; nothing records runtime verification (e.g. a model that claims `tools` but never returns a valid call).
4. No intent detection, reference resolution ("continue where we stopped") or ambiguity detection.
5. Planning is a manual proposal API; chat cannot decompose a complex request, track steps/dependencies, or checkpoint.
6. Tool calls chosen by the model are executed directly; there is no separate capability-selection / permission-evaluation / verification stage and no per-step status (planned/attempted/completed/failed/verified).
7. No completion verification: an assistant claim of "done" is stored as-is.
8. No Brain diagnostics (selected model + reason, retries, steps, verification).
9. No evaluation scenarios with recorded outcomes.

## 4. Target design (supporting runtime service, NOT an eleventh SPS layer)

Package `mia.brain` orchestrates the existing layers through contracts:

| Component | SPS layer(s) |
|---|---|
| `intent` (intent, references, ambiguity, complexity) | SPS-03 Cognitive Core |
| `capabilities` + `router` (task-aware, explainable model selection) | SPS-07 Adaptation Core (routing), SPS-01 (capability definitions) |
| `context` (budgeted context, memory retrieval via existing API) | SPS-04 Knowledge, SPS-05 Experience |
| `plan` (steps, dependencies, checkpoints, state store) | SPS-03 Cognitive, SPS-10 Execution (lifecycle) |
| `broker` (capability selection, permission evaluation, execution) | SPS-02 Governance, SPS-10 Execution |
| `verify` (claim ledger, output validation, evaluation) | SPS-09 Verification & Validation, SPS-06 Meta-Learning |
| `diagnostics` (decision trace, events) | SPS-05 Experience (traceability events) |

Separation enforced in code: reasoning (model) -> planning -> capability selection -> permission evaluation -> execution -> verification.
Model output is untrusted input at every boundary.

## 5. Checklist (status updated as work lands)

- [x] M1 Audit and checklist (this file)
- [x] M2 Provider orchestration: capability registry (declared vs verified), task profiles (`brain/capabilities.py`)
- [x] M3 Task-aware routing with explanations, refusal when unsuitable, fallback order (`brain/router.py`, `ModelGateway order_hint`)
- [x] M4 Intent/reference/ambiguity + budgeted context + memory integration (`brain/intent.py`, `brain/context.py`)
- [ ] M5 Planning engine with dependencies, checkpoints, persistence
- [ ] M6 Tool broker: selection, validation, permission gate, result interpretation
- [ ] M7 Verification ledger, output validation, recovery, evaluation scenarios
- [ ] M8 Single Brain pipeline used by chat, stream and voice endpoints
- [ ] M9 Diagnostics API/dashboard data
- [ ] M10 Full regression, docs, STATUS update

Out of scope (per feature brief): unrestricted computer control, self-modifying code, autonomous life runtime.
