# Safety and Governance

Mia is intended to be capable of broad computer assistance and self-improvement. That requires stronger controls, not fewer controls.

## Default operating posture

- Local-only service binding.
- Read-only discovery before mutation.
- Least-privilege tool scopes.
- Explicit preview of high-impact plans.
- Approval required for destructive, irreversible, financial, credential, security, external-communication and deployment actions.
- Auditable event history.
- Emergency stop for agent tasks and autonomous evolution.

## Generated-code isolation

Generated code must not execute in the main Mia process. Use an isolated worker process, restricted account or container where available. On Windows, do not assume Docker is installed or that a restricted Python subprocess alone is a complete security sandbox. The actual isolation method must be tested and documented before enabling autonomous code execution.

Never pass provider keys, browser cookies, session tokens or unrelated personal files into a generated-code workspace. Use narrowly scoped temporary credentials only when truly required, and revoke them after the task.

## Data trust boundaries

Webpages, repositories, PDFs, documents, emails, terminal output, model responses and external skills are untrusted input. They may contain prompt injection or malicious instructions. They cannot modify system policy, unlock permissions, access secrets or bypass the approval layer.

## AI provider governance

- Paid providers are disabled by default.
- Free tier availability is volatile and is not a guaranteed service level.
- Apply provider-specific rate limits and terms of service.
- Do not rotate accounts or keys to circumvent a quota or restriction.
- Enforce per-request and monthly spend caps before dispatch.
- Record model, provider, cost estimate, actual usage and fallback reason without logging secret values.
- Cloud routing requires explicit disclosure that task content leaves the local device.

## Computer permissions

Computer access is expressed as capabilities: browser read, browser interaction, file read, file write, app launch, shell execution, system configuration, external communication and so on. Each permission is granted independently. The owner can later choose a trusted profile with broader pre-authorization, but that policy must remain visible, revocable and logged.

## Voice privacy

Show microphone state clearly. Provide a mute control, push-to-talk fallback and a visible wake-word indicator. Do not retain raw audio by default. Keep transcripts and audio retention configurable.

## Evolution guardrails

- Day 0 baselines and benchmark definitions are immutable once the experiment starts.
- Candidate changes are evaluated in a separate workspace.
- A failed validation blocks activation.
- Policy and governance code cannot be self-modified by ordinary generated capabilities.
- The system must preserve rollback and the ability to disable autonomy.
- No-op, defer and reject are valid decisions.
