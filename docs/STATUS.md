# Mia project status

**Last updated:** 2026-11-20  
**Repository:** muhammadnaumantahir/Mia  
**Default branch:** main  
**Overall state:** Active implementation — not release-ready

## Owner-confirmed product direction (2026-11-20)

Mia's **first product goal is natural, intelligent conversation**. The owner wants to be able to talk with Mia about Mia itself, personal topics, ideas and ongoing work, and have Mia understand context and remember previous discussions. Text and voice must share the same identity, memory and AI Brain. The first milestone includes natural multilingual conversation, reliable voice and hands-free/wake-word interaction, accurate self-description, continuity across sessions/providers, reasoning and bounded parallel model evaluation.

The product roadmap is now strictly ordered:
1. Integration and development baseline.
2. Conversation-first Mia — the first product milestone.
3. Full 3D holographic interaction.
4. Personal digital life and recommended everyday-assistant additions.
5. Secure computer intelligence and control.
6. Self-programming, custom foundation model research and governed evolution.
7. Evolution Observatory and 30-day experiment infrastructure.

The Code Graph is a required cross-cutting developer capability, initialized during the integration baseline and automatically refreshed after source changes throughout development. This sequence supersedes the older "three co-equal priorities" direction. See AGENTS.md and [the canonical roadmap](ROADMAP.md).

**Implementation truth (not a completion claim):** main now includes the core runtime integration: independent AI Gateway, bounded semantic-memory option, structured/FTS memory and multilingual deterministic capture/recall, owner-editable versioned identity and UI, shared Brain pipeline, browser/local voice engines, opt-in browser wake phrase, governed computer adapters, task runtime, proactive inbox, and Code Graph. Core integration PR #3 merged to main at `7325708`; Code Graph PR #12 and identity/multilingual memory work were included in that integration. PR #20 is the clean-main follow-up for canonical SPS self-description and multilingual conversation-evaluation fixtures, currently under fresh CI. Important gaps remain: full-history semantic retrieval, model-assisted extraction beyond conservative rules, richer relation-aware recall, stronger cross-session/provider continuity, reasoning/judge evaluation, and offline always-on wake-word detection. Physical device checks and owner deployment remain separately tracked.

## Status inventory (component status, not execution order)

The following table records prior workstream status. It is an implementation inventory, not the development sequence; docs/ROADMAP.md is authoritative for execution order.

