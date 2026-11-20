# AI Gateway audit (milestone 1)

# Current implementation addendum — 2026-11-20

> The body below records a historical 2026-11-19 baseline and plan. It is not the current implementation inventory. This addendum supersedes statements in that snapshot that still describe FreeLLMAPI/OmniRoute as runtime integrations or model registry/controls and the diagnostics UI as missing.

Current branch: `development/complete-product-gaps`

- Runtime factory now registers Ollama plus only explicitly listed generic direct providers (`MIA_EXTRA_PROVIDERS`, `MIA_PROVIDER_<ID>_*`). Legacy FreeLLMAPI/OmniRoute flags are ignored; neither is a built-in provider or required sidecar.
- The persistent model registry, capability provenance, enable/disable, alias and preference storage are implemented. Per-model stored preferences now participate in gateway candidate ordering.
- The gateway exposes a local `/gateway` console for provider health, sanitized connection tests, model discovery, model settings, usage, budgets and recent routing decisions.
- The usage ledger now computes UTC calendar-month spend. Paid routing requires valid per-request, daily and monthly limits; any missing/invalid limit fails closed. Free/local routing remains available without cloud credentials.
- The gateway API/admin surface remains local-only and unauthenticated. Do not bind it to a public interface.
- Atomic in-flight paid-request reservations are implemented in this branch: `BEGIN IMMEDIATE` serializes reservations, live reservations are included in daily/monthly spend checks, and usage settlement removes the reservation in the same transaction. Concurrent overbooking, release/settlement, unknown-cost rejection and cancelled completion/stream reservation-retention tests are present but have not been executed in this environment. Invalid provider token counts are rejected; unknown paid cost does not consume the reservation. Remaining gateway engineering: remaining-quota headers; persistent health/circuit-breaker/decision history; provider discovery TTL; benchmark-driven quality routing; semantic cache; full multimodal/embedding adapters; token/context compaction and mid-stream paid spend cut-off.
- Automated tests were added for direct provider configuration, monthly/daily budget caps, API console, model settings and persisted routing preferences. They have **not** been executed in this working environment; wait for CI runner evidence or a local run before recording them as passing. No live cloud provider has been contacted by this implementation task.


Audit date: 2026-11-19. Method: reading `src/mia/gateway/*`, the gateway call sites
in `src/mia/observatory/api.py`, `src/mia/brain/*`, `src/mia/agent/planner.py`, the
existing gateway docs and tests, plus a baseline run of the existing suite.

**Baseline (before any change):** `ruff check src tests` passed;
`pytest` reported **577 passed, 1 skipped, 1 warning** (Linux, Python 3.12.3, mocked
HTTP only). No live Ollama, FreeLLMAPI, Omniroute or other provider was available in
this environment, so nothing below has been verified against a real provider.

## What exists (code in `src/mia/gateway/`)

| File | Contents |
|---|---|
| `contracts.py` | `ModelRequest`, `ModelCandidate`, `ProviderResponse`, `StreamChunk`, `TokenUsage`, `RoutePolicy`, `Transient/PermanentProviderError`, `ModelProvider` and `StreamingModelProvider` protocols |
| `providers.py` | `OllamaProvider` (native API, NDJSON streaming, `/api/show` context and capability discovery) and `OpenAICompatibleProvider` (`/models`, `/chat/completions`, SSE streaming, usage) |
| `factory.py` | Registers Ollama by default; FreeLLMAPI only when `MIA_FREELLMAPI_ENABLED=true` with key from the OS credential store; paid-route and daily-cap policy |
| `registry.py` | `ProviderRegistry` (provider IDs only) |
| `routing.py` | `rank_candidates` (capability, context, quota, cost, reliability, latency) and cost estimation |
| `service.py` | `ModelGateway.complete/stream/discover_models`, health-ordered fallback |
| `health.py` | `CandidateHealthTracker` (429 / Retry-After blocking, exponential backoff for degraded candidates) |
| `ledger.py` | Append-only SQLite usage and cost ledger |
| `credentials.py` | OS credential store wrapper (keyring) |

