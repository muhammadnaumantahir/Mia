# Mia project status

**Last updated:** 2026-11-21  
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

**Implementation truth (not a completion claim):** main includes the independent Mia-owned AI Gateway, structured/FTS memory, full-retained-history semantic candidate retrieval, bounded two-hop relationship-aware recall, conservative deterministic capture plus optional local-Ollama model-assisted extraction (suggestions require owner approval), owner-editable versioned identity, shared Brain pipeline, browser/local voice engines, opt-in browser wake phrase, governed computer adapters, task runtime, proactive inbox and Code Graph. PR #20 was merged to main at `58d6d1f` and adds canonical SPS self-description plus multilingual conversation-evaluation fixtures. Remaining Priority 1 gaps include stronger end-to-end continuity across providers/projects, reasoning-quality evaluation, bounded parallel judge/model escalation and offline always-on wake-word detection. Tests added in recent direct-main commits have not been run in this environment; physical device checks and owner deployment remain separately tracked.

## Latest development update (2026-11-21)

- Fixed a cross-session intent-analysis privacy gap: temporary conversations no longer count as prior persistent context when the Brain decides whether a vague continuation request can be resolved.
- Added regression coverage for temporary-only history, persistent prior history, Urdu-script and Roman Urdu continuation, past-issue, past-decision and preference references.
- Added a Brain integration regression test that disables the first provider between turns and verifies a second provider receives the original conversation context from the shared persistent conversation store. This verifies provider-independent context storage, not live model answer quality.
- Expanded the deterministic Brain evaluation set with calibrated uncertainty, explicit owner-correction acknowledgement without falsely claiming persistent mutation, and rejection of unsupported memory citations. Evaluation records now include verifier confidence and can assert an expected confidence level.
- Extended deterministic result verification to recognize common Roman Urdu and Urdu-script claims of completed file actions and repository inspection, plus common uncertainty phrases. Added parameterized regression cases; this improves language coverage but is not a general Urdu language parser.
- Changes are committed directly to `main`. Tests are authored but have not been executed in this environment; no CI run was used.
- Code Graph snapshot refresh remains a local development step and is not claimed as complete by these remote source edits.
- PR #20 is merged; older notes below that say it is awaiting CI are historical and superseded by this current status.

## Voice update - MIA-VOICE-01 (agent A4, 2026-11-21)

- **Implemented and tested locally:** an offline wake-word core in `src/mia/voice/wake.py` (replaceable detector contract; session that is off by default, needs an explicit owner opt-in, is always time-bounded, stops immediately and drops late audio; threshold/patience/cooldown/activation-storm guards; no audio retention; fail-closed scoring; optional openWakeWord adapter that takes an owner-supplied model and never downloads one) and a truthful `offline_wake_word` capability report in `GET /api/voice/status`.
- **Not operational:** there is **no audio transport and no UI**, so Mia does **not** have an offline wake word. The status report says `operational: false` / `audio_transport_not_available`. The browser "Hey Mia" phrase is unchanged (online, page must stay open).
- **Audit findings (verified 2026-11-21, see `docs/voice/WAKE_WORD_PLAN.md`):** openWakeWord code is Apache-2.0 but its **pre-trained models are CC BY-NC-SA 4.0 (non-commercial)**; wake words are English-only; on Linux with Python 3.13 `pip install openwakeword` fails because upstream pins `tflite-runtime` (a `--no-deps` install with onnxruntime works; Windows uses ONNX).
- **Evidence actually run:** `tests/test_voice_wake.py` (51 tests) and new `tests/test_voice_api.py` cases pass; a 9-mutant check of the safety properties was caught 9/9; full suite excluding the Chromium-based files (`test_voice_browser.py`, `test_browser_automation.py`, not run - no Chromium here): 1330 passed, 2 skipped, 0 failed; `ruff check` clean on touched files. The adapter and session were run once against the real openWakeWord engine in a scratch environment (about 3-5 ms CPU per 80 ms frame on a 2-core Linux sandbox; 0 activations on 20 s of synthetic noise).
- **Not verified:** detection of real speech, false-accept and missed-wake rates, Windows install and CPU use, any "Hey Mia" model - `OWNER_VERIFICATION_REQUIRED` (`docs/voice/OWNER_VERIFICATION.md` section G). Synthetic noise is not evidence of false-accept performance.
- **Follow-ups on the board:** MIA-VOICE-02 (loopback transport), MIA-BRAIN-04 (self-description must not infer a wake word from route names), MIA-UX-02 (UI), MIA-VOICE-03 (Urdu / Roman Urdu stop commands; today English-only).
- Code Graph: refreshed locally with `python scripts/update_code_graph.py` (3,936 nodes, 7,924 edges, 0 parse errors; includes `voice/wake.py`); the tracked `docs/code-graph/CHANGELOG.md` entry was deliberately not committed to avoid cross-agent conflicts and wrong-commit attribution.

