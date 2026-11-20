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

**Implementation truth (not a completion claim):** the repository already has persistent conversation storage, bounded FTS5 contextual retrieval, conservative rule-based automatic memory capture (owner approval is the default), owner-managed structured memory records, versioned model-independent identity, a shared AI Brain path for text/streaming/voice, browser-based hands-free voice and an opt-in browser speech wake phrase, local voice foundations, an independent gateway foundation, curiosity backlog, activity journal, scoped read-only research, inbox/outreach controls, Capability Lab and experiment/observability foundations. The owner-facing identity editor is implemented in draft PR #16. Important conversation-first gaps remain: model-assisted extraction of less predictable memories, full-history and relationship-aware retrieval, stronger cross-session/project/provider continuity, model-assisted reflection/reasoning evaluation, end-to-end voice and multilingual quality verification, and robust offline/always-on wake-word operation. Recheck source and tests before making any completion claim.

## Status inventory (component status, not execution order)

The following table records prior workstream status. It is an implementation inventory, not the development sequence; docs/ROADMAP.md is authoritative for execution order.

| Workstream | Status | Evidence / next gate |
|---|---|---|
| Research and reuse audit | In progress | Source/license matrix and upstream pins recorded; complete SBOM and legal/dependency review remain |
| Foundation | Implemented foundation; cross-platform verification incomplete | SPS registry, FastAPI app, SQLite observatory, memory/task/capability stores, approval flow and dashboards exist. CI infrastructure has previously failed before runner assignment; this is not evidence of an application failure and does not block development |
| AI gateway | In progress | Ollama and OpenAI-compatible adapters, free-first router and fallback. Text streaming over SSE (/api/gateway/chat/stream), provider-reported token usage, a persistent usage/cost ledger (/api/gateway/usage) and a runtime daily spend cap (MIA_MAX_DAILY_SPEND_USD) are implemented. The assistant page streams replies token by token when the Tools toggle is off (tool-enabled chat stays buffered) and shows tokens/cost per reply; its SSE parser was exercised only with a Node harness against fake responses, not in a real browser. Ollama model discovery reads real context length and capabilities (/api/show, falling back to 8192 and owner-gated tools when unavailable); the assistant header shows today's spend with a 30-day usage tooltip (not browser-verified). Provider throttles (429, or 503 with Retry-After) block a candidate for the advised wait and other transient failures back off exponentially (GET /api/gateway/health, Retry-After on chat 503; local tests with mocked HTTP only, no live provider). Streaming tool calls, full usage dashboard, UI for health, OpenAI-compatible capability discovery and remaining-quota header tracking remain |
| AI Brain | Core implemented; live provider/browser/voice verification pending (2026-11-20) | mia.brain: task-aware explainable routing with refusal when unsuitable, runtime capability evidence, intent/reference/ambiguity analysis, budgeted memory-aware context, persisted plans with dependencies/retries/checkpoints, permission-gated tool broker, completion-claim verification, one pipeline for text/streaming/voice, diagnostics API and repeatable evaluation scenarios. Verified with mocked providers and automated tests only (no live Ollama or owner-device voice run). See docs/architecture/AI_BRAIN.md |
| Voice | Implemented foundations; real-device and wake-word work remains | Hands-free and push-to-talk sessions, browser or local Faster-Whisper engine, English/Urdu/auto language, mic device selection, mute, interrupt, stop phrases, chunked spoken replies with speed/volume, local pyttsx3 and browser TTS fallback, state UI and privacy badge. Prior Chromium lifecycle tests and backend tests were recorded; physical microphone/speaker, Windows SAPI voices, Faster-Whisper model download/latency/Urdu accuracy and always-on wake word remain unverified or incomplete. Owner steps: docs/voice/OWNER_VERIFICATION.md |
| Memory and knowledge | In progress | Persistent conversations, FTS5 memory, provenance labels, document parsers and bounded checksummed JSON export for conversations and knowledge. Office DOCX/XLSX/PPTX ingestion preflights ZIP structure and resource limits. Owner-triggered retention purge and preview-then-confirm cleanup are implemented. Contextual retrieval searches prior messages and knowledge across conversations with FTS5, ranking relevance, trust and recency; retrieved context is bounded, provenance-labelled and withheld from remote models unless MIA_ALLOW_REMOTE_FILE_CONTEXT=true. Structured decision/goal/episode/project/entity/fact/preference/lesson records carry provenance, confidence, owner corrections, conflicts and audit history. Automatic capture is implemented with conservative rule patterns over owner messages: default `approval` mode queues pending suggestions, while optional `auto` mode saves only high-confidence, conflict-free candidates; explicit remember/forget commands work independently of automatic capture. Sensitive attributes, credentials, questions and uncertain/hypothetical statements are screened. Duplicate detection and owner-approved consolidation are implemented. Optional local Ollama semantic reranking exists but is disabled by default; relationship storage exists, while richer relationship-aware retrieval and full-history evaluation remain. Structured active records are not included in conversation-retention purge. The owner-editable identity backend is versioned and audited; the editor page is in draft PR #16. Remaining: model-assisted extraction for less predictable statements, richer relation-aware retrieval, structured-memory retention policy and broader hostile-document testing |
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