Consumers of the gateway: `/api/gateway/*` endpoints and chat in `observatory/api.py`,
`brain/planning.py`, `brain/router.py` (task-aware ordering hint), `agent/planner.py`.

## Capability matrix

Status legend: **Present** (code and mocked tests exist), **Partial**, **Missing**.
None of these is marked "verified live".

| Capability | Existing status (audited) | Required work |
|---|---|---|
| Provider abstraction | Partial. Protocol covers health, list, complete, stream. No structured-output, embeddings, capability reporting or distinct auth/refusal/context-length error classes (all 4xx collapse to `PermanentProviderError`) | Extend contract; normalized error taxonomy |
| Provider integrations | Partial. Ollama and generic OpenAI-compatible exist. FreeLLMAPI is wired through the OpenAI-compatible adapter. **No Omniroute adapter or factory entry exists** (only a mention in docs). No custom headers, per-provider timeout config, or second compatible provider via config | Config-driven provider list, custom headers, Omniroute entry, connection test |
| Model registry | Missing. `ModelCandidate` objects are rebuilt on every discovery and not persisted. No aliases, enable/disable, preference, pinned model, or capability provenance (reported / configured / tested / unknown) | Persistent registry with provenance and verification status |
| Model discovery | Partial. Per-request discovery with no caching or refresh policy; Ollama reads real context via `/api/show`, OpenAI-compatible capabilities are static guesses | Cache with TTL, refresh endpoint, provenance |
| Intelligent routing | Partial. Cost / reliability / latency ranking plus a task-aware hint in `brain/router.py`. No routing modes (local-first, free-first, quality, latency, manual), no pinned-model handling, no recorded rejected alternatives | Routing preferences, pinning with explained overrides, decision records |
| Provider fallback | Partial. Fallback across candidates for transient errors, only before the first streamed token. No circuit breaker beyond backoff, no configurable retry policy or retry limit, no privacy/cost-change guard when switching models mid-chain beyond the policy filter | Retry policy, breaker, privacy-aware fallback rules |
| Streaming | Partial. SSE text streaming with usage and latency. Tool-bearing requests are buffered. Cancellation, partial-response preservation and disconnect handling not audited as complete | Cancellation, streaming tool calls, interruption tests |
| Tool calling | Partial. Tool schemas pass through and execution is brokered by Mia (brain broker). Owner-gated per provider by env flag; no gateway-level normalization or argument validation layer | Normalization and validation in gateway |
| Health monitoring | Partial. Per-candidate throttle/backoff tracking exposed at `/api/gateway/health`. Process-local, not persisted; no provider-level latency history | Provider-level health and latency stats |
| Context handling | Partial. Token estimates are `len/4`; context window check in ranking; memory context via brain/memory modules. No provider-specific budgeting, compaction or oversized-request handling in the gateway | Token-budget and compaction layer |
| Security | Partial. Keys live in the OS credential store; no keys in SQLite. `base_url` only checked for an `http(s)` prefix: **no SSRF/URL validation**; no request-size limit on gateway inputs beyond field limits; no provider-admin API (so no owner-only admin yet) | URL validation, admin authorization, redaction tests |
| Observability | Partial. Usage ledger with reported/estimated tokens, cost and latency. No stored route decision records | Route decision records without prompt content |
| Diagnostics UI | Missing. Health and usage are JSON endpoints only; docs list the UI as outstanding | Dashboard |
| Embeddings / vision / audio paths | Missing as separate selection paths | Capability-based selection, optional |
| SPS layers | Intact (`docs/architecture/SPS_LAYERS.md`, `tests/test_sps_layers.py`); gateway stays infrastructure | Keep unchanged |

## Observations worth carrying forward

- Documentation (`docs/STATUS.md`) already marks the gateway "In progress" and lists
  streaming tool calls, a health/usage UI, OpenAI-compatible capability discovery and
  quota-header tracking as remaining. This audit agrees with those gaps and adds:
  no Omniroute adapter, no persisted model registry, no URL validation, no pinning or
  routing modes.
- Existing tests are mocked HTTP only. Per the task rules, no claim of live provider
  verification is made.

## Plan (milestones, in order)