## Voice update - MIA-VOICE-02 (agent A4, 2026-11-21)

- **Implemented and tested locally:** `src/mia/voice/wake_transport.py`, a loopback-only WebSocket/REST transport for the offline wake word (strict owner opt-in message, same-origin and no-proxy checks, one connection, stop on message/REST/socket-close/stall/time limit, metadata-only events). 31 tests, including a real uvicorn server with a real WebSocket client; a 10-mutant check of the guards was caught 10/10. Full suite excluding the Chromium files: 1676 passed, 2 skipped, 0 failed.
- **Still not operational:** the routes are deliberately **not registered** in `create_app` (waits for MIA-BRAIN-05 so Mia cannot describe a route as a working wake word), and there is no UI (MIA-UX-04) and no real-speech verification (`OWNER_VERIFICATION_REQUIRED`).

## A6 / MIA-UX-01 — Stop generating (2026-11-21)

- The Assistant page now has a **Stop ■** control (and Esc, after speech interruption takes priority) that cancels an in-flight text reply through an `AbortController`. A streamed reply keeps the text that had already arrived and is labelled "Stopped by you … partial reply"; a partial reply is never spoken aloud. If the tool-enabled (buffered) request is stopped, Mia says the server may already have processed it. The status line (`#hint`) is now an `aria-live` region.
- Evidence: `tests/test_assistant_stop_reply.py` (contract checks + a Node harness that runs the real `streamReply` against a fake streaming `fetch`; the five new tests fail against the previous page). Full local run on this change: 1340 tests, 1338 passed, 1 skipped, 1 failed — the failure is `tests/test_browser_automation.py::test_browser_api_end_to_end`, which now hits the owner-grant requirement for `browser.fill`; it is unrelated to this change and filed as MIA-FIX-01. `ruff check src tests` clean.
- Not verified: no real-browser, screen-reader or touch-device run (OWNER_VERIFICATION_REQUIRED); the server-side effect of a client disconnect (whether a partial reply is persisted, and ledger settlement) was not traced or tested here.
- Further UX gaps found in source, filed as MIA-UX-03: the failure hint always says "Check that Ollama or an enabled provider is running" whatever the error was; no retry action after a failed reply; `#messages` is `aria-live="polite"` and so may announce every streamed token to a screen reader (unverified with assistive technology); the tool-enabled path is buffered with no progress indication.

## A6 / MIA-UX-03 — Error recovery in the Assistant (2026-11-21)

- Failure hints are now specific: network failure, permission/approval block, spending or rate limit, and provider unavailability each get their own guidance (HTTP status and message are read from the existing API; no contract change). The old unconditional "Check that Ollama…" hint is gone, so a permission or budget error no longer sends the owner to restart a provider. A failed reply offers **Retry**, labelled as sending the message again (it adds a new message; it does not undo anything). `#messages` is `aria-busy` while a reply is in flight.
- Evidence: `tests/test_assistant_error_recovery.py` (5 tests, all failing against the previous page; includes a Node harness running the real `chatErrorHint`). Full local run: 1401 tests, 1399 passed, 1 skipped, 1 failed (`test_browser_api_end_to_end`, MIA-FIX-01, pre-existing). `ruff check src tests` clean.
- Not verified: real browser, screen reader (the `aria-busy` change follows the standard pattern but was not tried with assistive technology), and the error classes against live providers — classification is by status code and message text only.

