# AI task planning

Mia exposes `POST /api/planning/propose` to turn an objective and optional
evidence into a small batch of reviewable task proposals.

Example request:

```json
{
  "objective": "Review the current project for reliability improvements.",
  "evidence": "Recent test report: ...",
  "max_tasks": 5
}
```

The planner uses the configured model gateway with paid routing disabled. It
requests a strict JSON plan, rejects tool calls and malformed output, bounds
the number and size of proposals, and submits accepted findings through
`WorkloadDiscovery` for deduplication and audit history.

**This endpoint does not execute tools or approve tasks.** Every generated task
starts as `proposed`; the owner must review and approve it through the task
lifecycle. The model output is treated as untrusted. A generated summary is
labelled as AI-generated and must be checked against the supplied evidence.

If no eligible model is available, the endpoint returns a service-unavailable
response. A valid empty plan is allowed and creates no tasks.

This is a planning foundation, not yet autonomous continuous operation. The
scheduler is deliberately not wired to the planner or an executor until a
governed executor, operation timeouts, OS isolation, authentication boundary,
and cross-platform verification are in place.


## Task Desk

Open the **Task Desk** from the assistant sidebar. Enter an objective and optional
evidence to create model-generated proposals, or create the built-in CPU/RAM
read-only check. Proposals remain in the proposed state until the owner approves
or rejects them. The planner receives only registered read-only tool contracts
and may attach one exact tool call or a bounded sequence of 2-5 calls with
structured arguments to a proposal. The server validates every descriptor
against the live registry before saving it; invalid, unregistered or
preview-only tool calls reject the whole plan.

The Task Desk displays the exact proposed arguments before approval and shows
an Execute action only when the saved descriptor still matches a registered
read-only tool. Proposals without a validated descriptor remain reviewable
backlog items and are never converted from free-form text into commands.

## Manual approved-task execution

A task may be executed through POST /api/tasks/{task_id}/execute only after
its owner has moved it to approved. The task must include an explicit
metadata.execution object with exactly tool_name and arguments. The tool must
already be registered by Mia with mode read. Free-form task titles and goals
are never interpreted as commands, and preview/write/launch/click tools are
rejected by this route.

The endpoint checks CPU/RAM policy before claiming the named task, executes one
tool only, and records the result in the task event history and observatory.
It is a manually invoked, local API operation; it does not start the background
scheduler. The global emergency-stop flag blocks it as well. Preview and
mutation actions continue through the separate payload-bound approval flow. This in-process read-only boundary is not an
OS sandbox and does not make arbitrary third-party tool handlers safe. Its async
timeout can cancel cooperative async handlers, but it cannot forcibly terminate
a blocking synchronous handler; a killable isolated worker remains a release
requirement.


## Bounded multi-step read-only execution

A proposal may attach either one exact read-only tool call or a sequence of
2-5 explicit calls. Every step is validated against the live tool registry
before persistence and again before execution. The Task Desk shows the whole
ordered sequence and its exact arguments before owner approval. Execution is
sequential, fails closed at the first error, and shares one overall timeout.
A task is marked completed only if every step succeeds. Preview and mutation
tools are rejected if they appear anywhere in the sequence. Background
autonomy applies its narrower system-inspection allowlist to every step.
