> **Architecture decision (owner-approved):** This document describes legacy gateway implementation details and must be read alongside [Independent Mia AI Gateway direction](AI_GATEWAY_DIRECTION.md), which is authoritative. FreeLLMAPI and OmniRoute are **not** approved runtime dependencies, sidecars or control planes. Any existing adapter/configuration for them is legacy migration scope; do not add new dependencies or setup instructions for either. New gateway work must use Mia-owned contracts and direct provider adapters.

# Mia Universal AI Gateway

## Goal

Expose one stable Mia model interface while supporting local inference and multiple user-authorized cloud providers.

## Logical components

- Provider adapters: local Ollama and directly configured cloud/self-hosted OpenAI-compatible endpoints, all called by Mia.
- Model registry: provider, model ID, capabilities, context, health, latency, prices, quota and terms metadata.
- Task classifier: conversation, reasoning, coding, vision, research, speech or embedding.
- Router: choose a suitable eligible candidate using task fit, capability, context, known cost, latency and reliability.
- Fallback manager: retry only eligible transient failures and never repeat non-idempotent tool actions blindly.
- Credential store: OS credential manager; no plaintext secrets in Git or logs.
- Budget policy: paid mode off by default. Per-request price checks work only when input and output price metadata are known. Invalid, negative, NaN or infinite price/budget values fail closed and exclude paid candidates.
- Observability: record route rationale, provider, model, latency, usage metadata and fallback reason.

## Current routing policy

Default mode is local/free-first:
1. Discover locally available models.
2. Filter by requested capability, health, context and available quota metadata.
3. Check owner-configured provider routes, subject to upstream terms and current quota.
4. If no eligible candidate exists, report that the task cannot be routed rather than silently spending money.
5. Paid candidates are excluded unless paid mode is explicitly enabled, both prices are known and the estimated request cost is within the configured per-request cap.

**Budget enforcement:** paid inference is disabled by default. It requires explicit request, daily UTC and monthly UTC ceilings (MIA_MAX_REQUEST_SPEND_USD, MIA_MAX_DAILY_SPEND_USD, MIA_MAX_MONTHLY_SPEND_USD) plus MIA_ALLOW_PAID=true. Missing, malformed, negative or non-finite limits disable paid routes. The append-only usage ledger calculates daily and monthly spend. Unknown cloud prices are not treated as zero. Budget ceilings are checked before routing; concurrent in-flight requests are not yet atomically reserved against the same remaining allowance, so paid use should remain disabled until reservation accounting is implemented and tested.

Routing should optimize the task, not simply choose the newest or largest model. Benchmark-based quality and reliability evidence by task type is still future work. Do not fabricate quality scores for newly discovered models.

## Gateway project research (non-runtime)

FreeLLMAPI and OmniRoute are not approved runtime dependencies, sidecars or control planes. Other gateway projects (including LiteLLM, Bifrost and Portkey) may be studied for isolated patterns. Any code reuse requires exact-source, licence, security and dependency review and must not make Mia depend on the upstream project. Prefer implementing required behavior in Mia-owned modules.

Existing provider adapters or configuration for FreeLLMAPI/OmniRoute, if present in the codebase, are legacy migration scope. Do not add new setup instructions or new runtime dependencies for them.

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

Streaming tool calls now reach the gateway layer; model-specific capability provenance and a persistent registry exist. Benchmark-based route quality, semantic caching, specialized multimodal paths and online route evaluation remain outstanding.

## Safety requirements

- Cloud routing is a data-egress decision and must be visible.
- No provider API key may be exposed to a tool or generated code.
- No automatic key/account rotation to bypass rate limits.
- Do not retry a request with side effects unless idempotency is guaranteed.
- Store provider terms/quotas as versioned policy metadata.


Ollama is marked as a local inference provider. Any remote-compatible provider is treated as non-local unless its data residency and routing are independently verified and explicitly configured. Localhost hosting alone is not proof that the provider will not forward data externally.

The chat tool loop withholds file and knowledge contents from non-local providers by default. `MIA_ALLOW_REMOTE_FILE_CONTEXT=true` is a separate owner opt-in that permits this data egress.

## Streaming, usage ledger and request/daily/monthly budget

- `POST /api/gateway/chat/stream` returns Server-Sent Events: `delta` (text fragments), then `done` (provider, model, token counts, cost, conversation id) or `error` (`partial: true` when text had already been sent). Streaming is text-only: tool-bearing chat stays on `POST /api/gateway/chat`; requests with tools passed to `ModelGateway.stream` use one buffered call.
- Fallback to the next candidate happens only before the first token. A failure after output has started is surfaced, not retried on another model; the saved assistant message is marked `[response interrupted]`.
- Ollama (NDJSON) and OpenAI-compatible (SSE, `stream_options.include_usage`) adapters report provider token counts when available. Ledger rows say `reported` or `estimated`.
- Cost: free-tier routes record 0. Paid routes use reported tokens times the candidate prices; with no reported usage the worst-case estimate is recorded so spend is never undercounted. Invalid prices produce no cost value and such routes are already ineligible.
- `UsageLedger` (SQLite `mia-usage.sqlite3` in `MIA_DATA_DIR`) is append-only. `GET /api/gateway/usage?days=N` returns totals per provider/model and today's UTC spend; the ledger also computes the current UTC calendar-month total.
- Request/daily/monthly caps: `MIA_MAX_REQUEST_SPEND_USD`, `MIA_MAX_DAILY_SPEND_USD` and `MIA_MAX_MONTHLY_SPEND_USD` are all mandatory for paid routing. Each paid request uses the minimum of the per-request ceiling and remaining daily/monthly budgets. A missing, malformed, non-finite or negative value disables paid routing. Paid routing still requires `MIA_ALLOW_PAID=true`.
- The `/gateway` console provides provider health and connection tests, model discovery, model settings, recent route decisions, usage and budget views. Route-decision history is persisted in `mia-routing.sqlite3` with bounded retention; records contain route metadata only, never prompts, messages, tool arguments or credentials. The Assistant page surfaces the proactive inbox with text-only rendering and owner read/done/dismiss controls.
- Not yet done: atomic in-flight budget reservations for concurrent paid requests, rate-limit remaining-quota header ingestion, mid-stream paid spend cut-off, semantic response cache, benchmark-driven quality/route evaluation, complete embedding and multimodal adapters, and remote owner-admin authentication (the app remains localhost-only). Keep paid inference disabled until budget reservation is implemented.