## A6 / MIA-UX-02 — Project control in the Assistant (2026-11-21)

- The sidebar has an opt-in **Project (optional)** control: it shows the current conversation's project, sets or clears it through `POST /api/conversations/{id}/project` (server validation errors are shown), is disabled until the conversation exists, and the conversation list shows each chat's project. Its help text states only what A1's recall does: with a project, that project's other conversations are recalled first and other projects' conversations are never recalled into it; without one, the newest other saved conversation is recalled. Structured memory records are **not** project-scoped (MIA-BRAIN-04a), and the UI does not claim otherwise.
- Evidence: `tests/test_assistant_project_control.py` (4 tests, all failing against the previous page; includes a Node harness running the real control functions against a fake DOM/fetch). Full local run: 1696 tests, 1694 passed, 1 skipped, 1 failed. `ruff check` clean on the new test file.
- Not verified: real browser rendering, keyboard/screen-reader behaviour, narrow-screen layout of the new sidebar row.

## Status inventory (component status, not execution order)

The following table records prior workstream status. It is an implementation inventory, not the development sequence; docs/ROADMAP.md is authoritative for execution order.

| Workstream | Status | Evidence / next gate |
|---|---|---|
| Research and reuse audit | In progress | Source/license matrix and upstream pins recorded; complete SBOM and legal/dependency review remain |
| Foundation | Implemented foundation; cross-platform verification incomplete | SPS registry, FastAPI app, SQLite observatory, memory/task/capability stores, approval flow and dashboards exist. CI infrastructure has previously failed before runner assignment; this is not evidence of an application failure and does not block development |
| AI gateway | In progress | Ollama and OpenAI-compatible adapters, free-first router and fallback. Text streaming over SSE (/api/gateway/chat/stream), provider-reported token usage, a persistent usage/cost ledger (/api/gateway/usage) and a runtime daily spend cap (MIA_MAX_DAILY_SPEND_USD) are implemented. The assistant page streams replies token by token when the Tools toggle is off (tool-enabled chat stays buffered) and shows tokens/cost per reply; its SSE parser was exercised only with a Node harness against fake responses, not in a real browser. Ollama model discovery reads real context length and capabilities (/api/show, falling back to 8192 and owner-gated tools when unavailable); the assistant header shows today's spend with a 30-day usage tooltip (not browser-verified). Provider throttles (429, or 503 with Retry-After) block a candidate for the advised wait and other transient failures back off exponentially (GET /api/gateway/health, Retry-After on chat 503; local tests with mocked HTTP only, no live provider). Streaming tool calls, full usage dashboard, UI for health, OpenAI-compatible capability discovery and remaining-quota header tracking remain |
| AI Brain | Core implemented; live provider/browser/voice verification pending (2026-11-20) | mia.brain: task-aware explainable routing with refusal when unsuitable, runtime capability evidence, intent/reference/ambiguity analysis, budgeted memory-aware context, persisted plans with dependencies/retries/checkpoints, permission-gated tool broker, completion-claim verification, one pipeline for text/streaming/voice, diagnostics API and repeatable evaluation scenarios. Verified with mocked providers and automated tests only (no live Ollama or owner-device voice run). See docs/architecture/AI_BRAIN.md |
| Voice | Conversation pipeline implemented; owner-device verification pending | Hands-free and push-to-talk sessions, browser or local Faster-Whisper input, English/Urdu/auto language, mic selection, mute, interrupt, stop phrases, chunked spoken replies, local pyttsx3/browser TTS fallback, state UI and privacy badge. An explicit opt-in "Hey Mia" wake phrase works through the online browser speech service while the Assistant page is open; it is not offline or OS-level always-on detection. Chromium lifecycle/API tests are automated. Physical microphone/speaker, Windows SAPI voices, Faster-Whisper download/latency/Urdu accuracy and offline wake-word operation remain unverified/incomplete. Owner steps: docs/voice/OWNER_VERIFICATION.md |
| Memory and knowledge | In progress | Persistent conversations, FTS5 and full-retained-history semantic candidates; optional loopback-only Ollama reranking with batched embedding cache; bounded two-hop structured relationship expansion (up to four linked records); provenance-labelled retrieval with remote-model withholding unless MIA_ALLOW_REMOTE_FILE_CONTEXT=true. Structured records support owner corrections by supersession, conflicts, audit history and active/completed retrieval. Automatic capture combines conservative rules with optional local-Ollama JSON-schema extraction (at most two source sentences, eight-second per-sentence timeout); model suggestions remain pending owner approval and fail closed. Explicit remember/forget commands, sensitive/uncertain screening, duplicate checks, approved consolidation and conversation-retention preview/purge exist. Remaining: structured-memory retention policy, wider multilingual model-extraction coverage and broader hostile-document testing. Recent retrieval/relationship tests are authored but not executed in this environment. |
| Autonomous digital life | Partial foundations; not the first development priority | Task Desk, approved read-only task dispatch, durable curiosity backlog, goal follow-up derivation, memory-review explorer, scoped allowlisted web research, journal, quiet hours, pause/emergency-stop/budget controls, scheduler/recovery, deterministic reflection digests, opt-in inbox outreach, /life UI and explicit curiosity capture exist as described in source. Model-based reflection, wider research, broader workflows, inbox integration in main chat, push delivery and owner-device verification remain |
| Computer intelligence | Governed host-side foundation implemented; local-device bridge pending | Scoped filesystem operations, allowlisted app launch, isolated browser automation, Windows UI Automation, process inspection/termination, risk-classified terminal, owner grants, one-time approvals, stop-all and durable multi-step tasks are implemented and tested. Adapters control the machine running Mia. If Mia runs on Oracle Cloud, they control the VM, not the owner's laptop; a paired outbound-only local companion agent, stronger sandboxing and real Windows device verification remain. Keep this work lower priority until the conversation-first milestone is accepted |
| Self-programming | AI draft + Capability Lab + Docker validation foundations | Mia can draft pure-Python modules/tests through free-only model routing; reviewed drafts are stored as immutable versions, Docker-validated and registered only when passing. Permission-free candidates can run in isolated Docker with bounded JSON I/O. Owner-approved promotion/rollback records exist behind a feature flag. Permission broker, safe plugin runtime boundary and target-Windows verification remain |
| Evolution Observatory | Day 0, explicit start, host verification and optional daily scheduler foundations | Baseline capture, fixed benchmarks, resource-aware daily snapshots, activity evidence, report API/viewer and evidence-hash verification exist. No real Windows-host baseline, actual owner-run 30-day experiment or evidence-backed final results exist yet |
| Mia Code Graph | Integrated in main; CI-verified | Deterministic source-backed index, Python AST symbols/imports/calls, route/file relationships, content-digest watcher, explorer, CLI refresh/stale checks and Git-diff impact analysis. Ubuntu and Windows CI passed in PR #12 and refreshed 3,797 nodes / 7,682 edges in core integration CI. Per-file fact cache with a global resolution pass (MIA-BASE-02): unchanged Python files are not re-parsed, and tests assert incremental output equals a clean rebuild; the watcher records changed paths, reuse counts and rebuild time in ignored history. Verified locally on Linux only; broader language parsers remain future improvements |

