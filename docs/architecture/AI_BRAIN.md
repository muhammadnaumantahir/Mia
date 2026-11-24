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

## Conversational task actions (MIA-LIFE-03, Brain half)

`src/mia/brain/task_actions.py`, wired through the optional `task_actions` hook on `Brain`
(`orchestrator.py`) next to the clarification short-circuit. An explicit task command is answered
by the Brain itself with **no model call** (provider `mia-brain`, model `task-action`, diagnostics
outcome `task_action`, same behaviour for `run_turn` and `stream_turn`). It acts on
`memory.personal`, the same store behind the `/tasks` page and `/api/personal/items`, so chat,
page and API always agree. A hook failure falls back to normal chat. `evaluation.py` and
`live_eval.py` build a `Brain` without the hook, so evaluation behaviour is unchanged.

- **Commands (English, anchored; ordinary sentences never match).** Add (`add a task: ... due
  YYYY-MM-DD[ HH:MM] high priority in project X`, or `add ... to my task list`), list (`show my
  open|overdue|done tasks`, `what are my tasks`), complete (`mark task X done`, `complete task X`),
  set priority, delete one task, delete finished/all tasks, complete all overdue/open tasks. A task
  is named by title or by the 8-character id shown in listings; an ambiguous or missing name changes
  nothing and says so. Dates are read in the owner's `owner_timezone`; a date alone means 23:59.
- **Confirmation.** Every delete and every change to more than one task is only *requested*: the
  reply says exactly what would happen and nothing changes until the owner replies "yes". Any other
  message cancels it, it expires after 10 minutes, it is held in memory only (a restart drops it) and
  it is per conversation. At confirmation only the ids shown and still matching are touched; more
  than 200 tasks is refused from chat.
- **Privacy.** Private tasks are invisible to chat: never listed, matched by title or id, or changed,
  and bulk requests exclude them with a note. Temporary chats are read-only (listing works, changes
  are refused). Credential-like text is refused and never echoed. Audit entries use actor `brain`;
  created tasks cite the source conversation and message and have provenance `owner_provided`.
- **Not done.** English only (Roman Urdu/Urdu commands fall through to the model; A7's language
  layer is not involved), no reminders or notes from chat, no rename or due-date edits, no undo,
  no voice-specific confirmation handling beyond the same text flow, no browser/accessibility test
  of the chat surface.
