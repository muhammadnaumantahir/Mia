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

The Code Graph is a required cross-cutting developer capability, initialized during the integration baseline and incrementally updated throughout development. This sequence supersedes the older "three co-equal priorities" direction. See AGENTS.md and [the canonical roadmap](ROADMAP.md).

**Implementation truth (not a completion claim):** the repository already has persistent conversation storage, bounded FTS5 contextual retrieval, owner-managed structured memory records, a model-independent identity/context prompt, a shared AI Brain path for text/streaming/voice, hands-free browser voice and local voice foundations, an AI gateway foundation, a curiosity backlog, an activity journal, scoped read-only research foundations, inbox/outreach controls, a Capability Lab and experiment/observability foundations. Important conversation-first gaps remain: an owner-facing identity editor (the versioned identity store and owner update/history/rollback APIs already exist), model-assisted multilingual memory extraction and consolidation, full-history semantic/relationship-aware retrieval, stronger cross-session/project/provider continuity, model-assisted reflection/reasoning evaluation, robust end-to-end voice verification, multilingual conversation-quality evaluation and reliable offline always-on wake-word support. The actual status of every component must be rechecked against source/tests at the start of a development session.

## Status inventory (component status, not execution order)

The following table records prior workstream status. It is an implementation inventory, not the development sequence; docs/ROADMAP.md is authoritative for execution order.

| Workstream | Status | Evidence / next gate |
|---|---|---|
| Research and reuse audit | In progress | Source/license matrix and upstream pins recorded; complete SBOM and legal/dependency review remain |
| Foundation | Implemented foundation; cross-platform verification incomplete | SPS registry, FastAPI app, SQLite observatory, memory/task/capability stores, approval flow and dashboards exist. CI infrastructure has previously failed before runner assignment; this is not evidence of an application failure and does not block development |
| AI gateway | In progress | Ollama and OpenAI-compatible adapters, free-first router and fallback. Text streaming over SSE (/api/gateway/chat/stream), provider-reported token usage, a persistent usage/cost ledger (/api/gateway/usage) and a runtime daily spend cap (MIA_MAX_DAILY_SPEND_USD) are implemented. The assistant page streams replies token by token when the Tools toggle is off (tool-enabled chat stays buffered) and shows tokens/cost per reply; its SSE parser was exercised only with a Node harness against fake responses, not in a real browser. Ollama model discovery reads real context length and capabilities (/api/show, falling back to 8192 and owner-gated tools when unavailable); the assistant header shows today's spend with a 30-day usage tooltip (not browser-verified). Provider throttles (429, or 503 with Retry-After) block a candidate for the advised wait and other transient failures back off exponentially (GET /api/gateway/health, Retry-After on chat 503; local tests with mocked HTTP only, no live provider). Streaming tool calls, full usage dashboard, UI for health, OpenAI-compatible capability discovery and remaining-quota header tracking remain |
| AI Brain | Core implemented; live provider/browser/voice verification pending (2026-11-20) | mia.brain: task-aware explainable routing with refusal when unsuitable, runtime capability evidence, intent/reference/ambiguity analysis, budgeted memory-aware context, persisted plans with dependencies/retries/checkpoints, permission-gated tool broker, completion-claim verification, one pipeline for text/streaming/voice, diagnostics API and repeatable evaluation scenarios. Verified with mocked providers and automated tests only (no live Ollama or owner-device voice run). See docs/architecture/AI_BRAIN.md |
| Voice | Implemented foundations; real-device and wake-word work remains | Hands-free and push-to-talk sessions, browser or local Faster-Whisper engine, English/Urdu/auto language, mic device selection, mute, interrupt, stop phrases, chunked spoken replies with speed/volume, local pyttsx3 and browser TTS fallback, state UI and privacy badge. Prior Chromium lifecycle tests and backend tests were recorded; physical microphone/speaker, Windows SAPI voices, Faster-Whisper model download/latency/Urdu accuracy and always-on wake word remain unverified or incomplete. Owner steps: docs/voice/OWNER_VERIFICATION.md |
| Memory and knowledge | In progress | Persistent conversations, FTS5 memory, provenance labels, document parsers and bounded checksummed JSON export for conversations and knowledge. Office DOCX/XLSX/PPTX ingestion preflights ZIP structure and resource limits. Owner-triggered retention purge and preview-then-confirm cleanup are implemented. Contextual retrieval is implemented for chat: knowledge items and past messages from any conversation are retrieved by FTS5, ranked by relevance, trust and recency, and injected as a bounded provenance-labelled block behind a canonical model-independent identity prompt; the block only reaches local models unless MIA_ALLOW_REMOTE_FILE_CONTEXT=true. Structured decision/goal/episode/project/entity records (/api/memory/records) carry provenance, confidence, owner corrections and competing-claim detection; the assistant sidebar provides a read-only view and replies display retrieved source references. Conservative rule-based automatic capture is implemented with explicit owner commands, confidence/provenance, duplicate/conflict checks and off/approval/auto modes (approval is the default). Model-assisted multilingual extraction and consolidation are not implemented; structured records are not covered by retention purge. The integration branch adds Urdu/Roman Urdu explicit remember/forget commands and Unicode-aware similarity, with tests added but not yet run. Remaining: owner-facing identity editor, semantic/relationship-aware full-history retrieval and broader hostile-document testing |
| Autonomous digital life | Partial foundations; not the first development priority | Task Desk, approved read-only task dispatch, durable curiosity backlog, goal follow-up derivation, memory-review explorer, scoped allowlisted web research, journal, quiet hours, pause/emergency-stop/budget controls, scheduler/recovery, deterministic reflection digests, opt-in inbox outreach, /life UI and explicit curiosity capture exist as described in source. Model-based reflection, wider research, broader workflows, inbox integration in main chat, push delivery and owner-device verification remain |
| Computer intelligence | In progress; deliberately lower priority | Scoped filesystem, approval-bound writes, allowlisted app launch, browser and Windows visible-window/text-control adapter; stronger OS sandbox, centralized permission broker and target-PC verification remain. Do not expand this work before Priority 1 in the roadmap is complete |
| Self-programming | AI draft + Capability Lab + Docker validation foundations | Mia can draft pure-Python modules/tests through free-only model routing; reviewed drafts are stored as immutable versions, Docker-validated and registered only when passing. Permission-free candidates can run in isolated Docker with bounded JSON I/O. Owner-approved promotion/rollback records exist behind a feature flag. Permission broker, safe plugin runtime boundary and target-Windows verification remain |
| Evolution Observatory | Day 0, explicit start, host verification and optional daily scheduler foundations | Baseline capture, fixed benchmarks, resource-aware daily snapshots, activity evidence, report API/viewer and evidence-hash verification exist. No real Windows-host baseline, actual owner-run 30-day experiment or evidence-backed final results exist yet |
| Mia Code Graph | Required; status not yet verified | Inspect repository for existing graph/indexer work before creating anything. Build a source-backed graph, interactive explorer, incremental local updates and change/impact history. Do not duplicate an existing implementation |