1. Audit (this document).
2. Provider contract and error taxonomy; URL validation; config-driven providers
   (FreeLLMAPI, Omniroute, generic OpenAI-compatible).
3. Persistent model registry with capability provenance.
4. Routing modes, pinning and decision records.
5. Retry policy and circuit breaker.
6. Streaming cancellation and streaming tool calls.
7. Tool-call normalization and validation; structured output.
8. Context budgeting; specialized model paths.
9. Diagnostics API and UI.
10. Subsystem integration review, full regression, documentation.

## Progress log

- **Milestone 2 (provider hardening, first slice):** added `gateway/urlpolicy.py`
  (syntactic SSRF checks: scheme, credentials, query/fragment, metadata/link-local/
  unspecified/multicast always blocked; loopback/private optional via
  `allow_private`), applied to `OllamaProvider` and `OpenAICompatibleProvider`; added
  `AuthenticationProviderError` (HTTP 401/403, permanent, never retried, message does
  not echo provider body). Not covered: DNS-rebinding defense (no resolution). Local
  verification: `ruff` clean, `pytest` 637 passed, 1 skipped (mocked HTTP only; 60 new
  tests in `tests/test_gateway_url_policy.py`).
- **Milestone 3 (provider configuration):** config-driven OpenAI-compatible providers
  (FreeLLMAPI unchanged, new Omniroute entry with no assumed URL, `MIA_EXTRA_PROVIDERS`),
  validated custom headers and timeouts, `ModelGateway.test_connection`, and model
  discovery now skips providers with rejected credentials. Omniroute's real endpoints
  and response shapes are **unverified**: the adapter assumes OpenAI-compatible
  `/models` and `/chat/completions`. Local verification: `ruff` clean, `pytest`
  705 passed, 1 skipped (mocked HTTP only).
- **Milestone 4 (model registry):** `gateway/model_registry.py`, a SQLite registry
  (`mia-models.sqlite3` in `MIA_DATA_DIR`) holding per-model capability evidence with
  separate sources (`reported`, `configured`, `tested`); effective capability is
  tested > configured > reported and unknown is never advertised. Supports manual
  registration, enable/disable, aliases, preference (-10..10), stale marking (only by
  a provider whose listing succeeded; owner settings preserved) and removal of stale
  or manual entries only. `ModelGateway.discover_models` now syncs and applies it, and
  the API app wires it in. Not yet done: runtime capability *testing* that writes
  `tested` rows, an HTTP API for registry administration, discovery caching/TTL.
  Local verification: `ruff` clean, `pytest` 718 passed, 1 skipped (mocked only).
- **Milestone 5 (routing modes, pinning, privacy, decision records):** new
  `gateway/selection.py` and a shared `ineligibility_reason` in `gateway/routing.py`
  (`rank_candidates` behaviour unchanged). Modes: `free_first` (default, identical to
  the previous order), `local_first`, `best_quality` (a heuristic proxy: preference,
  then context window, then reliability; there is no benchmark data), `lowest_latency`
  and `manual`. A pinned model goes first, with fallbacks unless disabled; if it cannot
  serve the request (missing mandatory capability, unavailable, barred by privacy) the
  override is explained in the decision. `ModelRequest.sensitive` plus
  `allow_remote_for_sensitive=False` (default) and `local_only` keep private content on
  local models, and nothing is chosen rather than leaking to a remote model. Decisions
  (selected, reason, ranked, rejected with reasons, overrides) are kept in a bounded
  in-memory `DecisionLog` and contain no prompt text. **Not yet done:** preferences are
  in-memory only (no persistence or HTTP API), no caller sets `sensitive` yet (Voice and
  Memory wiring is a later milestone), and the task-aware hint from `brain/router.py` is
  unchanged. The baseline grew to 942 tests when the rebase pulled in other work on
  `main`; after this milestone: `ruff` clean, `pytest` 957 passed, 1 skipped (mocked).
