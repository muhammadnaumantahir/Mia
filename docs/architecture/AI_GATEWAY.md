# Mia Universal AI Gateway

## Goal

Expose one stable Mia model interface while supporting local inference and multiple user-authorized cloud providers.

## Logical components

- Provider adapters: Ollama, FreeLLMAPI sidecar, or a later selected gateway.
- Model registry: provider, model ID, capabilities, context, health, latency, prices, quota and terms metadata.
- Task classifier: conversation, reasoning, coding, vision, research, speech or embedding.
- Router: choose a suitable eligible candidate using task fit, capability, context, known cost, latency and reliability.
- Fallback manager: retry only eligible transient failures and never repeat non-idempotent tool actions blindly.
- Credential store: OS credential manager; no plaintext secrets in Git or logs.
- Budget policy: paid mode off by default. Per-request price checks work only when input and output price metadata are known.
- Observability: record route rationale, provider, model, latency, usage metadata and fallback reason.

## Current routing policy

Default mode is local/free-first:
1. Discover locally available models.
2. Filter by requested capability, health, context and available quota metadata.
3. Check owner-configured provider routes, subject to upstream terms and current quota.
4. If no eligible candidate exists, report that the task cannot be routed rather than silently spending money.
5. Paid candidates are excluded unless paid mode is explicitly enabled, both prices are known and the estimated request cost is within the configured per-request cap.

**Monthly spend enforcement is not implemented yet.** The environment variable for a monthly ceiling is reserved for a future usage ledger; do not enable paid inference until that ledger exists and has been tested. Unknown cloud prices are not treated as zero.

Routing should optimize the task, not simply choose the newest or largest model. Benchmark-based quality and reliability evidence by task type is still future work. Do not fabricate quality scores for newly discovered models.

## FreeLLMAPI evaluation

FreeLLMAPI is the preferred first gateway prototype due to the owner's familiarity and its unified endpoint, provider adapters, key encryption, health/quota tracking, fallback and usage dashboard. Self-hosted operation must not require the hosted paid model catalog. The Mia adapter treats a FreeLLMAPI route as paid/unknown by default. The owner must inspect the actual active model chain and upstream provider terms before manually marking a route as free.

## Provider contract

A Mia provider adapter should implement:
- health check;
- model listing or documented static catalog;
- capability and context metadata;
- structured chat request and response;
- streaming cancellation;
- usage/cost reporting where available;
- structured error classification;
- secret redaction;
- timeouts and retry policy.

Streaming, model-specific capability metadata, monthly cost ledger and benchmark-based route quality remain outstanding.

## Safety requirements

- Cloud routing is a data-egress decision and must be visible.
- No provider API key may be exposed to a tool or generated code.
- No automatic key/account rotation to bypass rate limits.
- Do not retry a request with side effects unless idempotency is guaranteed.
- Store provider terms/quotas as versioned policy metadata.


Ollama is marked as a local inference provider. An OpenAI-compatible provider, including FreeLLMAPI, is treated as non-local unless `MIA_FREELLMAPI_DATA_LOCAL=true` is explicitly set after checking the complete upstream route. Localhost hosting alone is not proof that the provider will not forward data externally.

The chat tool loop withholds file and knowledge contents from non-local providers by default. `MIA_ALLOW_REMOTE_FILE_CONTEXT=true` is a separate owner opt-in that permits this data egress.