| Workstream | Status | Evidence / next gate |
|---|---|---|
| Research and reuse audit | In progress | Source/license matrix and upstream pins recorded; complete SBOM and legal/dependency review remain |
| Foundation | Implemented foundation; cross-platform verification incomplete | SPS registry, FastAPI app, SQLite observatory, memory/task/capability stores, approval flow and dashboards exist. CI infrastructure has previously failed before runner assignment; this is not evidence of an application failure and does not block development |
| AI gateway | In progress | Ollama and OpenAI-compatible adapters, free-first router and fallback. Text streaming over SSE (/api/gateway/chat/stream), provider-reported token usage, a persistent usage/cost ledger (/api/gateway/usage) and a runtime daily spend cap (MIA_MAX_DAILY_SPEND_USD) are implemented. The assistant page streams replies token by token when the Tools toggle is off (tool-enabled chat stays buffered) and shows tokens/cost per reply; its SSE parser was exercised only with a Node harness against fake responses, not in a real browser. Ollama model discovery reads real context length and capabilities (/api/show, falling back to 8192 and owner-gated tools when unavailable); the assistant header shows today's spend with a 30-day usage tooltip (not browser-verified). Provider throttles (429, or 503 with Retry-After) block a candidate for the advised wait and other transient failures back off exponentially (GET /api/gateway/health, Retry-After on chat 503; local tests with mocked HTTP only, no live provider). Streaming tool calls, full usage dashboard, UI for health, OpenAI-compatible capability discovery and remaining-quota header tracking remain |
| AI Brain | Core implemented; live provider/browser/voice verification pending (2026-11-20) | mia.brain: task-aware explainable routing with refusal when unsuitable, runtime capability evidence, intent/reference/ambiguity analysis, budgeted memory-aware context, persisted plans with dependencies/retries/checkpoints, permission-gated tool broker, completion-claim verification, one pipeline for text/streaming/voice, diagnostics API and repeatable evaluation scenarios. Verified with mocked providers and automated tests only (no live Ollama or owner-device voice run). See docs/architecture/AI_BRAIN.md |
| Voice | Conversation pipeline implemented; owner-device verification pending | Hands-free and push-to-talk sessions, browser or local Faster-Whisper input, English/Urdu/auto language, mic selection, mute, interrupt, stop phrases, chunked spoken replies, local pyttsx3/browser TTS fallback, state UI and privacy badge. An explicit opt-in "Hey Mia" wake phrase works through the online browser speech service while the Assistant page is open; it is not offline or OS-level always-on detection. Chromium lifecycle/API tests are automated. Physical microphone/speaker, Windows SAPI voices, Faster-Whisper download/latency/Urdu accuracy and offline wake-word operation remain unverified/incomplete. Owner steps: docs/voice/OWNER_VERIFICATION.md |
| Memory and knowledge | In progress | Persistent conversations, FTS5 memory, provenance labels, document parsers and bounded checksummed JSON export for conversations and knowledge. Office DOCX/XLSX/PPTX ingestion preflights ZIP structure and resource limits. Owner-triggered retention purge and preview-then-confirm cleanup are implemented. Contextual retrieval searches prior messages and knowledge across conversations with FTS5, ranking relevance, trust and recency; retrieved context is bounded, provenance-labelled and withheld from remote models unless MIA_ALLOW_REMOTE_FILE_CONTEXT=true. Structured decision/goal/episode/project/entity/fact/preference/lesson records carry provenance, confidence, owner corrections, conflicts and audit history. Automatic capture is implemented with conservative rule patterns over owner messages: default `approval` mode queues pending suggestions, while optional `auto` mode saves only high-confidence, conflict-free candidates; explicit remember/forget commands work independently of automatic capture. Sensitive attributes, credentials, questions and uncertain/hypothetical statements are screened. Duplicate detection and owner-approved consolidation are implemented. Optional local Ollama semantic reranking exists but is disabled by default; relationship storage exists, while richer relationship-aware retrieval and full-history evaluation remain. Structured active records are not included in conversation-retention purge. The owner-editable identity backend is versioned and audited; the owner-facing editor page was merged into the development integration branch in PR #16. Remaining: model-assisted extraction for less predictable statements, richer relation-aware retrieval, structured-memory retention policy and broader hostile-document testing |
| Autonomous digital life | Partial foundations; not the first development priority | Task Desk, approved read-only task dispatch, durable curiosity backlog, goal follow-up derivation, memory-review explorer, scoped allowlisted web research, journal, quiet hours, pause/emergency-stop/budget controls, scheduler/recovery, deterministic reflection digests, opt-in inbox outreach, /life UI and explicit curiosity capture exist as described in source. Model-based reflection, wider research, broader workflows, inbox integration in main chat, push delivery and owner-device verification remain |
| Computer intelligence | Governed host-side foundation implemented; local-device bridge pending | Scoped filesystem operations, allowlisted app launch, isolated browser automation, Windows UI Automation, process inspection/termination, risk-classified terminal, owner grants, one-time approvals, stop-all and durable multi-step tasks are implemented and tested. Adapters control the machine running Mia. If Mia runs on Oracle Cloud, they control the VM, not the owner's laptop; a paired outbound-only local companion agent, stronger sandboxing and real Windows device verification remain. Keep this work lower priority until the conversation-first milestone is accepted |
| Self-programming | AI draft + Capability Lab + Docker validation foundations | Mia can draft pure-Python modules/tests through free-only model routing; reviewed drafts are stored as immutable versions, Docker-validated and registered only when passing. Permission-free candidates can run in isolated Docker with bounded JSON I/O. Owner-approved promotion/rollback records exist behind a feature flag. Permission broker, safe plugin runtime boundary and target-Windows verification remain |
| Evolution Observatory | Day 0, explicit start, host verification and optional daily scheduler foundations | Baseline capture, fixed benchmarks, resource-aware daily snapshots, activity evidence, report API/viewer and evidence-hash verification exist. No real Windows-host baseline, actual owner-run 30-day experiment or evidence-backed final results exist yet |
| Mia Code Graph | Integrated in main; CI-verified | Deterministic source-backed index, Python AST symbols/imports/calls, route/file relationships, content-digest watcher, explorer, CLI refresh/stale checks and Git-diff impact analysis. Ubuntu and Windows CI passed in PR #12 and refreshed 3,797 nodes / 7,682 edges in core integration CI. Current watcher performs full rebuilds; true file-level incremental indexing and broader language parsers remain future improvements |

