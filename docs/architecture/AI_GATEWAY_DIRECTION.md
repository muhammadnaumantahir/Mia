# Mia Independent AI Gateway — Product and Architecture Direction

**Status:** Owner-approved architectural direction  
**Applies to:** All future AI gateway design, implementation, tests, documentation and deployment.

## Non-negotiable decision

Mia must own its AI gateway, model intelligence, routing policy, provider lifecycle and observability. **FreeLLMAPI and OmniRoute must not be runtime dependencies, sidecars, required services, or the control plane for Mia.** Do not build Mia around either project, and do not require either to be installed or running.

Mia may study their public implementations and reuse isolated ideas or code only after checking the exact source, version, license, security posture, maintenance state and dependency impact. Such research must not create a runtime dependency. Prefer native Mia modules and small provider adapters behind Mia-owned contracts.

This decision supersedes older references in the README, gateway notes, roadmap, integration matrix, environment examples and implementation plans that describe FreeLLMAPI as the preferred gateway, a required sidecar, or OmniRoute as a supported built-in gateway. Update conflicting documentation/configuration when encountered. Do not silently retain obsolete setup instructions.

## Ownership boundary

Mia owns:
- Stable provider and inference interfaces.
- Provider adapters, authentication integration and connection tests.
- Provider and model discovery/registry, metadata and capability catalog.
- Task classification, routing policy, candidate ranking and escalation.
- Retry, fallback, circuit-breaker, health and quota-aware scheduling.
- Load balancing, concurrency control and request lifecycle.
- Credential references, encryption integration, redaction and key rotation for legitimate security maintenance (never quota evasion).
- Token accounting, price metadata, request/daily/monthly budgets and spend enforcement.
- Streaming, cancellation, structured errors and idempotency safeguards.
- Prompt/context assembly, context-window management and optional semantic caching.
- Multimodal capability negotiation for text, image, audio, video and embeddings when supported.
- Quality/latency/cost benchmarking, route evaluation and evidence-backed policy tuning.
- Traces, metrics, audit events, admin controls and operational diagnostics.

External providers supply inference only. They must not own Mia's routing decisions, identity, memory, agent orchestration, policy or usage ledger.

## Required architecture

Implement these as modular Mia-owned components behind explicit contracts. Keep the core lightweight and optional integrations isolated.

1. **Provider Registry and Adapter SDK**
   - Provider lifecycle: register, validate, enable/disable, test, update and remove.
   - Common chat, completion/response, streaming, embeddings and multimodal contracts where the upstream supports them.
   - Native adapters for local Ollama and direct cloud-provider APIs; generic OpenAI-compatible adapter for compatible endpoints.
   - Per-provider feature/capability declarations; never assume every endpoint implements the full protocol.
2. **Model Intelligence Registry**
   - Discover models and retain provider/model identifiers, model family, release metadata, context limits, modalities, tool calling, structured output, reasoning, embedding dimensions, deprecation state, region, licence/terms references and timestamps.
   - Record input/output prices, quota/rate limits and free-tier status as time-sensitive sourced metadata. Unknown values remain unknown, never zero.
   - Separate model weights' licence from the hosted API provider's terms.
3. **Policy and Intelligent Router**
   - Route by task type, required capabilities, context size, data sensitivity, quality evidence, latency, availability, quota and cost.
   - Support deterministic rules, weighted policies and owner-defined profiles.
   - Small/cheap-first with escalation only when confidence, task complexity or validation results justify it.
   - Respect allowlists, denylists, region/data-residency restrictions and provider-specific terms.
   - Explain every decision with candidate scores/reasons and policy version.
4. **Resilience and Traffic Management**
   - Health checks, circuit breakers, exponential backoff, Retry-After compliance, bounded retries, concurrency/rate controls and weighted/latency-aware load balancing.
   - Fallback only for eligible transient failures and before streamed output begins. Never retry non-idempotent tool actions blindly.
   - Do not rotate identities, accounts or keys to bypass upstream quotas or restrictions.
5. **Security, Privacy and Governance**
   - Store secrets through OS credential manager or a dedicated encrypted secret store; never commit, log or expose credentials to models/tools/generated code.
   - Per-provider and per-agent permissions, data-egress labels, redaction, request size limits and explicit remote-context policy.
   - Paid inference disabled by default. Unknown pricing, malformed budgets or unavailable cost estimates fail closed for paid routes.
   - Enforce per-request, daily and monthly budgets from an append-only usage ledger; do not enable paid routing until all configured limits are enforced and tested.
6. **Context, Cache and Execution**
   - Token estimation with provider-reported usage preferred; mark estimates clearly.
   - Context selection/compression with provenance and sensitivity filtering.
   - Optional exact and semantic response caching with TTL, namespace isolation, model/prompt/policy versioning and cache bypass for sensitive or non-deterministic requests.
   - Streaming deltas, cancellation, timeouts and structured completion metadata.
7. **Quality and Evaluation**
   - Benchmark model candidates by task class for quality, tool success, latency, throughput, reliability and cost.
   - Use reproducible test sets and versioned evaluators; never invent quality scores for newly discovered models.
   - Optional parallel execution and judge/reranker workflows only when explicitly selected, budgeted and safe for the task.
   - Keep online routing feedback evidence-backed and reversible; do not let model-generated feedback rewrite governance policy.
8. **Observability and Owner Console**
   - Per-request trace: task, selected provider/model, alternatives, routing rationale, policy version, timings, tokens, cost, cache result, retries, fallback and outcome.
   - Provider/model health, quota, usage, budget, latency and benchmark dashboards.
   - Owner controls for provider credentials, enablement, model aliases, routing profiles, budgets, data-egress and audit export.

## Cloud open-weight model support

Mia should be able to use cloud-hosted open-weight models without downloading model weights to the user's computer or Oracle VM. A cloud model is accessed through the provider's hosted inference API. The registry must distinguish:
- Open weights versus OSI-approved open-source software.
- Model licence versus inference provider terms.
- Free promotional quota versus a guaranteed free service.
- Total parameters versus active parameters for MoE models, when known.
- Provider-hosted inference versus self-hosted local inference.

Support providers through direct adapters and the generic compatible adapter. Provider availability, free quotas, model IDs, prices and regional access are dynamic; discover and validate them rather than hardcoding assumptions.

## Delivery principles

- Preserve the existing ten SPS layers; the gateway is a supporting service, not an eleventh layer.
- Keep provider integrations replaceable and model-agnostic.
- Local-only usage must continue working without cloud credentials.
- No provider or external gateway is mandatory.
- Add contract tests, adapter tests, routing-policy tests, fault-injection tests, security tests and budget-enforcement tests.
- Document implemented versus planned behavior honestly; documentation alone is not implementation evidence.
- Before adopting any upstream code, record URL, exact commit/version, licence, files reused, notices, vulnerabilities and verification results in the research register/SBOM.
- Do not copy whole gateway projects or import their full dependency stacks when a small adapter or native component is sufficient.

## Acceptance criteria

The direction is satisfied only when:
- Mia starts and routes local inference without FreeLLMAPI or OmniRoute installed or running.
- Direct provider adapters can be enabled independently.
- Routing, fallback, health, usage and budget policy execute inside Mia.
- Model discovery and metadata provenance are visible and refreshable.
- Paid routes remain blocked unless explicitly enabled and all spend limits are enforceable.
- A provider outage cannot silently cause unsafe duplicate actions or uncontrolled spend.
- Tests demonstrate routing rationale, data-egress controls, credential redaction and budget enforcement.
