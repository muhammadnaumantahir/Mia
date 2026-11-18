# Mia Universal AI Gateway

## Goal

Expose one stable Mia model interface while supporting local inference and multiple user-authorized cloud providers.

## Logical components

- Provider adapters: Ollama, FreeLLMAPI sidecar, or a later selected gateway.
- Model registry: provider, model ID, capabilities, context, health, latency, prices, quota and terms metadata.
- Task classifier: conversation, reasoning, coding, vision, research, speech or embedding.
- Router: choose a suitable eligible candidate using task fit, capability, context, quality evidence, cost, latency and reliability.
- Fallback manager: retry only eligible transient failures and never repeat non-idempotent tool actions blindly.
- Credential store: OS credential manager or encrypted local store; no plaintext secrets in Git or logs.
- Budget policy: paid mode off by default, per-request and monthly caps.
- Observability: record route rationale, provider, model, latency, usage, estimated/actual cost and fallback reason.

## Routing policy

Default mode is free/local-first:
1. Check locally available model candidates.
2. Filter by required task capability, health, context and resource constraints.
3. Check free-tier providers that the owner has configured and whose terms permit the use.
4. If no suitable candidate exists, report that the task cannot be routed rather than silently spending money.
5. Paid candidates are eligible only after explicit owner enablement and within both spend caps.

Routing should optimize the task, not simply choose the newest or largest model. Maintain benchmark-based quality and reliability evidence by task type. Do not fabricate quality scores for newly discovered models.

## FreeLLMAPI evaluation

FreeLLMAPI is the preferred first gateway prototype due to existing owner familiarity and its unified endpoint, provider adapters, key encryption, health/quota tracking, fallback and usage dashboard. Self-hosted operation must not require the hosted paid model catalog. Verify local catalog behavior, update security, key storage, upstream provider policies and localhost exposure before linking it to Mia.

## Provider contract

A Mia provider adapter must implement:
- health check;
- model listing or documented static catalog;
- capability and context metadata;
- structured chat request and response;
- streaming cancellation;
- usage/cost reporting where available;
- structured error classification;
- secret redaction;
- timeouts and retry policy.

## Safety requirements

- Cloud routing is a data-egress decision and must be visible.
- No provider API key may be exposed to a tool or generated code.
- No automatic key/account rotation to bypass rate limits.
- Do not retry a request with side effects unless idempotency is guaranteed.
- Store provider terms/quotas as versioned policy metadata.