## Verification and CI policy

Development must continue even when GitHub Actions is unavailable or fails before runner allocation. CI status is tracked separately and is not a development completion criterion. Do not spend repeated sessions troubleshooting runner infrastructure. Run local formatting, static checks, targeted tests and the full test suite where practical; record exact results and distinguish local results from CI. Do not claim tests passed if they were not run.

Owner-only physical microphone/speaker tests, Windows desktop/device acceptance, live cloud-provider credentials, Oracle deployment, final owner approval and the real 30-day experiment must be labelled OWNER_VERIFICATION_REQUIRED. Implement the supporting code, controls and automated tests without treating those owner actions as blockers for continued development.

## Not complete

Mia is not yet a finished conversational companion or personal AI operating system. PR #20's canonical SPS self-description and multilingual evaluation fixtures are already merged. Full-history semantic candidate retrieval, bounded relationship-aware recall and local model-assisted memory extraction are also integrated on main, but recent regression tests have not been executed here. Remaining conversation-first development includes stronger cross-session/project/provider continuity, reasoning-quality benchmarks, bounded parallel judge-model evaluation, quality-based model escalation and offline always-on wake-word detection. A paired local agent is still needed for an Oracle-hosted Mia to control the owner's Windows PC. The full 3D holographic interface, additional digital-life workflows, advanced governed self-programming, custom foundation-model research and the complete Evolution Observatory remain later roadmap work.

