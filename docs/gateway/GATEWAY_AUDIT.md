# AI Gateway audit (milestone 1)

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