## Verification and CI policy

Development must continue even when GitHub Actions is unavailable or fails before runner allocation. CI status is tracked separately and is not a development completion criterion. Do not spend repeated sessions troubleshooting runner infrastructure. Run local formatting, static checks, targeted tests and the full test suite where practical; record exact results and distinguish local results from CI. Do not claim tests passed if they were not run.

Owner-only physical microphone/speaker tests, Windows desktop/device acceptance, live cloud-provider credentials, Oracle deployment, final owner approval and the real 30-day experiment must be labelled OWNER_VERIFICATION_REQUIRED. Implement the supporting code, controls and automated tests without treating those owner actions as blockers for continued development.

## Not complete

Mia is not yet a finished conversational companion or personal AI operating system. Core runtime and Code Graph are merged into main; identity editing and deterministic English/Roman Urdu/Urdu-script memory capture and recall are integrated. PR #20 adds canonical SPS self-description and expanded multilingual conversation evaluation and is awaiting fresh-main CI. Owner-device testing remains separate. Remaining conversation-first development includes full-history semantic retrieval, model-assisted memory extraction, richer relationship-aware recall, stronger continuity, model escalation, parallel judge-model evaluation, reasoning-quality benchmarks and offline always-on wake-word detection. A paired local agent is still needed for an Oracle-hosted Mia to control the owner's Windows PC. The full 3D holographic interface, additional digital-life workflows, advanced governed self-programming, custom foundation-model research and the complete Evolution Observatory remain later roadmap work.

Do not confuse a design target, UI affordance, documentation entry or mocked test with an operational capability. No Day 0 or daily growth data may be fabricated.

## Next core implementation sequence

The next development session must select the first unfinished item in Priority 0 or Priority 1 of docs/ROADMAP.md, based on a fresh source/PR review. Within the conversation-first target, the default order is:

1. Finish and merge PR #20's clean-main SPS architecture self-description and multilingual evaluation changes after fresh Ubuntu/Windows CI passes.
2. Improve memory beyond the current deterministic baseline: full-history semantic indexing/retrieval, model-assisted extraction with owner approval, richer relation-aware recall and structured-memory retention.
3. Strengthen cross-session/project/provider continuity and repeatable reasoning-quality evaluation.
4. Add bounded parallel judge-model evaluation and quality-based model escalation, with latency/cost limits and evidence-based acceptance.
5. Complete offline local wake-word detection only after the client-side microphone/agent architecture and privacy controls are defined; browser "Hey Mia" remains opt-in and online.
6. Complete Priority 1 acceptance, then proceed to the 3D holographic interface and lower-priority digital-life/computer-control expansions.

The Code Graph is a Priority 0 cross-cutting prerequisite and should be indexed/updated as code changes. Do not start lower-priority computer-control features while the conversation-first milestone has unfinished development acceptance criteria.

## Phase completion and evidence rule

A development phase is complete when its code-side acceptance criteria are implemented and supported by proportionate local tests, static checks, security review and documented evidence. CI success is not required. Owner-only manual/device checks remain separately tracked as OWNER_VERIFICATION_REQUIRED and are not blockers for the AI to continue to the next development task. Never claim those checks have happened without owner evidence.


## 2026-11-20 development branch addendum

**Current main baseline:** `7325708ff4d99264ae75dc7826ff64ec40ad0c5d` (core runtime + Code Graph merged; conversation-first direction). PR #3 is merged. PR #20 is the current draft follow-up; its exact head must pass CI before merge.

- Independent gateway: paid budget reservations and atomic settlement, fail-closed unknown-price handling (including historical NULL-cost ledger rows), cancellation-safe reservation retention, provider health/routing improvements and console updates.
- Memory: opt-in local Ollama semantic reranking with bounded recent conversation/knowledge/structured candidates and content-addressed embedding cache. Disabled by default; no cloud embeddings or model auto-download.
- Self-description: Brain now appends a deterministic live-runtime manifest derived from registered tools, provider adapters, memory, permission broker, capability registry and available voice/Code Graph routes. It explicitly distinguishes registration from health, permission and owner verification; secret-like identifiers are filtered.
- Permissions: shared permission policy across TaskEngine, direct computer routes and Brain broker; unregistered capabilities fail closed.
- PR #12 Code Graph, PR #16 identity editor, PR #17 multilingual capture, PR #18 multilingual recall and PR #3 core integration passed Ubuntu/Windows Python 3.13 CI. PR #20 SPS self-description is undergoing a fresh-main CI run. These are CI results, not local test claims.
- PR #3 was merged into main after both platform CI jobs passed and targeted review of gateway reservations, owner permission evaluation, task execution and direct filesystem approval paths. CI is evidence, not a substitute for owner device testing.