Remaining Priority 1 development includes full-history and relationship-aware retrieval/evaluation, model-assisted memory extraction beyond the current conservative rules, richer self-description of architecture/limitations, quality-based model escalation, reasoning-quality evaluation, reliable offline wake-word operation and full text/voice acceptance verification.


## Code Graph (Priority 0 cross-cutting developer capability)

The integration branch now contains the first repository-wide Code Graph implementation: deterministic Python AST indexing, stable node/edge IDs, source digests, Git commit metadata, a browser explorer, CLI refresh/stale checks, local API endpoints and Git-diff impact analysis. The background watcher detects edits using file-content digests, including same-size edits with preserved timestamps, and atomically rebuilds the full graph. The explorer is linked from the Assistant and Observatory navigation.

**Important limits:** the watcher currently performs full graph rebuilds rather than true file-level incremental indexing. Impact analysis reports confirmed static-import relationships and likely test candidates; it cannot prove dynamic imports, dependency injection or all runtime call paths. The graph endpoints are loopback-only by default; remote access needs explicit opt-in and a strong token.

**Verification:** graph unit/API/watcher tests are included in the integration branch, but they have not yet passed a complete CI run. Treat this as implemented foundation awaiting automated verification, not as a completed acceptance gate. The graph must refresh and be used for impact review throughout subsequent development.

## Conversation-first identity editor

The persistent, model-independent identity backend already provides versioned owner updates, immutable history, rollback, audit events and prompt injection shared by text/streaming chat and provider switches. This branch adds the missing owner-facing Identity page, linked from the Assistant sidebar, with profile editing, prompt preview, version history and rollback. A route smoke test is included. Automated verification for this UI addition is pending CI; do not treat the feature as fully accepted until its route and interaction tests pass.

## Multilingual memory capture

Automatic memory capture and explicit remember/forget commands now recognize common English, Roman Urdu and Urdu-script preference, decision, goal, project and basic profile statements. Confidence/provenance, pending-by-default owner approval, conflict handling and duplicate checks are unchanged. Sensitive-category, uncertainty and temporary-context screening includes the corresponding common Roman Urdu and Urdu terms. Regression tests cover both scripts and confirm sensitive/recent/uncertain statements remain uncaptured. Automated CI verification is pending; this is conservative pattern-based extraction, not model-assisted semantic extraction.

## Multilingual contextual recall

The memory context assembler now recognizes common Roman Urdu and Urdu-script references to prior projects/progress, decisions, preferences, problems and goals. Intent-aware retrieval can therefore pull the appropriate structured memory even when the question shares few or no keywords with the saved record. Regression tests cover both scripts and verify goal retrieval. Automated CI verification is pending; this does not claim full Urdu semantic understanding.