Do not confuse a design target, UI affordance, documentation entry or mocked test with an operational capability. No Day 0 or daily growth data may be fabricated.

## Next core implementation sequence

The next development session must select the first unfinished item in Priority 0 or Priority 1 of docs/ROADMAP.md, based on a fresh source/PR review. Within the conversation-first target, the default order is:

1. PR #20 is merged; canonical SPS self-description and deterministic multilingual fixtures are on main.
2. Strengthen cross-session/project/provider continuity with explicit provider-switch tests and broader evidence-backed recall scenarios.
3. Expand repeatable reasoning-quality evaluation, including uncertainty, correction, memory conflicts and failed-provider/tool outcomes.
4. Add bounded parallel judge-model evaluation and quality-based model escalation, with latency/cost/privacy limits and deterministic provider-fake tests.
5. Complete offline local wake-word detection only after client-side microphone/agent architecture and privacy controls are defined; browser "Hey Mia" remains opt-in and online.
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


## 2026-11-20 full-history semantic memory development

Branch: `feature/full-history-semantic-memory` (based on current `main`).

- Added a store query that returns all messages from retained, non-temporary conversations rather than limiting semantic candidates to the latest 12/100 messages.
- Semantic retrieval now considers this full retained history; temporary conversations remain excluded and the final prompt context remains governed by the existing message/character budgets.
- Cached embeddings continue to use content-addressed hashes. Cache reads/writes use batched SQLite operations rather than one connection per message. First-time cache population is split into batches of 64 to avoid oversized Ollama embedding requests; subsequent retrieval reuses cached vectors.
- Added regression tests for old messages outside the recent window and exclusion of temporary conversations.
- Tests have been authored but not run in this session. CI is intentionally skipped per owner direction. The first query after upgrade may incur additional local embedding work proportional to uncached retained history; the configured local Ollama reranker remains opt-in.


## 2026-11-20 main development update — model-assisted extraction safety seam

- Full-history semantic retrieval and batched embedding-cache changes are committed directly to `main` (commit `4e16f522404c78f3e444b0bc95d6f6c560ad9670`). No PR or CI was used. Regression tests were authored but not executed locally.
- Added an optional `CaptureService(model_extractor=...)` integration seam (commit `e11dc6203e952bb73de7b493e9afc30f6cbef3b6`). It accepts only a structured candidate whose body exactly matches the screened owner sentence, validates the record type/title/confidence, and never lets a model suggestion auto-save. Model-derived suggestions are always pending owner approval, retain message evidence and observed provenance, and extractor failures fail closed without interrupting chat.
- Added focused tests for approval-only model suggestions, invented/paraphrased text rejection, sensitive/uncertain screening and extractor failure (commit `d08e95a4713daf9eb5397e55044795812cf06a63`). Tests have not been run.
- **Completed in subsequent main commits:** the Brain now awaits asynchronous capture callbacks; the Observatory capture callback asks Mia's own Gateway for at most two eligible sentences per user message, using only healthy, enabled, local Ollama candidates advertising JSON-schema support. Requests are marked sensitive, use a zero-paid-spend route policy, have an eight-second per-sentence timeout, and are skipped for explicit memory commands, disabled capture, temporary conversations, sensitive/uncertain statements and deterministic-rule matches. Suggestions still pass exact-source validation and are always pending owner approval. Ollama advertises its structured-output API capability; unsupported model/runtime combinations fail closed. This path is optional and does not send personal chat to cloud providers.
- Development policy: work and commit directly to `main`; no feature branches, no PRs, and no CI runs. Continue local targeted tests/static checks when the repository runtime is available and record exact outcomes.


## 2026-11-20 main development update — local model-assisted memory extraction