Voice: an explicit opt-in “Hey Mia” wake phrase mode is implemented using the browser speech service, with visible Mic/listening state, mute/stop and re-arming after each turn. It is not offline wake-word detection; browser privacy disclosure and OWNER_VERIFICATION_REQUIRED steps are documented. Regression tests are added but not run.
- Conversation evaluation: a deterministic fixture and runner cover English, Urdu and Roman Urdu decision recall plus runtime self-description. This tests context assembly, not live model translation quality; tests are added but not run.

Remaining Priority 1 development includes full-history and relationship-aware retrieval/evaluation, model-assisted memory extraction beyond the current conservative rules, richer self-description of architecture/limitations, quality-based model escalation, reasoning-quality evaluation, reliable offline wake-word operation and full text/voice acceptance verification.


## Code Graph (Priority 0 cross-cutting developer capability)

Main now contains the first repository-wide Code Graph implementation: deterministic Python AST indexing, stable node/edge IDs, source digests, Git commit metadata, a browser explorer, CLI refresh/stale checks, local API endpoints and Git-diff impact analysis. The background watcher detects edits using file-content digests, including same-size edits with preserved timestamps, and atomically rebuilds the full graph. The explorer is linked from the Assistant and Observatory navigation.

**Important limits:** the watcher currently performs full graph rebuilds rather than true file-level incremental indexing. Impact analysis reports confirmed static-import relationships and likely test candidates; it cannot prove dynamic imports, dependency injection or all runtime call paths. The graph endpoints are loopback-only by default; remote access needs explicit opt-in and a strong token.

**Verification:** graph unit/API/watcher tests passed in both Ubuntu and Windows Python 3.13 CI in PR #12. The graph refresh step also runs on every subsequent integration CI run. Treat the current implementation as a verified foundation; full incremental indexing and broader language parsers remain future work.

## Conversation-first identity editor

The persistent, model-independent identity backend provides versioned owner updates, immutable history, rollback, audit events and prompt injection shared by text/streaming chat and provider switches. The owner-facing Identity page is linked from the Assistant sidebar and provides profile editing, prompt preview, version history and rollback. Route and API integration tests passed on Ubuntu and Windows Python 3.13 in PR #16, included in core PR #3 merged to main. Owner interaction testing remains separate.

## Multilingual memory capture

Automatic memory capture and explicit remember/forget commands now recognize common English, Roman Urdu and Urdu-script preference, decision, goal, project and basic profile statements. Confidence/provenance, pending-by-default owner approval, conflict handling and duplicate checks are unchanged. Sensitive-category, uncertainty and temporary-context screening includes the corresponding common Roman Urdu and Urdu terms. Regression tests cover both scripts and confirm sensitive/recent/uncertain statements remain uncaptured. Ubuntu and Windows Python 3.13 CI passed in PR #17. This is conservative pattern-based extraction, not model-assisted semantic extraction.

## Multilingual contextual recall

The memory context assembler now recognizes common Roman Urdu and Urdu-script references to prior projects/progress, decisions, preferences, problems and goals. Intent-aware retrieval can therefore pull the appropriate structured memory even when the question shares few or no keywords with the saved record. Regression tests cover both scripts and verify goal retrieval. Ubuntu and Windows Python 3.13 CI passed in PR #18. This does not claim full Urdu semantic understanding.

## SPS architecture self-description

Mia's runtime self-description now reads the canonical immutable ten-layer registry from `mia.sps.layers` and injects the exact layer IDs, names and responsibilities into Brain context. It explicitly distinguishes the SPS framework contract from implementation evidence: the metadata registry exists, but the full ten-layer implementation remains partial/in progress. Supporting runtime services are described as components, not extra SPS layers. Tests verify layer order, canonical names, responsibilities and the limitation statement. The deterministic conversation-evaluation fixture also covers Roman Urdu/Urdu goal recall, project continuity, the SPS architecture summary and the browser-versus-offline wake-phrase distinction.