## Verification and CI policy

Development must continue even when GitHub Actions is unavailable or fails before runner allocation. CI status is tracked separately and is not a development completion criterion. Do not spend repeated sessions troubleshooting runner infrastructure. Run local formatting, static checks, targeted tests and the full test suite where practical; record exact results and distinguish local results from CI. Do not claim tests passed if they were not run.

Owner-only physical microphone/speaker tests, Windows desktop/device acceptance, live cloud-provider credentials, Oracle deployment, final owner approval and the real 30-day experiment must be labelled OWNER_VERIFICATION_REQUIRED. Implement the supporting code, controls and automated tests without treating those owner actions as blockers for continued development.

## Not complete

Mia is not yet a finished conversational companion or personal AI operating system. In particular, the repository still needs verified owner-editable identity, automatic memory extraction and consolidation, semantic/relationship-aware retrieval, stronger continuity across model/provider changes, multilingual conversation evaluation, reliable always-on wake-word operation, parallel judge-model evaluation, and end-to-end conversational quality verification. The full 3D holographic interface, recommended digital-life features, secure computer-control runtime, advanced governed self-programming, custom foundation-model research and the complete Evolution Observatory remain future roadmap work.

Do not confuse a design target, UI affordance, documentation entry or mocked test with an operational capability. No Day 0 or daily growth data may be fabricated.

## Next core implementation sequence

The next development session must select the first unfinished item in Priority 0 or Priority 1 of docs/ROADMAP.md, based on a fresh source/PR review. Within the conversation-first target, the default order is:

1. Audit and integrate the current chat, AI Brain, voice, memory and gateway changes; inspect actual open PRs (including PR #3 if it exists) and fix application-level regressions.
2. Complete a truthful, model-independent Mia identity and live capability description.
3. Complete cross-session memory: owner-editable identity, automatic owner-approved extraction, provenance/confidence, semantic retrieval, consolidation, correction and retention.
4. Verify one coherent context/reasoning path for text, streaming and voice; improve continuity across model/provider changes.
5. Complete natural multilingual voice conversations, turn-taking, interruption, wake-word/hands-free controls and truthful listening/privacy state.
6. Add bounded parallel judge-model evaluation, model escalation and repeatable conversational quality benchmarks.
7. Complete the Priority 1 acceptance suite and update the evidence/status before moving to the 3D holographic interface.

The Code Graph is a Priority 0 cross-cutting prerequisite and should be indexed/updated as code changes. Do not start lower-priority computer-control features while the conversation-first milestone has unfinished development acceptance criteria.

## Phase completion and evidence rule

A development phase is complete when its code-side acceptance criteria are implemented and supported by proportionate local tests, static checks, security review and documented evidence. CI success is not required. Owner-only manual/device checks remain separately tracked as OWNER_VERIFICATION_REQUIRED and are not blockers for the AI to continue to the next development task. Never claim those checks have happened without owner evidence.


## 2026-11-20 development branch addendum

**Current main:** `d8d00fc420d78b0c4420a280fadfb15342db78ef` (conversation-first direction). The following work is committed to draft PR #3 but is not yet merged:

- Independent gateway: paid budget reservations and atomic settlement, fail-closed unknown-price handling (including historical NULL-cost ledger rows), cancellation-safe reservation retention, provider health/routing improvements and console updates.
- Memory: opt-in local Ollama semantic reranking with bounded recent conversation/knowledge/structured candidates and content-addressed embedding cache. Disabled by default; no cloud embeddings or model auto-download.
- Self-description: Brain now appends a deterministic live-runtime manifest derived from registered tools, provider adapters, memory, permission broker, capability registry and available voice/Code Graph routes. It explicitly distinguishes registration from health, permission and owner verification; secret-like identifiers are filtered.
- Permissions: shared permission policy across TaskEngine, direct computer routes and Brain broker; unregistered capabilities fail closed.
- Regression tests have been added, but the latest gateway, semantic-memory and permission changes have **not been run locally**. Historical test counts in earlier status entries do not verify this current diff.
- PR #3 remains draft. No changes from it have been merged into main. CI is reported separately and is not a development gate.

Voice: an explicit opt-in “Hey Mia” wake phrase mode is implemented using the browser speech service, with visible Mic/listening state, mute/stop and re-arming after each turn. It is not offline wake-word detection; browser privacy disclosure and OWNER_VERIFICATION_REQUIRED steps are documented. Regression tests are added but not run.
- Conversation evaluation: a deterministic fixture and runner cover English, Urdu and Roman Urdu decision recall plus runtime self-description. This tests context assembly, not live model translation quality; tests are added but not run.

Remaining Priority 1 development includes full-history semantic retrieval/evaluation, model-assisted memory extraction/consolidation, richer self-description of architecture/limitations, quality-based model escalation, reasoning-quality evaluation and full text/voice acceptance verification.


## Code Graph implementation status — 2026-11-20

The Code Graph foundation is now ported onto `integration/code-graph-core`, which is based on the current core-runtime development branch. This integration branch is **not merged into main**.

Implemented in this branch:
- Deterministic repository/file/directory and Python AST symbol indexing with stable IDs, source digests, commit metadata, atomic graph output and parser-error reporting.
- Confirmed import/declaration/same-file call/API/config-file relationships; inferred SPS-layer and test-name relationships remain explicitly labelled.
- Secret/private-key exclusions, conservative circular-import and unreferenced-candidate diagnostics.
- Local explorer, search and relationship/evidence filters, graph APIs, CLI refresh/stale-check and automatic background rebuild after source changes.
- Loopback-only graph data endpoints by default; remote access requires explicit opt-in and a strong token.
- Regression tests for graph indexing, watcher refresh, API access control, nested API-route symbol mapping and private-key exclusion.

Important limits: the watcher currently rebuilds the complete graph after detecting changes; true incremental file-level indexing remains open. Basic Git-diff impact analysis is now available through the API and explorer: it reports changed paths, transitive confirmed import dependents and tests connected by inferred TESTED_BY edges. It is conservative, not a complete execution impact map. Python is the only language with symbol-level parsing. Dynamic dependency injection, runtime call traces, ORM/database relationships, permission/workflow attribution and richer history comparison are not yet resolved.

Verification truth: the integration was inspected and targeted defects were corrected, but the Code Graph and full core-runtime test suites have **not been run locally** in this environment. Do not treat the implementation as verified or merge-ready until proportionate local checks are completed.


## Conversation memory multilingual increment — 2026-11-20

The current core-runtime branch already contains a versioned, persistent identity profile with owner-only update, history and rollback APIs; the Assistant UI does not yet expose a dedicated identity editor. Memory capture is not wholly absent: conservative rule-based automatic suggestions, explicit remember/forget/update commands, confidence/provenance, duplicate/conflict checks and owner-controlled off/approval/auto modes exist (approval is the default).

The `integration/code-graph-core` branch adds explicit Roman Urdu and Urdu remember/forget command recognition, Unicode-aware similarity tokens and tests for cross-language commands. These changes are committed but have not been run locally and are not yet merged into the core-runtime branch. Model-assisted multilingual extraction and consolidation remain open.