- Wired model-assisted extraction into the real Brain capture callback using Mia's own AI Gateway; the Brain now awaits async capture callbacks without changing the single text/voice pipeline.
- Extraction is local-only: only enabled, healthy, data-local models advertising JSON-schema capability are eligible; requests are marked sensitive and use a zero-paid-spend route policy. No cloud provider receives chat text for this feature.
- Work is bounded to two eligible sentences per user message and an eight-second timeout per sentence. Explicit remember/forget/update commands, temporary conversations, disabled capture, deterministic-rule matches, sensitive content and uncertain statements skip model extraction.
- Gateway structured output is validated again by CaptureService. The returned body must exactly match the owner's source sentence, confidence must be 0.40–0.85, and model-derived records are always pending approval. They can never auto-save.
- Added regression coverage for precomputed model candidate validation and approval-only lifecycle. Earlier tests also cover invented text rejection and extractor failures.
- **Verification:** tests and local Code Graph regeneration were not run; CI remains intentionally skipped. The repository GitHub file editor was used, so Python runtime behavior has not been executed here. Owner/runtime verification is still required. The Code Graph watcher should refresh when the local app runs; run `python scripts/update_code_graph.py` locally after pulling these commits to refresh the offline snapshot/history.


## Latest direct-to-main implementation — model-assisted memory extraction runtime

- Runtime wiring is now in place: the Observatory capture callback uses Mia's own Gateway and the Brain awaits the asynchronous callback.
- The extractor is local-only and sensitive-request constrained. It selects only healthy/enabled local candidates that advertise structured JSON output, with paid routes disabled. It handles at most two eligible sentences per owner message, with an eight-second timeout per extraction.
- Explicit memory commands, temporary chats, disabled capture, sensitive/uncertain statements and sentences already handled by deterministic patterns bypass model extraction.
- CaptureService validates every result again: exact original sentence only, known record type, bounded title and confidence 0.40–0.85. Model-derived records are pending owner approval only; they cannot auto-save.
- Ollama's provider metadata now advertises its schema-constrained JSON output capability. If the local model/runtime cannot satisfy the structured request, the feature safely produces no suggestion.
- Tests added for precomputed model-candidate validation and approval-only lifecycle; tests are not executed in this environment. CI intentionally skipped. Python runtime/syntax checks and Code Graph snapshot regeneration remain unverified and should be run locally after pulling main.

## 2026-11-21 main development update — relationship-aware memory regression coverage

- Current main includes bounded two-hop structured-memory relationship expansion: traversal starts from up to three strongest retrieved structured records and adds no more than four linked records, with a relevance penalty at each hop. Only normal-visibility relationships and active/completed endpoints are eligible.
- Added regression tests for two-hop recall, exclusion of private relationship chains, and the four-record expansion cap.
- These tests are authored but have **not** been executed in this environment. CI remains intentionally skipped per owner direction; do not treat authored tests as passing evidence.
- The current repository head before this test addition was `ae84b71b7e2146bd1f1f1925658b218b6d96b4ec`; the relationship test addition is committed directly to `main`.
- The local Code Graph snapshot/history was not regenerated because this environment has GitHub file editing but no repository checkout/runtime. After pulling main, run `python scripts/update_code_graph.py` locally; then run `python scripts/update_code_graph.py --check` to confirm it matches the checkout.
- Next conversation-first work: add evaluation coverage for memory correction/supersession and cross-session/provider continuity, then strengthen reasoning-quality evaluation and bounded model escalation. Do not begin 3D or computer-control expansion ahead of this milestone.

## 2026-11-21 follow-up — semantic retrieval documentation reconciled

- Rewrote `docs/memory/SEMANTIC_RETRIEVAL.md` to match the current main implementation: full retained non-temporary conversation history, batched embedding-cache operations, batches of up to 64 uncached messages, opt-in loopback-only Ollama embeddings, and bounded two-hop relationship expansion.
- Removed obsolete statements that described the semantic implementation as a development-branch change or limited conversation candidates to 12 messages.
- Strengthened the relationship-expansion cap test to construct a branching graph and assert exactly four related records can be added when the context record budget allows it.
- Verification remains unchanged: tests are authored but not run; CI is intentionally skipped. Code Graph regeneration requires a local checkout.

