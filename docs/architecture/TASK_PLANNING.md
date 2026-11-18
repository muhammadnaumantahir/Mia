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
scheduler. Preview and mutation actions continue through the separate
payload-bound approval flow. This in-process read-only boundary is not an OS
sandbox and does not make arbitrary third-party tool handlers safe. Its async
timeout can cancel cooperative async handlers, but it cannot forcibly terminate
a blocking synchronous handler; a killable isolated worker remains a release
requirement.
