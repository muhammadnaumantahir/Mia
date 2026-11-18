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
