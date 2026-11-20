# AI Brain (`mia.brain`)

The Brain is Mia's model-independent intelligence pipeline. It is a **supporting runtime service**
that connects the ten canonical SPS layers through explicit contracts; it is not an eleventh layer
and it does not replace the AI gateway, memory, tool registry or approval system. Text chat,
streaming and voice all enter through `Brain.run_turn` / `Brain.stream_turn`
(`POST /api/gateway/chat`, `/api/gateway/chat/stream`; `interface` is `text` or `voice` and only
labels diagnostics).

```
input -> intent -> context+memory -> route -> [plan] -> tool offer -> permission gate
      -> execute -> verify -> reply -> memory + diagnostics
```

| Module | Responsibility | SPS layer(s) |
|---|---|---|
| `intent.py` | Category, complexity, references ("continue where we stopped"), ambiguity, high-impact flag. Deterministic, no model call | SPS-03 Cognitive Core |
| `context.py` | Token-budgeted context; memory retrieved through the existing `assemble_memory_context`; remote models never receive private memory unless `MIA_ALLOW_REMOTE_FILE_CONTEXT=true` | SPS-04 / SPS-05 |
| `capabilities.py` | Task profiles; declared vs runtime-observed model capabilities (a model that fails a claimed capability twice is excluded until it succeeds) | SPS-01 / SPS-07 |
| `router.py` | Task-aware, explainable ordering on top of the gateway's free-first eligibility; raises `NoSuitableModel` rather than choosing an unsuitable model. Passed to the gateway as `order_hint`; owner routing preferences (`gateway/selection.py`) still apply | SPS-07 Adaptation |
| `plan.py`, `planning.py` | Plans with dependencies, step states, retries, timeouts, checkpoints, SQLite persistence (`mia-brain.sqlite3`), crash recovery. Model-proposed plans are strictly validated, otherwise a deterministic plan is used | SPS-03 / SPS-10 |
| `broker.py` | Capability selection, permission evaluation, schema validation, bounded execution, failure classification. Model output is untrusted: unknown, unoffered, denied or invalid calls are refused before execution | SPS-02 / SPS-10 |
| `verify.py` | Operation ledger (planned, attempted, completed, failed, verified); tool-result checks; completion-claim detection; structured-output validation; recovery advice | SPS-09 / SPS-06 |
| `evaluation.py` | Repeatable scenarios with recorded JSONL outcomes (`python -m mia.brain.evaluation [log.jsonl]`) | SPS-09 / SPS-06 |
| `diagnostics.py` | Privacy-safe turn traces | SPS-05 |

## Behaviour guarantees (each covered by tests)
- Simple turns make one model call: no plan, no tool schemas, no memory beyond a strict retrieval match.
- Ambiguous requests ("fix it" with no context) get a clarification question and no model call.
- A claim such as "I deleted the folder" without an execution record is annotated as not done. Registered tools are read/preview only, so mutations stay approval-gated.
- Provider failure never deletes memory, the user message or plan state; fallback follows the router order and keeps the same context. Streams that break mid-way keep the partial reply marked `[response interrupted]`.
- Complex requests with tools available are planned; tool steps run through the broker, failures block dependents and are reported honestly with a recovery step; approvals pause the plan.

## Diagnostics API
`GET /api/brain/diagnostics[?limit=]`, `GET /api/brain/diagnostics/{turn_id}`, `GET /api/brain/plans`,
`GET /api/brain/plans/{id}`, page at `/brain`, `POST /api/brain/plans/{id}/resume` (`answers`, `retry_failed`).
Traces hold model choice and reasons, rejections, fallbacks, intent, plan/step state, tool outcomes and
verification. They never hold prompts, replies, tool arguments or outputs, memory content or credentials.

## Configuration (all optional)
`MIA_BRAIN_PREFERRED_PROVIDERS`, `MIA_BRAIN_PREFERRED_MODELS`, `MIA_BRAIN_LOCAL_ONLY`,
`MIA_BRAIN_MAX_LIGHT_LATENCY_MS`, `MIA_BRAIN_DENY_PERMISSIONS` (comma-separated tool permissions to deny),
`MIA_CONTEXT_RETRIEVAL=false` to disable retrieval.

## Known limits
- Capability evidence persists in `mia-brain-capabilities.sqlite3` (advisory; a write failure never breaks a reply).
- Verification is structural and deterministic; there is no model judge by design.
- Resuming a plan (`POST /api/brain/plans/{id}/resume`) reads the approval store: a consumed approval completes the step ("outcome not observed" - the Brain does not verify the executed action), rejected/expired approvals fail it, pending/approved stay paused. The owner still triggers the resume.
- The diagnostics page (`/brain`) is plain HTML over the JSON API and has not been checked in a real browser. Only mocked providers were exercised in tests; no live Ollama run.


## Runtime self-description

The Brain receives a model-independent identity prompt plus a live runtime manifest. The manifest includes current registered tools/provider adapters and the state of memory, semantic reranking, permissions, voice, Code Graph and wake-word routes. It also names the ten canonical SPS layers and their design responsibilities. The architecture list is explicitly not a completion report: a layer's presence does not prove its features are implemented, enabled, healthy or owner-verified. Tests cover deterministic registry reporting, secret filtering and the ten-layer architecture statement.