- **Milestone 6 (retry, circuit breaker, resilience events):** new
  `gateway/resilience.py` and integration in `ModelGateway.complete/stream`.
  `RetryPolicy` (default 1 attempt, i.e. unchanged behaviour; up to 5 with exponential
  backoff and jitter) retries only transient non-rate-limit failures on the same
  candidate in `complete()`; rate limits, authentication and request-specific errors
  are never retried. A per-provider `CircuitBreaker` opens after repeated transient
  failures, allows one half-open probe after the cooldown (doubling on a failed probe),
  does not count throttling as a failure, and frees the probe slot on cancellation,
  early stream close or a request-specific error. An authentication failure now pauses
  that provider for a long cooldown and falls back to the next candidate (previously
  it aborted the whole request). If every eligible provider is paused the caller gets a
  `TransientProviderError`. A bounded `ResilienceLog` records retry, failure,
  circuit and skip events without prompt content. **Not done:** streaming does not
  retry the same candidate (it falls back before the first token only); context-length
  errors still abort instead of falling back to a larger-context model; breaker and
  event state are in-memory and not exposed over HTTP yet. Local verification: `ruff`
  clean, `pytest` 974 passed, 1 skipped (mocked only).
- **Milestone 7 (streaming tool calls, cancellation, partial output):**
  `StreamChunk.tool_calls` and `StreamEvent.tool_calls` carry *whole* normalized tool
  calls on the final event (never partial fragments). `OpenAICompatibleProvider`
  assembles streamed tool-call fragments by index (bounded: 8 calls, 200k argument
  characters, invalid index rejected, nameless calls dropped); `OllamaProvider`
  collects tool calls from its stream. Providers without `streams_tools` still use one
  buffered call, which now keeps tool calls and treats a tool-only reply as valid.
  Streams are always closed (idle timeout, error, early close, task cancellation).
  Optional `stream_idle_timeout` fails a silent stream transiently (falls back before
  the first token). A failure after text was sent raises `StreamInterruptedError`
  (a `TransientProviderError`) carrying `partial_text` and rate-limit hints. **Not
  done:** the Brain orchestrator still runs tool-bearing turns through its buffered
  tool/plan pipeline, so gateway-level streaming tools are available but not yet used
  by chat; tool arguments are not yet validated against schemas (milestone 8);
  assembly against real servers is unverified (mocked SSE and NDJSON only). Local
  verification: `ruff` clean, `pytest` 1035 passed, 14 skipped (the skips are browser
  tests with no Playwright/Chromium here; none are gateway tests).
- **Milestone 8 (tool-call screening, structured output):** new
  `gateway/jsonschema_lite.py` (bounded JSON Schema subset; `pattern` is ignored to avoid
  ReDoS, `$ref` unsupported, so a pass means "satisfies the supported constraints"),
  `gateway/toolcalls.py` (`screen_tool_calls`, `parse_structured`). Model-proposed tool
  calls are checked against the tools *offered in the request*: exact name match only,
  arguments must be a JSON object that satisfies the offered schema (unknown keys
  rejected), size caps, unique ids, at most 8 calls; issue text never contains argument
  values. `ModelGateway(tool_call_policy=...)`: `annotate` (default) reports issues in
  `CompletionResult.tool_call_issues` / `StreamEvent.tool_call_issues` and passes calls
  through untouched, so the Brain broker (still the authoritative permission, registry
  and egress check) behaves as before; `filter` drops invalid calls and falls back to
  the next model when nothing valid remains. `ModelRequest.json_schema` asks for
  structured output: Ollama gets native `format`, OpenAI-compatible providers get
  `response_format` only when `MIA_<PREFIX>_ENABLE_JSON_SCHEMA=true`; `complete()`
  always validates the reply, returns `structured`, and tries the next model on
  non-conforming output without penalising the provider (the error is
  `PermanentProviderError` if none conforms); `stream()` rejects `json_schema`.
  **Not implemented on purpose:** a prompt-based tool-calling fallback for models without
  native tool calls (tools are only routed to models with the `tools` capability).
  A validator bug (error cap not enforced in loops) was caught by the new tests and
  fixed. Local verification: `ruff` clean, `pytest` 1095 passed, 16 skipped (browser
  tests lacking Playwright/Chromium; mocked providers only).