## 2026-11-21 continuity safety fix — temporary-chat isolation and context budgeting

- Source review found that cross-session "continue where we stopped" recall selected the newest other conversation without checking its `temporary` flag. That could have surfaced temporary-chat content in a normal conversation.
- Fixed `_prior_conversation_tail` to skip temporary conversations and search up to 50 recent conversation entries for the newest eligible persisted conversation.
- Fixed prior-conversation tail assembly to add only the newest contiguous messages that fit the remaining token budget. The previous implementation could append two oversized messages and exceed the requested context budget.
- Added regression tests for temporary-chat isolation and strict context-budget adherence.
- Tests are authored but not executed in this environment. CI is intentionally skipped. This fix is directly in the conversation-first memory/continuity path.



## Multi-agent development system initialized (2026-11-21)

- Added a six-worker specialist model: A1 Brain/identity/memory; A2 AI Gateway/model routing; A3 reasoning quality/parallel judges; A4 voice/wake word; A5 Code Graph/governed self-development; A6 experience/UI/interaction.
- Added a separate coordinator role; it is not counted as a seventh worker.
- Added a shared task board, agent registry, integration log, master prompt and six role-specific instructions.
- Initial tasks focus on the highest-priority open development work. The full 3D interface and later roadmap phases remain gated by conversation-first completion.
- All six registry slots are currently UNREGISTERED/OFFLINE. No external agents have been connected or started by this repository setup.
- Work remains on main only. Parallel analysis/disjoint implementation is allowed, but task claims, shared-file updates and pushes must be serialized and based on the latest remote state.
- This setup commit does not implement or verify the six feature workstreams and does not imply that tests were run.

## 2026-11-21 A1 baseline repair and continuity fix (MIA-BRAIN-01)

- **First executed test run of the recent direct-to-main work.** Earlier sections above say tests were "authored but not executed". On 2026-11-21 the full suite was run locally for the first time: it could not import because of two syntax errors on main (`brain/verify.py`, `observatory/api.py`). After repair (commit `5887f86`): `pytest` = **1277 passed, 16 skipped**, `ruff check src tests` clean. This supersedes the "not run" notes for the code covered by the suite; live-provider, device and browser behaviour remains unverified.
- Fixed Roman Urdu/Urdu hedge detection (short-reply rule now word-based) and a continuity defect where >=4 recent temporary chats hid an older persistent conversation from the prior-session check (commit `e9b15b7`, regression-tested).
- Remaining Priority 1 continuity gap: conversations have no project association; tracked as MIA-BRAIN-02.
- CI was not used. Code Graph snapshot was not regenerated in this session.

## 2026-11-21 A1 provider-switch continuity coverage (MIA-BRAIN-02)

- Added `tests/test_brain_continuity.py`: cross-session memory recall after a provider switch, remote-provider privacy withholding after a switch, and owner-correction precedence after a switch. Local run: full suite **1280 passed, 16 skipped**, ruff clean (commit `f61cdd3`). Scripted providers only; this verifies provider-independent continuity plumbing, not live model answer quality.
- Project-scoped conversation continuity does not exist and is deferred (MIA-BRAIN-03) pending an owner decision.

## 2026-11-21 — MIA-GW-01 (A2): bounded quality escalation

- **Finding (source-traced):** verifier confidence was only recorded in the turn trace/API result; no code used it to route or retry, so quality-based escalation was *not* wired. Gateway fallback was failure-based only.
- **Implemented:** `src/mia/gateway/escalation.py` (pure policy, disabled by default, fail-closed on privacy/cost/latency/tokens/side effects) and a small non-streaming hook in `Brain` (`_maybe_escalate`). Details in `docs/architecture/AI_GATEWAY.md`.
- **Verified here:** `tests/test_gateway_escalation.py` (fake providers) passes; full suite run locally: 1405 passed, 1 skipped, 1 failed (`tests/test_browser_automation.py::test_browser_api_end_to_end`, failing before this change and untouched by it). No live-provider quality claim is made.
- **Not done:** streaming-turn escalation, judge-model orchestration, owner-facing settings UI.