## Latency measurement

The gateway times the provider call that actually succeeded (failed fallback attempts are not included). Buffered calls record `total_ms`; streamed calls also record `first_token_ms` (request start to first text). Timestamps are taken when a chunk is received, and time the generator spends suspended while the consumer handles an earlier chunk is subtracted, so a slow browser does not look like a slow model. The ledger stores only finite, non-negative values (otherwise NULL), `GET /api/gateway/usage` shows per-model average latency, and the stream `done` event carries both numbers. Daily experiment snapshots add count, mean, median and nearest-rank p95 for the window, and write median and p95 as daily metrics; the report keeps these per day and does not add them up. Requests with no measurement are excluded from the statistics. Not measured: queueing before the gateway, network time to the browser, and tokens per second. Local timings depend on the host's load and model state, so only same-host comparisons mean anything.

## Rate-limit tracking and failure backoff

`ModelGateway` owns a `CandidateHealthTracker` (`src/mia/gateway/health.py`), keyed by `provider/model`, so a failing candidate is not retried first on every request.

- **Throttle (HTTP 429, or 503 with `Retry-After`)** — the candidate is *blocked* until the provider-advised wait elapses (`Retry-After` as delta-seconds or HTTP-date; capped at 900 s; with no usable header it falls back to the exponential backoff below). Mia never retries inside a provider's advised wait.
- **Other transient failures (timeout, 5xx, connection error)** — the candidate is *degraded*: tried after healthy candidates with exponential backoff (5 s doubling, capped at 300 s), but never excluded, so a lone local Ollama model stays usable after a blip.
- **Success** clears the record. **Permanent errors** (4xx) are request-specific and are not recorded.
- If every eligible candidate is blocked, the gateway raises a `TransientProviderError` carrying the shortest remaining wait without calling any provider. `POST /api/gateway/chat` turns that into HTTP 503 with a `Retry-After` header; the stream `error` event carries `retry_after_seconds`.
- `GET /api/gateway/health` lists candidates that are currently rate limited, backing off or recovering. Invalid header values (negative, NaN, infinite, unparsable) are discarded.

State is process-local and not persisted; a restart resets it. This does not rotate keys or accounts to evade limits. The failure ordering applies after the cost/reliability ranking, so paid-route eligibility and spending caps are unchanged. Verified by `tests/test_gateway_health.py` (injected clock, httpx responses via respx, and API round trips); not exercised against a live rate-limited provider.

## Provider configuration (environment)

Direct OpenAI-compatible providers are registered from `MIA_EXTRA_PROVIDERS` and `MIA_PROVIDER_<ID>_*` variables; the API key
comes only from the OS credential store (`mia-credentials set <provider>`), never from
the environment or the repository. Ollama is always registered; nothing else is, so a
local-only setup needs no keys.

| Variable | Meaning |
|---|---|
| `MIA_EXTRA_PROVIDERS=id1,id2` | Register independently configured direct provider adapters; do not add gateway sidecars |
| `..._BASE_URL` | Provider base URL, validated (`gateway/urlpolicy.py`) |
| `..._ROUTE_FREE` | Treat the route as free (default false) |
| `..._DATA_LOCAL` | Treat data as staying local (default false) |
| `..._ENABLE_TOOL_CALLING` | Advertise tool calling (default false) |
| `..._TIMEOUT_SECONDS` | Request timeout, 1 to 600; invalid values use 90 |
| `..._HEADERS` | JSON object of extra non-secret headers; `Authorization`, `Host` and framing headers are refused |
| `MIA_MAX_REQUEST_SPEND_USD`, `MIA_MAX_DAILY_SPEND_USD`, `MIA_MAX_MONTHLY_SPEND_USD` | All three valid ceilings are required before paid inference can be enabled |

`ModelGateway.test_connection(provider_id)` lists models and returns a sanitized
status (`ok`, `unreachable`, `auth_failed`, `invalid_response`, `unknown_provider`,
`error`) with model count and latency; it never returns URLs, headers or bodies.
Model discovery skips a provider whose credentials are rejected instead of failing.
The local `/api/gateway/providers/{provider_id}/test` endpoint returns a sanitized connection result. The app remains localhost-only; no authenticated remote owner-admin surface exists.