## 2026-11-21 Reasoning-quality evaluation (MIA-EVAL-01, agent A3)

- **Implemented:** `mia.brain.evaluation` now has 23 deterministic scenarios (memory conflicts through the real supersede path, real tool failures, English/Roman Urdu/Urdu continuity, multilingual unsupported claims, uncertainty) with assertions on what the model actually received; `summarize()` reports by category/language and always states `live_model_quality: not_measured`.
- **Fixed:** verifier no longer treats honest "failed/cannot" wording (incl. Roman Urdu/Urdu) as silent failure; added `tests/test_source_compiles.py` guard.
- **Verified here:** full suite 1618 passed, 1 failed (MIA-FIX-01, browser grant), 1 skipped; scenario runner 23/23 (commit 186f5d0).
- **Not done:** parallel judge models (MIA-EVAL-02), live-model evaluation (OWNER_VERIFICATION_REQUIRED).

## 2026-11-21 Parallel judges module (MIA-EVAL-02, agent A3)

- **Implemented:** `mia.brain.judges` - opt-in, bounded advisory judges (cap 3, concurrency/timeouts/cancellation, local-only for sensitive turns, paid/remote opt-in with fail-closed unknown prices, independent of the answering model, strict JSON votes, quorum else deterministic fallback; may only lower confidence).
- **Verified here:** 20 fake-provider tests pass (commit 387420f). Not run against any live model.
- **Not done:** pipeline wiring and owner settings (MIA-EVAL-03, needs A1/A2); live judge quality is OWNER_VERIFICATION_REQUIRED.

## 2026-11-21 — MIA-GW-02 (A2): escalation visibility

- Added read-only `GET /api/gateway/escalation` and an `escalation` block in `/api/gateway/overview` (effective policy + process-local counters). Streaming escalation is documented as intentionally unsupported. No write surface; no persistence of counters.
- Verified locally: full suite 1644 passed, 1 skipped, 1 failed (`tests/test_browser_automation.py::test_browser_api_end_to_end`, pre-existing, untouched). Fake providers only; no live-provider claim. Not browser-verified: no dashboard UI was added.

## 2026-11-21 A1 project-scoped conversation continuity (MIA-BRAIN-03)

- Owner-approved and implemented (commit `ba7f6cf`): a conversation can be assigned to a project (opt-in, default none). Recall for a conversation in a project prefers that project's history, then unassigned chats, and never another project's; temporary chats stay excluded. Schema migration 4 adds `conversations.project`; `POST /api/conversations/{id}/project` sets or clears it; the project appears in conversation listings.
- Local evidence: full suite **1633 passed, 16 skipped**, ruff clean, on the tree rebased over current main. Isolation tests were mutation-checked. Scripted/store-level tests only; no live model run.
- Not done: no Assistant UI control yet (MIA-UX-02), so the project is currently visible and settable through the API only; structured memory records and retrieval are not project-scoped (MIA-BRAIN-04).


## 2026-11-21 — MIA-EVAL-03a (A2): gateway-backed judge call

- Added `mia.gateway.judge_call.make_judge_call` (pinned candidate, text-only, normal budget/route policy). Not yet wired into the turn pipeline (MIA-EVAL-03). 6 fake-provider tests pass; no live-model judge quality is claimed.

## 2026-11-21 — MIA-GW-03 (A2): gateway audit + language-aware routing

- Audit (source-traced): fallback, circuit breakers, paid-budget reservations (kept on cancel), stream partial-text preservation and routing audit are in place. Gap fixed: routing had no language awareness, so Urdu/Roman Urdu turns were steered to the smallest model.
- Implemented `mia.gateway.language.detect_language` + `BrainRouter` scoring (ordering only; preferences win). Not done: a non-streaming provider hang has no gateway-level timeout (real adapters use httpx timeouts; `stream_idle_timeout` is opt-in) - noted for follow-up.
- Verified locally: full suite 1718 passed, 1 skipped, 0 failed. Fake candidates only; no live-model Urdu quality is claimed.
