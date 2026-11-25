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

## Voice update - MIA-COMP-10 voice-driven computer actions (agent A4, 2026-11-24)

- **Authority:** owner-approved exception ("approve MIA-COMP-10 for A4"). Backlog dependencies (COMP-09, M1 voice pipeline) are done. COMP-04 (central broker, A7) is still partial, so this adds **no new permission path**: a voice turn is the same chat turn through the same Brain, tools, per-adapter checks and `ApprovalStore` as text.
- **Implemented:**
  - `src/mia/voice/action_readback.py`: a sentence generated **from the structured action fields** (never the model's summary) plus a 4-character card code (no I, O, 0, 1). Irreversible parts are stated first ("Open the application X. Mia cannot undo this."); unknown kinds fail safe ("cannot describe exactly ... check the details on screen very carefully"); hostile display text is flattened and truncated. Every pending action now carries `readback`; the restored-cards route does too.
  - After a voice turn that prepared an action, Mia says: "I prepared one action. Card K 7 M 2: <sentence> Nothing has happened yet. Approve it on screen, or say stop to cancel." The card shows the same sentence and code ("Mia will: ..."), so what was heard and what is shown match.
  - **Voice never approves.** Approval remains an on-screen click. A spoken "yes, approve it" is an ordinary turn and executes nothing (tested).
  - **Cancel is real:** a short spoken "stop", "cancel", "never mind" (and Urdu/Roman Urdu equivalents), the Interrupt button, Esc, "stop speaking"/"be quiet" and ending the conversation all reject the approvals prepared by that voice turn (server-side reject), mark the cards "Cancelled by voice. Nothing was done." and say so. A bare "stop" with nothing waiting is an ordinary turn, and longer sentences never count as cancel commands. An action already decided on screen is not touched. If a cancel cannot be applied (already decided or expired) Mia says so.
  - **Doubtful transcripts do not drive tools:** if the browser recognizer reports confidence below 0.5 and tools are on, the text is placed in the prompt box for the owner to check (spoken: "I'm not sure I heard that correctly..."); unknown confidence (0) and tools-off turns are unaffected.
  - Found by the new tests and fixed: speech chunking split file names at the dot, so "notes/a.txt" was spoken as "a. txt"; in-word periods no longer split sentences (all speech, not just read-backs).
- **Tests actually run:** `tests/test_action_readback.py` (10; includes a real chat-tool flow with a real filesystem adapter) and 7 new Chromium tests (read-back spoken and shown identically and no approval by voice; stop cancels; interrupt during the read-back cancels; screen-approved action not cancelled; doubtful transcript held back; tools-off unaffected; bare stop and long sentences). Without the page changes 5 of the 6 flow tests fail. Regression: 94 UI tests (voice browser, stop-reply, life inbox, UI contract) and 84 computer/approval/chat-tool/notification/lifespan/language tests pass. `ruff` clean. Full suite not re-run for this step (last full run is recorded above).
- **Scope notes (honest):** covers the four tool-produced approval kinds (file write, application launch, browser click, desktop type-text). "Ambiguous target" disambiguation relies on the tools resolving to a specific target, which the read-back then states in full; there is no separate spoken disambiguation dialog. Spoken confirmation of low-risk actions (design-note option) is deliberately **not** offered. Push-to-talk and hands-free both use the same flow; the wake-word path was not separately exercised. The read-back uses English wording regardless of the owner's language.
- **Owner-required checks:** real microphone/speaker run of the read-back and cancel flow on the owner's Windows machine; Urdu and Roman Urdu command recognition ("ruk jao", "band karo", "منسوخ کرو") with the real recognizer; real browser-recognizer confidence values vs the 0.5 threshold; a screen-reader pass over the new card lines.

## Computer update - MIA-COMP-09 close-out and full-suite check (agent A4, 2026-11-24)

- **Acceptance check against the spec:** exact action, target, data and side effects are shown before approval for every tool-produced approval kind (filesystem write, application launch, browser click, desktop type-text; the card also shows validity time); the audit persists approvals, executions, failed and refused attempts, and undos; cancellation is reject plus the existing `/api/tasks` approve/reject/cancel panel (already present in the assistant page and covered by the dispatcher cancellation tests); undo exists where it is possible (file writes) and the card says plainly where it is not; tested: expiry (timeout), reload (disconnect), duplicate and simultaneous submit, failure after the approval was spent (partial completion), retry after failure.
- **Scope notes:** terminal, process and file-operation approvals are API-only (no chat card, no UI) so they were not given restorable cards; enforcing approvals centrally is COMP-04 (A7 continues it); undo/approval routes are not yet routed through that broker.
- **Test hygiene fixed:** my new test files wrote to the shared `./data` folder, so a settings change in one run leaked into the next run (found when the full suite failed in `test_notification_api.py`). They now use a per-test `MIA_DATA_DIR`. Other existing tests still create `./data/mia-notifications.sqlite3` through `create_app()`; harmless for them, but any test asserting default notification settings must isolate the data dir.
- **Full suite actually run (this session):** all test files except `test_voice_wake_ui.py` (times out in this Linux sandbox, identical at baseline) and `test_holo_budget.py` (its frame-rate checks failed under four parallel Chromium processes and pass alone: 25 passed). Final grouped run: every group passed except `test_projects_workspace_page.py::test_deleting_a_project_keeps_its_tasks_decisions_and_chats_and_says_so`, which failed under the same parallel load and passes alone (24 passed). One more environment finding: `websockets` was not installed in this sandbox, which made two wake-transport tests fail until it was installed (it is declared in `pyproject.toml`). Not run: real Windows, microphone/speaker, screen reader.
- **Owner-required checks for COMP-09:** use the approval cards, reload and Undo on the owner's Windows machine; a screen-reader and contrast pass on the approval card and Notifications panel.

## Computer update - MIA-COMP-09 undo for approved file writes (agent A4, 2026-11-24)

- **Implemented:** `FilesystemAdapter.undo_write` and `POST /api/computer/files/write/undo`. After an approved write completes, the card shows **Undo this change**. Undo restores the earlier version (or removes the file Mia created) **only while the file still has exactly the content Mia wrote** (SHA-256 match), so it can never destroy the owner's later edits; it accepts only a recovery copy of the same file (path must be `.mia-recovery/backups/<stamp>-<same file name>`, no traversal, no symlinks, blocked credential/key/.git targets stay blocked); whatever it replaces or removes is first copied into the recovery area, so the undo is itself recoverable; a second undo, a missing or pruned backup, or a deleted file is refused with a clear reason. Every undo and every refusal is audited (`write_undone`, `write_undo_refused`).
- **Tests actually run:** `tests/test_write_undo.py` (18: overwrite and new-file undo, changed-since refusal, double undo, deleted file, 7 bad backup paths, a real recovery copy of a different file, pruned backup, blocked files, input validation) plus 1 Chromium test (button appears only after completion, sends the exact hash and backup, a refused undo can be retried, success removes the button). Disabling the hash check, the same-file rule or the safety copy each makes tests fail; the UI test fails without the HTML change. Regression: 87 UI tests and 116 computer/approval/chat-tool/app-lifespan tests pass. `ruff` clean. Full suite not run.
- **Honest limits:** undo covers file writes only; launches, clicks and typed text cannot be undone (the card says so). The undo route is an owner action like the other approval routes and is not itself gated by the COMP-04 broker (which does not exist yet).
- **COMP-09 status: PARTIAL, not done.** Open: approvals created through terminal, process and file-operation routes are not covered by the card hook; no cancel/partial-completion handling for multi-step computer tasks in the approval UI; no Windows run; no screen-reader pass; broker enforcement is COMP-04.

## Computer update - MIA-COMP-09 restorable approval cards (agent A4, 2026-11-24)

- **Problem (verified in source):** approval cards existed only in the chat stream. After a page reload or a dropped connection a pending action disappeared from the UI, and `GET /api/computer/approvals` returned only a summary, not the exact action, so it could not be approved or even reviewed.
- **Implemented:** every tool-produced pending action (filesystem write, application launch, browser click, desktop type-text) is now remembered by `ApprovalStore.save_card` (central hook in `register_tool`, so future tools get it automatically). New `GET /api/computer/approvals/cards` returns the exact kind, summary, display fields, payload and expiry of approvals still waiting. The card is deleted the moment the approval is used, rejected or expires (checked at the database, not just hidden), kept after a failed attempt so the owner can retry, capped at 64 KB (larger actions keep their summary but are marked `restorable: false`, show Reject only, and say why). Saving a card can never fail the tool call itself. The page now restores pending cards on load (once each, no duplicates) with the line "still waiting for your approval from earlier. Nothing has been done yet." Cards also state **what happens** (per kind: the write backs up the previous version first; launch, click and typed text cannot be undone by Mia) and **until when** the approval is valid.
- **Tests actually run:** `tests/test_approval_cards.py` (7: restore after reload then execute, reject clears stored content, expiry purge, retry after a failed attempt, oversized payload, state rules, failing store does not break the tool call) and 3 new Chromium tests (restored card shows effect and expiry, restores once, runs with the exact payload; a failed run can be retried; oversized card offers only Reject and HTML in text stays text). Mutations that remove saving, expiry purge or the reject cleanup each make tests fail; the UI tests fail without the HTML change. Regression: 86 UI tests and 144 computer/approval/chat-tool/app-lifespan tests pass. `ruff` clean. Full suite not run.
- **Privacy note:** a pending card holds the file content or text that would be written (up to 64 KB) in the local approvals database until the approval is spent, rejected or expired.
- **Still open in COMP-09:** an undo/recovery surface for completed actions (the write keeps its backup, but there is no button to restore it); approvals created through the terminal, process and file-operation routes are not covered by the central card hook; no Windows verification; no screen-reader pass; enforcement stays COMP-04.

## Computer update - MIA-COMP-09 approval recovery slice (agent A4, 2026-11-24)

- **Authority:** owner-approved exception ("approve MIA-COMP-09 for A4"). The backlog dependencies COMP-04/06/07/08 are not done, so this builds on the existing `ApprovalStore` and `/api/approvals` flow. It does **not** add broker enforcement (COMP-04) and does not claim the whole task.
- **Defect reproduced first:** a failed `POST /api/approvals/{id}/execute` (even a mere payload mismatch) left the approval stuck in `approved`: the owner could neither retry nor reject it, it lingered in the pending list until expiry, and the failure was not audited.
- **Fixed:** `ApprovalStore.release()` returns an approved-but-unspent, unexpired approval to `pending` after a failed attempt (a spent or expired approval can never be revived). The execute route now releases on failure and records an audit event for every failed or refused attempt (`approved_action_failed` / `approved_action_refused`, with kind, reason and resulting approval state). If the failure happened after the approval was spent, the error says so ("ask Mia for a new preview"). State errors are clearer: already submitted, already used, rejected, expired.
- **Tests actually run:** `tests/test_approval_recovery.py` (11): failed attempt then retry, reject after failure, duplicate submit executes once, four simultaneous clicks execute exactly once, expired approval refused and audited with nothing written, failure after the approval was spent, release rules, message clarity. Disabling the release logic makes 2 fail. Regression: 124 tests across computer API, fileops, terminal, process/apps, browser API, chat tools, brain owner permissions, capability API and desktop control all pass. `ruff` clean. Full suite not run.
- **Still open in COMP-09:** approval cards disappear on page reload (they exist only in the chat stream; `GET /api/computer/approvals` lists summaries only, not the exact action); the card does not yet show side effects, reversibility or the expiry time for most action kinds; no cancel/undo surface for completed actions in the UI; terminal/process/other kinds are approved through different routes and were not touched; no Windows verification.

## Voice review - HOLO-04 voice wiring and LIFE-14 delivery (agent A4, 2026-11-22)

- **HOLO-04 review (the item A6 noted for A4):** the evidence reporter derives `voice_speaking` from the real `voiceSpeaking` flag and republishes on every `setVoiceState`, which is compatible with streamed speech (VOICE-05): the flag stays true across all sentences of one reply. New Chromium test `test_runtime_evidence_keeps_speaking_held_across_streamed_sentences` checks the evidence stream, the flag and the orb state between sentences; making each sentence clear the flag makes it fail (restored: passes). Note the evidence is only published on state changes and a 20 s timer, so a hologram watching the channel would not notice a mid-stream flag drop; the flag itself matters for Interrupt and echo protection, which is why the test asserts it directly.
- **LIFE-14 / outreach vs the notification center (decision):** LIFE-14 (A1) deliberately sends proactive suggestions to the outreach inbox with its own policy and excludes due and overdue items, which belong to the notification center. The two surfaces therefore carry disjoint content, so I am **not** routing outreach or suggestions through the center (that would duplicate caps, quiet hours and dismissal memory and override A1's design without an owner decision). This closes that leftover from the LIFE-09 notes; the owner may still ask for one merged inbox later.
- **Tests actually run:** the new test twice (passes) plus the mutation check; the full `test_voice_browser.py` file then passed (69 tests, 0 failed). `ruff` clean.

## Life update - MIA-LIFE-09 one timezone setting (agent A4, 2026-11-22)

- **Change:** the notification timezone now follows the main `owner_timezone` setting by default (`follow_owner_timezone`, on by default), so quiet hours, the daily-cap day and the due times shown in notifications move with it. Choosing a timezone in the notification settings is an explicit override and turns following off; a "Use my main timezone" checkbox in the panel turns it back on. An unreadable or invalid main setting falls back to the last explicit/default zone (UTC) and never blocks delivery. A1's memory settings are only read (`memory_store.settings.get("owner_timezone")`), not changed.
- **Tests actually run:** 3 new unit tests and 1 new API test (following, override, change moving quiet hours, bad/missing/raising provider); mutating the follow logic makes 2 of them fail. Notification unit/API/task suites (66) and the 4 notification Chromium tests pass; `test_app_lifespan.py`, `test_life.py`, `test_assistant_life_inbox.py` still pass. `ruff` clean. Full suite not run.
- **Still open:** the outreach inbox and briefings are not routed through the center (recommended to do with LIFE-14), a real screen-reader/contrast check (owner-only), push-to-talk starts skip the voice announcement.

## Life update - MIA-LIFE-09 notification UI and voice announcement (agent A4, 2026-11-22)

- **UI (`src/mia/assistant.html`):** a "Notifications" button with an unread badge opens an accessible panel (real buttons, `aria-expanded`/`aria-controls`, live region, visible focus, badge `aria-label`). Each item shows its title, body, **why** and **source**, priority, repeat count and expiry, with Mark read, Snooze 1 hour, Snooze 1 day, Dismiss and a lazy History expander. An empty state explains when items are only waiting for quiet hours or the daily limit. A Settings section edits notifications on/off, voice mention, timezone, quiet hours, urgent bypass and the daily limit, with server validation errors shown. All text is set with `textContent` (a title containing HTML stays text); no `innerHTML`. Polls every 60 s like the inbox.
- **Voice:** `POST /api/notifications/voice-digest` (owner route, opt-in via the voice setting, each item returned once). Starting a hands-free voice conversation now speaks "You have N notifications: ..." before the microphone starts; it does nothing when the setting is off, the list is empty or the route fails.
- **Tests actually run:** 4 new Chromium tests (panel shows why/source and snooze updates the badge; HTML titles stay text; settings validate and save; voice start announces once and only then listens) and 1 new API test. The four UI tests fail without the HTML change. Regression run: `test_voice_browser.py`, `test_assistant_life_inbox.py`, `test_assistant_stop_reply.py`, `test_voice_ui_contract.py` and the notification suites all pass (97). `ruff` clean. Full suite not run.
- **Not done / not verified:** push-to-talk starts skip the announcement; no screen-reader or contrast audit with real assistive technology (the structure follows the existing inbox, but this is `OWNER_VERIFICATION_REQUIRED`); the outreach inbox and briefings (A1's LIFE-05) are not yet routed through the center; the notification timezone vs `owner_timezone` duplication still stands.

## Life update - MIA-LIFE-09 task producer (agent A4, 2026-11-22)

- **Now that MIA-LIFE-02 is DONE**, `src/mia/life/task_notifications.py` (`sync_due_tasks`) turns open tasks and reminders that are due within 24 hours or overdue into notifications. It runs whenever the owner opens `GET /api/notifications` (pull model, so it works with no scheduler and catches up after downtime). A failing producer cannot break the inbox. Each notification carries the task as source and evidence and a reason with the due time in the owner's notification timezone. Repeats merge; a recurring task's next occurrence notifies again; overdue high-priority items are urgent; completed, cancelled, deleted or re-dated tasks are withdrawn (`NotificationCenter.withdraw`, which sets no dismiss cooldown). Private items never put their title or details into a notification.
- **Tests actually run:** `tests/test_task_notifications.py` (10) plus the notification suites: 60 passed, 0 failed. Three deliberate breakages (private-title leak, no withdrawal, due time missing from the dedupe key) each made tests fail. `test_task_manager.py`, `test_app_lifespan.py`, `test_life.py` and the other notification tests still pass. `ruff` clean. Full suite not run.
- **Known gap:** the notification center has its own `timezone` setting, separate from the memory setting `owner_timezone` used for recurring tasks. They can disagree (quiet hours and the shown due time would then use the notification one). Unifying them is a one-line decision for the owner or A1, so I did not override either.
- **Still open for LIFE-09:** the accessible UI (A6), a voice-session hook for `voice_digest()`, and feeding the existing outreach inbox and future briefings into the center.

## Life update - MIA-LIFE-09 notification center, backend half (agent A4, 2026-11-22)

- **Authority:** owner-approved exception to the M2->M3 gate, named in chat ("MIA-LIFE-09"). LIFE-01 is DONE; LIFE-02 (A1) is still IN_PROGRESS, so this is a source-agnostic core. Due-date and recurrence notifications from tasks plug in through `submit()` once LIFE-02 lands; none are built here.
- **Implemented:** `src/mia/life/notifications.py` (own SQLite file `mia-notifications.sqlite3`, local only, no network). Mandatory reason + source on every notification (shown by `explain()` with evidence and a state history). Owner settings: master switch, inbox/voice channels (voice opt-in, announced once at voice-session start, never while held), IANA timezone, quiet hours (wrap past midnight; urgent bypass is a setting), relevance floor, per-day cap, minimum gap, default expiry, default snooze, dismiss cooldown. De-duplication merges repeats (`occurrences`) and respects a dismissed cooldown. Expiry, snooze wake-up and release policy are evaluated lazily on every read, so an app that was closed or offline catches up correctly. `src/mia/life/notification_api.py`: owner routes under `/api/notifications` (list, settings, explain, read, dismiss, snooze). There is deliberately no create route, so nothing outside Mia's own code can post a notification. `tzdata` added as a Windows-only dependency in `pyproject.toml`.
- **Tests actually run:** `tests/test_notifications.py` (45) and `tests/test_notification_api.py` (5) pass, including timezone (Asia/Karachi) and both DST transitions (America/New_York spring-forward and fall-back), duplicates, offline catch-up after 3 days, restart persistence and invalid input. Four deliberate source mutations (fixed UTC offset, no dedupe, no cap, no expiry) each made tests fail. Regression suites run and passing: `test_voice_api.py`, `test_app_lifespan.py`, `test_life.py`, `test_gateway_api.py`, `test_knowledge_api.py` and the life/observatory file set. `ruff` clean. Full suite not run.
- **Not done (task stays partly open):** the accessible Notification Center UI and a settings panel (A6's half; `assistant.html` untouched), wiring voice session start to `voice_digest()`, and a real producer. No source currently submits notifications, so the center is empty until LIFE-02/05/14 or the outreach inbox feed it. The existing `life_outreach` inbox and fixed-offset quiet hours were left unchanged and are not migrated.
- **Not verified:** behaviour on the owner's Windows machine (zoneinfo needs the new `tzdata` dependency installed); real-world timezone choice by the owner.

## Voice update - MIA-VOICE-05 speak while streaming (agent A4, 2026-11-21)

- **Implemented (`src/mia/assistant.html`):** on the streaming path (tools-off turns) Mia now speaks each completed sentence while later text is still arriving, instead of after the whole reply. One speech generation per reply (`createStreamingSpeaker`, `speakReply(..., {append:true})`); a partial sentence and an unclosed code fence are never spoken; Interrupt/Esc/Stop-reply cancel speech, and after an interrupt the text keeps streaming on screen but nothing more is spoken; `MAX_SPOKEN_CHARS` still applies; listening resumes only after the last sentence.
- **Important limit found:** the tools checkbox is ON by default, and with tools offered the server runs the buffered tool/plan pipeline (`Brain.stream_turn`, `buffered = bool(p.offered)`), which returns one delta. So in the default configuration speech still starts after the full reply. Streaming tool-bearing turns is a Brain/Gateway change (A1/A2) and is not done; recorded as a follow-up, not claimed.
- **Tests actually run:** 3 new Chromium tests (speak first sentence before the stream ends; interrupt mid-stream; open code fence held back) fail without the change and pass with it. `test_voice_browser.py`: 64 passed, 0 failed. `test_assistant_stop_reply.py` (one source-contract assertion loosened to allow the new optional `onDelta` argument) and `test_voice_ui_contract.py` passed. `ruff` clean.
- **Pre-existing failures, not caused by this change:** `tests/test_voice_wake_ui.py` times out in this Linux sandbox identically with and without this change; not investigated.
- **Not verified:** real TTS timing/gaps between sentences, real voices, real microphone echo; `OWNER_VERIFICATION_REQUIRED`.

## Voice update - MIA-M1-09 text/voice parity (agent A4, 2026-11-21)

- **Gap (from A3's `docs/M1_TRACEABILITY.md` row 4, re-verified in source):** `assistant.html` never sent `interface` on `/api/gateway/chat` or `/chat/stream`, so every spoken turn was recorded server-side as `text` and the voice-turn policy (no judge latency on spoken replies, `orchestrator.py`) never applied to real UI voice turns. Server tests passed only because they set the field by hand.
- **Fixed:** `submitPrompt` now sends `interface: "voice"` when the reply is spoken (`voiceReply`) and `"text"` otherwise, on both request paths (2-line change in `src/mia/assistant.html`). Identity, context, Brain, gateway, verification and persistence remain one shared pipeline.
- **Tests actually run:** two new Chromium tests in `tests/test_voice_browser.py` (spoken turn posts `interface: voice`; typed turn posts `text`); both fail without the HTML change. `test_voice_browser.py`, `test_assistant_stop_reply.py`, `test_voice_ui_contract.py`: all passed (72 tests, 0 failed). `ruff` clean. Full suite not run.
- **Behaviour note:** a prompt typed while voice mode is on is also treated as a spoken-reply turn (that is the existing `voiceReply` meaning), so its reply is spoken and not judged.
- **Not verified:** real microphone turns end-to-end; `OWNER_VERIFICATION_REQUIRED`.

## Voice update - MIA-M1-12 interruption and recovery (agent A4, 2026-11-21)

- **Audit (source and test read):** browser-side stop, mute, barge-in (off by default), Escape, push-to-talk, mic release on session end/transcription failure, offline stop, provider-failure recovery and Urdu/Roman Urdu end phrases are already covered by `tests/test_voice_browser.py` and `tests/test_assistant_stop_reply.py` (Chromium-driven). Server-side, `Brain.stream_turn` keeps a partial reply marked `[response interrupted]` on provider failure or cancel.
- **Defect found and fixed:** if a streamed turn failed *after* its complete reply had been stored (for example a bookkeeping error), the error handler stored a second copy marked `[response interrupted]` and reported `partial: true`. It now stores nothing extra and reports `partial: false`. `src/mia/brain/orchestrator.py` (2 lines).
- **Tests actually run:** new `tests/test_brain_interruption_recovery.py` (4 tests: no duplicate after store, cancel while judging keeps exactly one complete reply, interrupt before any text stores nothing, repeated interruption keeps the conversation usable and ordered) plus `test_brain_pipeline.py` and `test_brain_judge_pipeline.py`: 31 passed, 0 failed. The duplicate-copy test failed before the fix. `ruff` clean on touched files. Chromium suites were not re-run this session; full suite not run.
- **Not covered / open:** cancellation of the buffered tool-bearing path of `stream_turn` has no dedicated test; real barge-in with a microphone, real speaker echo and real provider timeouts are `OWNER_VERIFICATION_REQUIRED`.

## Voice update - MIA-M1-11 multilingual audit (agent A4, 2026-11-21)

- **Audit (source-read):** STT is local Faster-Whisper (CPU int8, lazy load); TTS is pyttsx3/OS voices with no language-aware voice choice. Whisper has no Roman Urdu code, so `ur` returns Urdu script and `en` returns English text; Roman Urdu speech is only approximated. `/api/voice/transcribe` previously forwarded any `language` query string to the engine unvalidated, and neither transcription nor synthesis reported latency.
- **Implemented:** `normalize_stt_language` (`src/mia/voice/stt.py`): accepts empty/`auto` (engine auto-detect), `en*`, `ur*`; anything else is a 400 before the engine runs. Transcribe response and event now carry `requested_language` and `latency_ms`; `/api/voice/speak` sets `X-Mia-Latency-Ms` and records `latency_ms` (spoken text still never logged). Failed calls record no event.
- **Tests actually run:** `tests/test_voice_language_latency.py` (44 tests: language normalization, API forwarding/rejection, latency in response/event/header, adapter segment consumption and temp-file deletion, text-language fixtures for English / Roman Urdu / Urdu script) plus the existing voice, TTS, STT, language-routing and voice-UI-contract suites: 83 passed, 0 failed. Without the source change the new file fails at import. `ruff check src tests scripts` clean. The full suite was not completed in this session (a broad run exceeded the sandbox time limit).
- **Not verified / OWNER_VERIFICATION_REQUIRED:** real Urdu or Roman Urdu recognition accuracy, real TTS voice availability for Urdu on the owner's Windows machine, and real latency numbers (tests use fakes). Streaming STT/TTS is not implemented (clip upload and whole-WAV synthesis); that remains an open Priority 1 item.

## Voice update - MIA-VOICE-01 (agent A4, 2026-11-21)

- **Implemented and tested locally:** an offline wake-word core in `src/mia/voice/wake.py` (replaceable detector contract; session that is off by default, needs an explicit owner opt-in, is always time-bounded, stops immediately and drops late audio; threshold/patience/cooldown/activation-storm guards; no audio retention; fail-closed scoring; optional openWakeWord adapter that takes an owner-supplied model and never downloads one) and a truthful `offline_wake_word` capability report in `GET /api/voice/status`.
- **Not operational:** there is **no audio transport and no UI**, so Mia does **not** have an offline wake word. The status report says `operational: false` / `audio_transport_not_available`. The browser "Hey Mia" phrase is unchanged (online, page must stay open).
- **Audit findings (verified 2026-11-21, see `docs/voice/WAKE_WORD_PLAN.md`):** openWakeWord code is Apache-2.0 but its **pre-trained models are CC BY-NC-SA 4.0 (non-commercial)**; wake words are English-only; on Linux with Python 3.13 `pip install openwakeword` fails because upstream pins `tflite-runtime` (a `--no-deps` install with onnxruntime works; Windows uses ONNX).
- **Evidence actually run:** `tests/test_voice_wake.py` (51 tests) and new `tests/test_voice_api.py` cases pass; a 9-mutant check of the safety properties was caught 9/9; full suite at that time excluding the Chromium-based files (not run then; CORRECTION: Chromium is available and they were later run, see MIA-VOICE-03 below): 1330 passed, 2 skipped, 0 failed; `ruff check` clean on touched files. The adapter and session were run once against the real openWakeWord engine in a scratch environment (about 3-5 ms CPU per 80 ms frame on a 2-core Linux sandbox; 0 activations on 20 s of synthetic noise).
- **Not verified:** detection of real speech, false-accept and missed-wake rates, Windows install and CPU use, any "Hey Mia" model - `OWNER_VERIFICATION_REQUIRED` (`docs/voice/OWNER_VERIFICATION.md` section G). Synthetic noise is not evidence of false-accept performance.
- **Follow-ups on the board:** MIA-VOICE-02 (loopback transport), MIA-BRAIN-04 (self-description must not infer a wake word from route names), MIA-UX-02 (UI), MIA-VOICE-03 (Urdu / Roman Urdu stop commands; today English-only).
- Code Graph: refreshed locally with `python scripts/update_code_graph.py` (3,936 nodes, 7,924 edges, 0 parse errors; includes `voice/wake.py`); the tracked `docs/code-graph/CHANGELOG.md` entry was deliberately not committed to avoid cross-agent conflicts and wrong-commit attribution.

## Voice update - MIA-VOICE-02 (agent A4, 2026-11-21)

- **Implemented and tested locally:** `src/mia/voice/wake_transport.py`, a loopback-only WebSocket/REST transport for the offline wake word (strict owner opt-in message, same-origin and no-proxy checks, one connection, stop on message/REST/socket-close/stall/time limit, metadata-only events). 31 tests, including a real uvicorn server with a real WebSocket client; a 10-mutant check of the guards was caught 10/10. Full suite excluding the Chromium files: 1676 passed, 2 skipped, 0 failed.
- **Still not operational:** the routes are deliberately **not registered** in `create_app` (waits for MIA-BRAIN-05 so Mia cannot describe a route as a working wake word), and there is no UI (MIA-UX-04) and no real-speech verification (`OWNER_VERIFICATION_REQUIRED`).

## Voice update - MIA-VOICE-03 (agent A4, 2026-11-21)

- **Implemented:** Roman Urdu and Urdu-script end-voice (`sunna band karo`, `khuda hafiz mia`, `سننا بند کرو`, ...) and stop-speaking (`chup ho jao`, `bas karo`, `چپ ہو جاؤ`, ...) commands, in `src/mia/assistant.html`. Commands apply only to short utterances, so a long question containing the same words is still sent to Mia.
- **Tests actually run (Chromium + Playwright, including `test_voice_browser.py` and `test_browser_automation.py`):** full suite 1781 passed, 1 skipped, 0 failed; `ruff check src tests` clean. 15 of the 26 new tests fail against the previous `assistant.html`.
- **Correction:** earlier entries said Chromium tests could not be run here. They can; they were skipped only because the test venv lacked the Python `playwright` package. 48 such tests passed on 2026-11-21 before this change.
- **Not verified:** real spoken Urdu through Whisper or the browser speech service (transcript spellings vary, so some phrasings will not match), and `bas karo` is an everyday idiom that may occasionally stop speech when said to someone else in a short utterance.

## Voice update - MIA-VOICE-04 (agent A4, 2026-11-21)

- **Implemented:** the offline wake-word routes (`WS /api/voice/wake/stream`, `GET /api/voice/wake/status`, `POST /api/voice/wake/stop`) are registered by `create_app`. Nothing listens at startup; arming needs the WebSocket opt-in message, loopback client, same-origin and no proxy headers. `offline_wake_word.operational` now also requires a WebSocket runtime (`websockets` or `wsproto`), used by both `/api/voice/status` and Mia's runtime self-description.
- **Tests actually run:** full suite including Chromium: 1835 passed, 1 skipped, 0 failed; `ruff check src tests` clean; 8 new integration tests (4 of 4 wiring mutants caught).
- **Still not operational end to end:** no UI (MIA-UX-04, A6) and no real-speech or real-microphone verification (`OWNER_VERIFICATION_REQUIRED`). `operational` cannot know whether the server is bound to loopback; a remotely hosted Mia would report operational while the transport correctly refuses non-loopback clients.

## Voice update - MIA-UX-04 (agent A4, 2026-11-21)

- **Implemented:** an opt-in "Offline wake word" panel in `src/mia/assistant.html`. It reads `GET /api/voice/wake/status`; the button is enabled only when the engine, an owner-supplied model and a WebSocket runtime exist, otherwise it states what is missing. Pressing **Listen for wake word** is the opt-in: only then does the page open the WebSocket, send the opt-in message and ask for the microphone. A persistent badge ("Listening for \u201cHey Mia\u201d \u00b7 offline \u00b7 this computer") and a **Stop listening** button are visible while active; the microphone and socket are released on Stop, on any server stop (shown with its reason), on hand-off and on page hide. A detected wake word stops the detector and starts the existing voice conversation; starting a voice conversation or Local record stops the wake word first so two microphones never run together.
- **Tests actually run:** 10 new Chromium tests (`tests/test_voice_wake_ui.py`: real AudioWorklet and WebSocket against a real uvicorn server with Chromium's fake microphone device and a fake detector). Full suite including all Chromium files: 1846 passed, 1 skipped, 0 failed; `ruff check src tests` clean. Mutation check of the page logic: 10 of 11 mutants caught (the 11th, removing the page-hide cleanup, is behaviourally equivalent because the server stops on socket close); an earlier run of this check was invalid because the sandbox lost `PLAYWRIGHT_BROWSERS_PATH` and every test skipped - it was redone.
- **Not verified:** real speech and real wake-word detection (the fake microphone plays a generated tone and the detector is fake), real microphone hardware, a Windows install, screen-reader and keyboard-only use, narrow-screen layout. The hand-off starts the voice conversation without a user gesture, so a browser may still block the *browser* speech engine; the local engine and the existing error path are unaffected. `OWNER_VERIFICATION_REQUIRED` (section G of `docs/voice/OWNER_VERIFICATION.md`).

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

## A6 / MIA-UX-05 - Microphone visibility and stop safety (2026-11-21)

- Audit finding fixed: the one-shot **Record** button never updated the always-visible mic badge (it read "Mic off" while recording), kept the microphone open until after transcription finished, ignored Esc, and was not released on page hide. Now the badge shows "Mic on - Recording", the button exposes `aria-pressed`, the microphone is released the moment recording stops (before the transcription request), Esc stops recording (checked before speech interruption), and `pagehide` discards an active recording and releases the mic. Microphone use stays opt-in (user-initiated click only); nothing here starts capture automatically.
- Evidence: `tests/test_assistant_mic_safety.py` (3 tests; the Node harness runs the real `startRecording` with a fake microphone/recorder and a pending transcription request; all fail against the previous page). Full local run: 1780 tests, 1778 passed, 2 skipped, 0 failed; `ruff check src tests` clean.
- Not verified: real browsers/microphones (browser mic indicator, permission prompts), mobile Safari/bfcache behaviour, screen readers. OWNER_VERIFICATION_REQUIRED. Voice Conversation mode and wake-word code were not changed.

## A6 / MIA-UX-06 - Read-only Quality controls panel (2026-11-21)

- The AI Gateway dashboard (`/gateway`) now has a **Quality controls** section showing the effective advisory-judges policy and counters (`/api/brain/judges`) and the quality-escalation policy and counters (`/api/gateway/escalation`). Both are off by default and the panel says so; it has no switches or write requests. It uses the server's own authority wording ("advisory only...") and counter scope notes, shows paid escalation as "Requested but not effective" when a cap blocks it, and one failing endpoint only marks its own card Unavailable. Values render as text (hostile strings are not parsed as markup).
- Also fixed a pre-existing defect: `src/mia/gateway/dashboard.html` had ~6 KB of duplicated, truncated script after `</html>` (present since the first integration commit), which browsers append to the page as visible text. It was removed and a regression test added.
- Evidence: `tests/test_gateway_quality_panel.py` (3 tests; all fail against the previous dashboard; a Node harness renders the page's real functions from the real live API payloads in default and enabled configurations). Full local run: 1855 tests, 1854 passed, 1 skipped, 0 failed (a first run showed 1 failure in `test_voice_wake_transport.py`, which also failed on the commit before this change; it was this sandbox missing the declared `websockets` dependency and passed once installed); `ruff check src tests` clean.
- Not verified: real-browser rendering/layout (including the new 2-column grid on narrow screens) and screen readers. OWNER_VERIFICATION_REQUIRED. Counters are process-local and reset on restart, as the API states.

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

## 2026-11-21 Judges in the turn pipeline (MIA-EVAL-03, agent A3)

- **Implemented:** advisory judges now run after verification when the owner opts in (`MIA_JUDGES_ENABLED=true`; off by default). Judges can only lower confidence, never change the reply, permissions or approvals; sensitive/memory-using or local-only turns use local judges only; remote and paid judges need separate opt-ins and the gateway route policy; failures fall back to the deterministic verifier. Trace event `judges`, `verification.judges`, and read-only `GET /api/brain/judges`.
- **Verified here:** 28 new fake-provider tests; full suite 1783 passed, 2 skipped (commit 9c29972).
- **Not done:** streamed turns, owner UI panel (MIA-UX-06), live judge-model quality (OWNER_VERIFICATION_REQUIRED).

## 2026-11-21 A1 retention policy and live self-description fix (MIA-BRAIN-04, MIA-BRAIN-05)

- Structured-memory retention is implemented as an owner action (commit `ee6fe3f`): configurable period (default keep), dry-run preview, retired records only, audited, never automatic. This closes the "retention policy for structured records" gap listed earlier in this file.
- **Correction to earlier status:** the live runtime-evidence block that lets Mia describe its own capabilities was never being attached to chat prompts because of a swallowed `AttributeError` (`ProviderRegistry.keys()`); fixed in `0d22d02`. Until then, self-description relied on the persona text alone. Wake-word capability is now derived from `offline_wake_word_status()` evidence instead of route names.
- Local evidence on the rebased tree: **1740 passed, 17 skipped**, ruff clean; scripted providers only. Not verified: behaviour with live models. Open follow-ups: record-level project scoping (MIA-BRAIN-06) and bounding the larger system prompt (MIA-BRAIN-07).


## 2026-11-21 Judges: streaming, voice, scenarios (MIA-EVAL-04, agent A3)

- **Implemented:** streamed text turns are judged after the reply is stored; voice turns are never judged; 3 judge scenarios in the scenario runner (26 total, 26/26 pass).
- **Verified here:** full suite 1791 passed, 2 skipped (before final rebase); targeted suites re-run after it (commit bd50db0).
- **Not done:** owner UI panel (MIA-UX-06, A6); live judge-model quality (OWNER_VERIFICATION_REQUIRED).

## 2026-11-21 — MIA-GW-04 (A2): gateway-level provider timeouts

- Non-streaming and buffered-stream provider attempts now have a gateway timeout (default 180s; idle stream default 120s; env overridable). Timeouts feed fallback + circuit breaker, are not retried on the same provider, cancel the hung call, and keep a paid reservation until expiry.
- Verified locally: 9 new hung-fake-provider tests pass (one mutation-checked: disabling the reservation rule makes it fail). Full suite on my final run (websockets installed): 1850 passed, 1 skipped, 0 failed. My first run had 1 failure (`tests/test_voice_wake_transport.py::test_status_endpoint_reports_session_and_capability_but_no_arming_route_exists`): `websockets` is only in the optional `voice-wake` extra, so a plain install lacks it and `audio_transport_available` is False. It passes once `websockets` is installed. Not a product defect; the test depends on an optional extra without a skip/guard (suggestion for A4). No live-provider claim.

## 2026-11-21 M1 traceability and conversation-eval coverage (MIA-M1-08 / MIA-EVAL-06, agent A3)

- **Implemented:** `docs/M1_TRACEABILITY.md` maps each Priority 1 exit criterion to source, tests and owner checks. `mia.brain.evaluation` grew from 26 to 31 scripted scenarios: identity and capability inventory (built from the real runtime-manifest renderer; English and Roman Urdu), and provider-switch continuity (English and Roman Urdu; the first provider is disabled between turns). `COVERAGE_MANIFEST` / `coverage_report()` make a test fail if a roadmap area has no scenario or named test file.
- **Tests actually run:** `python -m mia.brain.evaluation` 31/31; `pytest tests` excluding 8 Chromium files: 1815 passed, 1 skipped, 0 failed; `ruff check src tests scripts` clean; two mutants of the new harness behaviour (no provider switch, identity text ignored) each failed the new tests. Chromium files not run by A3.
- **Not claimed:** live-model quality (OWNER_VERIFICATION_REQUIRED). Voice turn-taking and interruption are covered by browser tests outside the suite.
- **Open finding handed to MIA-M1-09:** `assistant.html` never sends `interface="voice"`, so real spoken turns are recorded as text and are not exempt from judge latency.

## 2026-11-21 — MIA-GW-05 (A2): model-not-found fallback

- Audit (source trace of `ModelGateway.complete/stream`, provider error mapping, health, breaker, reservations): local-first routing, discovery, fallback on transient failures, rate-limit blocking, cancellation, timeouts, reservations and ledger behave as documented. One gap: any HTTP 4xx other than 401/403/429 became `PermanentProviderError`, which aborts the whole candidate chain. A model deleted or renamed at its provider (HTTP 404/410, e.g. a stale Ollama registry entry ranked first) therefore failed every turn even when other eligible candidates were healthy.
- Fix: `ModelUnavailableError(PermanentProviderError)`; adapters raise it for HTTP 404/410; `complete()`/`stream()` fall back to the next candidate (before any streamed text), degrade only that candidate in the health tracker (so it is ranked after healthy ones next turn), leave the circuit breaker alone, and release a paid reservation (the request was rejected before processing). If every candidate is gone a permanent `ModelUnavailableError` is raised (502 via the existing handler, no misleading retry hint); a transient failure elsewhere still takes precedence. HTTP 400/422, credential, and empty-output semantics are unchanged.
- Verified locally: `tests/test_gateway_model_unavailable.py` (13 tests, fake providers + mocked HTTP); mutation-checked (removing the handlers or the 404/410 mapping fails the tests). Full suite: 1925 collected, no failures, 1 skipped (Chromium path not configured), ruff clean.
- Not covered / follow-ups: the model registry is not yet marked stale on a 404 (health degrade only, process-local); a per-turn total deadline across fallback candidates (noted in GW-04) is still open; no live-provider evidence.

## 2026-11-21 — MIA-M1-10 (A6): memory delete/export owner controls

- **Implemented (commit 3703885):** retention purge now removes usage rows and is audited; conversation deletion is audited, reports the structured memories captured from it, and can opt in to forgetting them (`DELETE /api/conversations/{id}?forget_derived=true`, preview at `GET /api/conversations/{id}/derived-memory`); export carries each conversation's `temporary` and `project`.
- **Verified here:** 15 new tests; full suite 1926 passed, 1 skipped before the final rebase; ruff clean. Scripted data only.
- **Not done:** UI for these actions, import/restore, audit log in the export.

## 2026-11-21 Live evaluation harness (MIA-EVAL-05, agent A3)

- **Implemented:** `python -m mia.brain.live_eval [--models a,b] [--json]` (`src/mia/brain/live_eval.py`): five fixed probes (seeded codeword recall, no invented memory, Roman Urdu and Urdu-script follow-up, no false completed-action claim) run through the real Brain against a pinned local model, with a fresh temporary memory store per probe, no tools, paid routing off, at most 3 models and 128 output tokens per reply. The report states pass counts, sample size and limits.
- **Tests actually run:** `tests/test_live_eval.py` 6 passed with scripted fake providers; mutants (no pinning, no local-only filter, no model cap) each failed tests; `pytest tests` excluding 8 Chromium files at commit e63d04c: 1888 tests, 0 failed, 1 skipped; ruff clean.
- **Not verified (OWNER_VERIFICATION_REQUIRED):** any real run against the owner's Ollama models. Five probes are a smoke check, not a quality benchmark; keyword checks can mis-grade unusual phrasings. Pinning uses a wrapper rather than routing preferences.

## 2026-11-21 — MIA-GW-06 (A2): per-turn deadline across fallback candidates

- Gap (documented in GW-04): each provider attempt was capped at 180s, but several hung candidates still made the owner wait attempts x 180s for a first answer. Added `turn_deadline` (default 300s, `MIA_GATEWAY_TURN_DEADLINE_S`, `off`/`0` disables, invalid values fall back to the default; constructor validates 0 < x <= 3600).
- Behaviour: `complete()` and `stream()` clamp every attempt (and the wait for the first streamed chunk) to what is left of the turn budget and stop starting new candidates once it is spent, raising the usual aggregated transient error (cause `ProviderTimeout`) and recording a `deadline` event. A stream that is already producing text is never cut off by the deadline (the idle timeout still applies). Consumer cancellation still propagates. A timed-out attempt is treated like any attempt timeout (health/breaker failure, paid reservation held until expiry).
- Verified locally: `tests/test_gateway_turn_deadline.py` (10 tests, hung/slow fakes with tiny real timeouts); three mutation checks (no loop-top check, no clamp, stream guard cutting off active output) each fail the intended tests. Full suite on the rebased tree: no failures, 1 skipped (Chromium path unset); ruff clean.
- Limits: the budget bounds time to a first answer, not total generation time; the attempt that crosses the deadline is penalised like a normal timeout; the default (300s) is a judgement call for slow local cold-loads and is owner-tunable; no live-provider evidence.

## 2026-11-21 — MIA-HOLO-01 (A6): runtime-state contract

- **Implemented (commit 1691e42):** a typed, evidence-based interface state contract and read-only routes (`/api/runtime/contract`, `/state`, `/resolve`). Nothing renders yet; no activity is inferred from animation.
- **Verified here:** 41 new tests; full suite 2050 passed, 1 skipped; ruff clean.
- **Not done:** UI binding (MIA-HOLO-04); server-side tracking of in-flight chat turns.

## 2026-11-21 — MIA-GW-07 (A2): empty-model-output fallback

- Gap: an empty or unusable reply from one model (gateway "empty response", "only invalid tool calls", Ollama/OpenAI-compatible "neither text nor tool calls") raised `PermanentProviderError`, aborting the whole fallback chain even though it is model output rather than a request fault, and structured-output failures already fell through to the next candidate.
- Fix: `EmptyResponseError(PermanentProviderError)` raised by the adapters (empty text and no tool calls; non-string payload types stay plain permanent) and the gateway. `complete()`/`stream()` record an `invalid_output` event, keep the provider healthy (breaker/health untouched), hold a paid reservation until expiry (tokens may have been billed) and try the next candidate. If every candidate fails the error stays permanent ("No model produced a valid response: ..."), now also for streams. 400-style request faults, credentials and a single failing candidate behave as before.
- Test hygiene fix: free-first routing meant the paid-reservation test I wrote for GW-05 never actually attempted the paid candidate. It now uses the paid candidate alone and a mutation proves it bites.
- Verified locally: `tests/test_gateway_empty_response.py` (10 tests) plus corrected GW-05 test; four mutation checks (handlers removed, reservation hold removed, adapters plain, gone-model hold added) each fail the intended tests. Full suite on the rebased tree: no failures, 1 skipped (Chromium path unset); ruff clean.
- Limits: an empty reply is not retried on the same model; no quality scoring of weak-but-nonempty replies (Brain verification/escalation own that); no live-provider evidence.

## 2026-11-21T21:01Z A1 record-level project scoping (MIA-BRAIN-06)

- Structured memory records can be scoped to a project (opt-in, default unscoped). Project conversations never recall another project's records, including via links; capture in a project conversation tags records and never confirms/supersedes another project's. Commit `44f45e6`. Verified with fake-provider/unit tests only; no UI and no live-model evidence.

## 2026-11-21 — MIA-HOLO-02 (A6): renderer spike and decision

- **Implemented (commit 1176c8e):** isolated three.js spike with WebGL detection, text fallback, reduced motion, hidden-page pause, fps cap, context-loss handling and disposal; decision doc with measured bundle sizes and licences. Not wired into the app.
- **Verified here:** 8 Chromium tests on software GL; full suite 2070 passed, 1 skipped.
- **Not verified:** real-GPU performance, Windows hardware, accessibility with assistive tech.

## 2026-11-21T21:11Z A1 personal data model (MIA-LIFE-01)

- Owner-scoped tasks/reminders/notes (`personal_items`, migration 6) with provenance, project filter, hard delete, owner-triggered retention, export and REST API. Not in model context, not encrypted at rest, no UI. Commit `5c5f14f`; tested with unit/API tests only.

## 2026-11-22 M1 evidence report (MIA-M1-13, agent A3)

- Delivered `docs/M1_EVIDENCE_REPORT.md`: criterion-by-criterion automated completion versus owner-only checks, run on 5a64019 (eval 31/31; 2119 tests, 0 failed, 1 skipped, Chromium tests included; ruff clean). **M1 acceptance is pending the owner's checks**; nothing in the report claims live-model quality.

## 2026-11-22T09:37Z A1 task manager (MIA-LIFE-02)

- Tasks gain priority, recurrence (owner-timezone, DST-safe), duplicate refusal, structural history and an owner_timezone setting. No UI or Brain intents yet. Commit `3dc0fa5`; unit/API tests only.

## 2026-11-22 — MIA-HOLO-03 (A6): hologram scene

- **Implemented (commit 0df4a2d):** `/hologram`, a responsive WebGL scene with ten state looks (motion and shape, not colour alone), text fallback, reduced-motion and hidden-page handling. Preview only.
- **Verified here:** 22 Chromium tests on software GL; full suite 2132 passed, 1 skipped.
- **Not done:** live event binding (HOLO-04), accessibility audit (HOLO-05), GPU profiling and budgets (HOLO-06).

## 2026-11-22T09:46Z A1 project workspace data/API (MIA-LIFE-04, A1 half)

- Projects gain a goal, status and milestones plus an isolated workspace view over tasks, decisions and non-temporary chats (migration 8, /api/projects). No UI, no rename, not yet in Brain context. Commit `b75556d`; unit/API tests only.

## 2026-11-22 Language-aware local TTS voice selection (MIA-LANG-01, agent A7)

- **Gap (source-read, from the MIA-M1-11 audit):** local TTS had no language-aware voice choice, so Urdu text was spoken by whatever default voice the OS had.
- **Implemented (commit 61be039):** `src/mia/voice/voice_select.py` (pure selection over installed-voice metadata) and small hunks in `src/mia/observatory/api.py`. `POST /api/voice/speak` accepts optional `language` (`auto`/`en`/`ur`). An explicit `voice_id` always wins. Urdu script uses an installed Urdu voice only (Hindi/Arabic/Persian voices are never substituted). Roman Urdu uses an English voice and is flagged approximate (`X-Mia-Voice-Approximate`). With no matching voice the system default is used and `X-Mia-Voice-Selection: no_matching_voice` says so. Selection, language and approximation are recorded in the speech event (spoken text is still never logged). `GET /api/voice/status` now reports `local_tts_languages` (which of en/ur have an installed local voice). Without `language`, behaviour is unchanged.
- **Tests actually run:** `tests/test_voice_language_selection.py` (14 tests) plus the existing voice API/TTS/language suites passed. Two mutants (Hindi counted as Urdu; language overriding an explicit voice) each failed the intended tests. Full suite excluding the four Chromium files (`test_voice_browser`, `test_voice_wake_ui`, `test_browser_automation`, `test_holo_renderer_spike`): exit code 0, no failures, on the tree before the final rebase; after the rebase only the new file and `test_voice_api.py` were re-run (passed). `ruff check src tests` clean. Chromium suites not run (no HTML changed).
- **Not done / not verified:** the Assistant UI still always sends an explicit `voice_id`, so the owner does not yet get this behaviour (MIA-LANG-02). No real Urdu voice was tried; whether the owner's Windows machine has an Urdu SAPI voice installed, and how it sounds, is `OWNER_VERIFICATION_REQUIRED`. Id/name matching is heuristic (language tags, `xx-YY` id tokens, "Urdu"/"English" in the name).
- Code Graph snapshot was not regenerated in this session.

## 2026-11-21 — MIA-LIFE-12 (A2 + A6 slot, owner-approved M3 exception): usage and resource dashboard

- Owner approved an exception to the M2->M3 gate in chat ("Approve an M3 exception") in reply to A2's offer naming this task. Dependencies (gateway ledger, resource monitor, schedulers) were already in place.
- Added `src/mia/observatory/usage_dashboard.py`, `GET /api/usage/dashboard` and a read-only "Usage & resources" panel on the gateway console (`dashboard.html`): inference usage and latency, spend and budgets, CPU/memory (host and Mia process), storage, background workload. Sections degrade independently with `status`, `source`, `measured_at` and a generic reason; unmeasured values are `null` and render as Unknown, not zero. The existing `/api/system/resources` 503-on-missing-psutil behaviour is unchanged; the dashboard reports that section as unavailable instead of failing.
- Verified locally: `tests/test_usage_dashboard.py` (14 tests: real `UsageLedger` rows, injected psutil/disk fakes, live-app API test, and the page's real JS under Node against the live payload plus null, unavailable, endpoint-failure and hostile-string fixtures); five mutation checks (unknowns coerced to zero in backend, in UI; symlink following; hidden truncation; leaked error text) each fail the intended test; existing quality-panel tests still pass.
- Limits: JS logic under Node, not a real browser (no screenshot taken); workload shows on/running flags only, not queue depth or CPU per worker; Mia CPU is cumulative seconds, not a rate; no GPU, network or per-process disk I/O; no history or trends, only the current snapshot plus the ledger window; not tested against a real provider mix.

## 2026-11-22T09:54Z A1 daily/weekly briefing (MIA-LIFE-05)

- GET /api/briefing builds a deterministic, source-cited briefing from tasks, reminders, milestones and goals in the owner's timezone; says plainly that no calendar is connected; hides private items by default; reports quiet hours as a delivery flag only. No scheduler, UI or Brain intent. Commit `e5ea839`; fixed-clock unit/API tests only.

## 2026-11-22 Assistant "Auto - match language" voice option (MIA-LANG-02, agent A7)

- **Implemented (commit 81ee534, `src/mia/assistant.html`):** an opt-in **Auto · match language** local voice choice. It sends `language: "auto"` and no `voice_id` to `/api/voice/speak` (MIA-LANG-01); explicit local and browser voices behave as before. The option label says "(no Urdu voice installed)" when `/api/voice/status` reports none. The page tells the owner when no installed voice matches the text's language (default voice used) and when Roman Urdu is read by an English voice (approximate). It is not the default selection, so existing voice choices are unchanged.
- **Tests actually run:** 5 new Chromium tests in `tests/test_assistant_voice_language.py` (voice routes mocked); 3+ of them fail against the previous page. Full suite including all Chromium files: exit code 0, no failures, before the final rebase; after rebasing over another agent's `assistant.html` change, `test_assistant_voice_language`, `test_voice_browser`, `test_assistant_stop_reply` and `test_voice_ui_contract` were re-run and passed. Chromium ran with Playwright 1.63.0 against /opt/pw-browsers.
- **Not verified:** a real Urdu voice, real playback, screen-reader and narrow-screen behaviour of the new option; `OWNER_VERIFICATION_REQUIRED`.

## 2026-11-22 — MIA-LIFE-11 (A2): encrypted backup and restore

- Owner approved an M3 exception for A2 in chat and said "continue" after A2 named this as its next M3 task (recorded on the board; the owner can stop it at any time). A5 was busy and not consulted.
- Added `src/mia/life/backup.py`, the `mia-backup` CLI (`src/mia/backup_cli.py`), an optional `backup` extra (`cryptography`, also in `dev`) and `docs/architecture/BACKUP_RESTORE.md` (key management, format, policies, limits). Passphrase-derived key (scrypt), AES-256-GCM chunks authenticated with header, index and final flag; consistent online SQLite snapshots with integrity checks; manifest with SHA-256; verify and read-only preview; restore policies `abort` (default), `overwrite` (undo copy, stale WAL/SHM moved aside) and `side_by_side`; atomic placement with rollback; fail closed on any invalid or partial archive.
- Deliberately excluded: provider API keys (OS credential store, not in the data folder) and the computer permission/approval stores (restoring old grants could silently re-grant revoked permissions). The spend ledger is never overwritten in place.
- Verified locally: `tests/test_life_backup.py` (55 tests: real SQLite and real crypto, byte-flip/truncation/append/reorder at every region and on the crypto layer alone, 18 forged-but-validly-encrypted hostile payloads, corrupt sources, failure injection with rollback, no leftover plaintext staging, CLI). Ten mutation checks (unauthenticated final flag, nonce/index, KDF bounds, checksum, integrity, ledger overwrite, stale sidecars, rollback, grants exclusion, manifest binding) fail the intended tests. One mutation initially survived because truncation was only caught by a later layer; crypto-layer tests were added to close it.
- Limits: local file only; no UI, schedule, incremental backups, key rotation, remote target or cross-version migration; a lost passphrase is unrecoverable by design; not tested with very large databases or Windows file locking; restoring into a data folder in use by a running Mia is unsafe (documented).
- **MIA-HOLO-04 (A6, 2026-11-22T15:55Z):** hologram bound to real chat/voice/offline events plus server task/approval state; reconnect, cancel and offline tested in Chromium. Not verified: real GPU, mic, model. Next: HOLO-05, HOLO-06.

## 2026-11-22T16:00Z A1 proactive stalled-work suggestions (MIA-LIFE-14)

- POST /api/life/suggestions/run evaluates idle goals and idle tasks through the shared outreach policy (dry run by default), reports why candidates were suppressed, withdraws pending suggestions whose evidence changed, and honours opt-out and emergency stop. Not scheduled, no UI. Commit `6f52281`; unit/API tests only.

## 2026-11-22 Accessible task workspace UI (MIA-LIFE-03 UI half, agent A7)

- **Implemented (commit 466517f):** `GET /tasks` serves `src/mia/life/tasks.html`, a workspace over the existing `/api/personal/items` API (the same store Mia's conversations use). Add, edit (including clearing due date/project), complete, reopen, cancel and delete; filter by status/type and search; select-all and bulk complete/reopen/cancel/delete. Delete and cancel (single or bulk) open a modal that names the items (first 10, then "and N more") and focuses the safe choice; nothing changes before confirming. Titles and details are rendered as text only (conversation-captured text is untrusted). Overdue is stated in words. A duplicate is reported and added only on a second deliberate press. Linked from `/life`.
- **Tests actually run:** 15 Chromium tests in `tests/test_tasks_workspace.py` against the real server and SQLite store (nothing mocked) covering the above plus keyboard completion, accessible names on every control, 360px width without horizontal scroll, reduced-motion media, load-failure alert. Three injected bugs (unescaped titles, bulk delete without confirmation, focus on the destructive button) each failed the intended test. Full suite (all Chromium files included) exit code 0 on the final rebased tree; ruff clean.
- **Not done / not verified:** Brain/conversational task actions and "one source of truth" proof from the chat side (A1's half; LIFE-03 stays open for it). No screen reader (NVDA/Narrator) run, no automated contrast audit, no real touch-device check: `OWNER_VERIFICATION_REQUIRED`. Bulk actions are sent one item at a time (not atomic); failures are listed, successes are kept. Completing a recurring item creates its next occurrence (store behaviour), so "Reopen" does not undo that. Private items show their title on this owner-only page. The delete confirmation says it cannot be undone from this page; whether the audit history remains was not checked here.

- **MIA-HOLO-05 (A6, 2026-11-22T18:05Z):** accessible text/shape view, contrast/target/reflow/keyboard/forced-colors/reduced-motion checks for `/hologram`. Not verified: real screen readers, other browsers. Next: HOLO-06.

- **MIA-HOLO-06 (A6, 2026-11-22T20:10Z):** hologram budgets and graceful degradation; HOLO-01..06 complete. Not verified: real-GPU frame times, power, low-end phones.

## 2026-11-24 Security threat model, loopback guard and route inventory (MIA-COMP-01 first slice, agent A7)
- Commit 3162dd1. Verified before the change: the API accepted a foreign Host/Origin on mutating requests and on the wake WebSocket. Now refused (400/403/close 1008) when `local_guard=True`, which the production `app` sets. Tested in `tests/test_local_guard.py`.
- Every mutating route is classified in `mia.security.capabilities`; a test fails on any unclassified new route. Classifications are declared, not proven per handler. This is not an enforcement layer (COMP-04).
- Not claimed: protection against other local processes, real-browser rebinding test, device identity.

## 2026-11-22 — MIA-OBS-03 (A2): pausable, explicit-day snapshot scheduler

- Claimed on the owner's repeated "continue" (same convention as A3/A6 out-of-gate claims); A5 busy and not consulted. Audit of the existing `ExperimentSnapshotScheduler` found opt-in default, CPU/RAM deferral, failed-capture and offline-day records already present. Gaps closed: no runtime pause/resume; a telemetry-unavailable deferral was returned in memory but never recorded; a day with nothing to capture left no record; the final report would have shown an owner-paused day as "unaccounted".
- Added `pause()`/`resume()` (durable: newest paused/resumed event; restart-safe; idempotent; 409 via `POST /api/experiments/scheduler/pause|resume` unless opted in), `daily_snapshot_skipped_paused` (once per experiment-day), `daily_snapshot_deferred_telemetry_unavailable` (error type only), `daily_snapshot_scheduler_noop_day` (once per UTC day, restart-safe), a read-only `ObservatoryStore.find_events` query and one additive line group in the report builder so paused days are `unavailable`, not `unaccounted`. Existing scheduler and report behaviour is unchanged.
- Verified locally: `tests/test_experiment_scheduler_days.py` (23 tests: durability across restart, idempotency incl. concurrent runs with no false failure, one explicit event per non-capture case, no-network proof with sockets blocked, API, store query, report accounting); eight mutation checks each fail the intended tests; existing experiment tests unchanged and passing.
- Limits: no pause windows or per-experiment pause; the no-network claim covers the capture path with a fake baseline, not third-party tools the real baseline runs; days lost to a deferral or failure never retried before the window closes stay "unaccounted"; no real multi-day run was performed.

## 2026-11-24 Execute-boundary adversarial tests (MIA-COMP-04 slice 1, agent A7)
- Commit 354a752. Verified over HTTP against the real filesystem adapter: no/forged/pending/rejected/expired/mismatched/replayed approvals and out-of-scope, revoked or wrong-capability grants never change the filesystem.
- Not covered yet: terminal, processes, apps, desktop, browser click/actions, background tasks, Brain, voice. Local-process self-approval is an open gap (COMP-02/LIFE-13).
- Known unrelated failure on main: clock-dependent quiet-hours test in `tests/test_life_web.py`.
- **MIA-LIFE-04 UI half (A6, 2026-11-24T06:45Z):** `/projects` workspace page over the A1 project API. Open: rename, Brain context, '/' in names (A1). Time-dependent failing test noted in the integration log.

## 2026-11-24T06:25Z A1 conversational task actions (MIA-LIFE-03, Brain half)

- Clear English task commands in chat are answered by the Brain without a model call, on the same store as the task page and API; deletes and bulk changes need an explicit yes; private tasks are invisible to chat and temporary chats are read-only. English only. Commit `5424535`; unit and API tests only. A time-of-day-dependent life test fails on clean main inside the 22:00-07:00 UTC quiet window.

## 2026-11-24 Workflow composer and checkpoint runtime (MIA-LIFE-08 backend, agent A7)
- Commit 4510573. Owner can compose a workflow of internal steps (add/list/complete tasks and notes, queue a notification), preview it without side effects, run it with a checkpoint after every step, pause/cancel it, retry a step explicitly, and read the audit trail. Stop-all pauses workflows.
- Refused by design: computer, browser, terminal, filesystem, desktop, app and delete steps (plan-time error). No UI page and no Brain/voice entry yet.
- Not verified: behaviour under real concurrent use; timed-out steps have unknown outcome by design.

## 2026-11-24 — MIA-OBS-08 (A2, backend slice): owner stop, verifiable export, privacy notice

- Owner approved Milestone 6 work in chat ("you can work on Milestone 6"); A1/A6 idle and not consulted. Audit: prepare/start/capture/report and the global scheduler pause/resume already existed; missing were an owner stop, an export the owner can verify, and a privacy notice with resource caps.
- Added `POST /api/experiments/{id}/stop` (terminal `stopped`, conclusion `stopped_by_owner`, never completed; days whose window never opened are `not_run_after_stop`, not `unaccounted`), `GET /api/experiments/{id}/export` (hash-verified report bundle with a content hash; fails closed with 409), `GET /api/experiments/privacy-notice` (derived from `FIELD_DISCLOSURE`, live caps) and an optional start body that records the notice version and caps. Offline verifier: `python -m mia.observatory.experiment_export verify FILE`.
- Verified locally: `tests/test_experiment_lifecycle.py` (29 tests: stop semantics, day accounting, export integrity, tamper detection including edits whose outer hash is recomputed and self-consistent snapshot rewrites, fail-closed on corrupted storage, offline CLI, and a drift test that runs the real baseline and snapshot code against the notice). Eleven mutation checks fail the intended tests; two survived at first (the verifier's per-item checks were masked by the outer hash) and drove an added baseline-link cross-check and rehash tests. Existing experiment tests pass unchanged.
- Limits: the export hash is a checksum, not a signature (documented; anchor `content_sha256` elsewhere); no dashboard controls (OBS-06 / A6); the privacy acknowledgement is recorded not enforced; the notice describes what the code records today and is guarded by a test, not by a legal review; no real 30-day run.

## 2026-11-24 Scoped research with provenance and verified citations (MIA-LIFE-07 backend, agent A7)
- Commit 9668d84. Owner supplies 1-5 URLs on hosts they already allowlisted; Mia reads them read-only, records provenance, compares sources by an explainable checkability score, and accepts a claim only if every citation is a verbatim quote from a source in that run. Instruction-like page text is flagged and cannot be cited; owner confirm/reject turns a claim into a finding.
- Not done / not verified: research UI page, search step, model synthesis, saving findings into long-term memory; live internet fetch not run (mock transport only); pattern-based injection detection is not exhaustive.
- **MIA-OBS-06 extension (A6, 2026-11-24T11:30Z):** `/trends` page with per-day coverage and gap-preserving charts, additive to A3's Evidence panel. Not verified: real 30-day data, other browsers.

## 2026-11-24 Stop-all now blocks app launch and file writes (MIA-COMP-04 slice 3, agent A7)
- Commit d6e1573. Previously, while Mia was stopped, a granted app launch or file write still ran. Both are now refused (423) without consuming the one-time approval; resume restores them. Covered by tests against the real app; app launch is proven by a recorded no-spawn, process termination by a real child process left running.
- Still not covered: desktop and browser actions over HTTP, background tasks, Brain, voice; no live desktop or real-device stop drill.
- **MIA-LIFE-07 UI half (A6, 2026-11-24T12:59Z):** `/research` page with provenance, injection marking, verbatim citations and owner review. Owner check: one real-site read.

## 2026-11-24 — Workflow UI (MIA-LIFE-08, agent A6)
- Commit c0311f2. `/workflows` lets the owner build a plan from the safe internal actions, preview risk and approval, run, pause, cancel (with confirmation) and retry failed steps, and read events and the report.
- Verified: 17 Chromium tests over the real workflow engine, mutation checks, full suite, ruff. Not verified: real screen readers, non-Chromium browsers.

## 2026-11-25T05:33Z A1 capability-gap discovery (MIA-EVOL-01)

- /api/evolution/gaps records and ranks repeated, consented signals (failed tasks, memory corrections, submitted evaluation failures, explicit owner requests); consent is off by default and revoking deletes evidence; one failure is never a gap. Records only: nothing runs, no UI, not wired to the evolution pipeline. Commit `02c96d1`; unit/API tests only. Known failing on clean main: A6's /projects delete-notice browser test.

## 2026-11-25 Model-tool surface and approval race tests (MIA-COMP-04 slice 4, agent A7)
- Commit 6b3f5f8. The model and voice can reach only read tools and approval-creating previews; previews leave files and processes untouched; one approval cannot be spent twice even under 8-way concurrency.
- Still open: local-process self-approval (needs caller identity, COMP-02/LIFE-13); owner drill of stop-all on real hardware.

## 2026-11-25 Companion pairing and device identity (MIA-COMP-02, agent A7)
- Commit c955516. A companion pairs with a one-time code, proves it holds its private key, logs in with a replay-proof challenge and receives a short scoped token. Owner can revoke one device or all at once, and sessions stop immediately. A device token can only call `/api/companion/*`.
- Not done: the companion itself (COMP-03), encrypted non-loopback transport (LIFE-13), owner authentication (a tokenless local process can still call owner routes), pairing UI. Real-device pairing is OWNER_VERIFICATION_REQUIRED.

## 2026-11-25 Selected-folder file index (MIA-LIFE-06 backend, agent A7)
- Owner-selected folders only; keyword search with line-range citations, staleness and live verification; untrusted/injection-flagged; real deletion; stop-all honoured. Owner check: index a real folder, search, delete, confirm nothing else was read. No UI yet.


## Workflow timeout recovery implementation checkpoint (2026-11-25)

MIA-LIFE-08-TIMEOUT has a first implementation on main. Workflow timeouts now preserve the underlying worker for reconciliation, expose an explicit reconciliation API, block duplicate execution while outcomes are unknown, and require an owner decision after restart before treating an interrupted step as applied or retryable. Regression tests were added for live-worker and restart paths. **Tests and static checks have not been run in this editing environment; this is an implementation checkpoint, not a verified completion claim.** See the task board and integration log for commit evidence.


The MIA-LIFE-08-TIMEOUT checkpoint was extended with owner-facing reconciliation controls. Interrupted steps no longer expose a generic retry action; direct retry is rejected until reconciliation, and cancellation waits for the unknown outcome to be resolved. UI, retry-bypass and cancellation regression tests were added. Test execution remains pending.


## MIA-LIFE-06-WATCH — implementation complete (2026-11-25)

An opt-in, owner-controlled file-index watcher now refreshes selected folders in bounded round-robin batches. It exposes start/stop/status endpoints, stops with stop-all and application shutdown, and refuses to start while automation is stopped. PDF and Office extraction now enforce the shared output-character budget while retaining optional dependency behavior and archive safeguards. Regression tests were added for watcher lifecycle, changed/deleted files, stop-all and extraction limits. **Tests were not executed here; execution remains OWNER_VERIFICATION_REQUIRED.**


## MIA-LIFE-06-BRAIN — implementation complete (2026-11-25)

Brain can now search the owner's selected file index through a read-only local-only tool. Results are live-verified against source text and file SHA, include exact path/line citations, and remain explicitly untrusted with injection flags. The Gateway receives those chunks for synthesis; system instructions prohibit following embedded file instructions and require exact citations. Remote-provider egress is blocked unless the owner explicitly enables remote file context. Regression tests were added; **test execution remains OWNER_VERIFICATION_REQUIRED**.


## MIA-LIFE-07-SEARCH — implementation complete (2026-11-25)

Mia now supports bounded owner-allowlisted search discovery and Gateway-backed research synthesis. Search results are untrusted suggestions filtered to allowed hosts; the owner selects pages before fetching. Synthesis uses a local structured-output model by default, with strict input/output/time budgets and explicit opt-in for remote models. It generates proposed claims only; every citation is checked against the same run's source text, and instruction-like passages cannot be cited. Stop-all and emergency-stop controls are honored. Regression tests were added; **test execution remains OWNER_VERIFICATION_REQUIRED**.


## MIA-LIFE-07-MEMORY — implementation complete (2026-11-25)

Owner-confirmed, source-cited research findings can now be imported into registered project-scoped structured memory. The bridge preserves source quotes, URLs, hashes and run/claim provenance; imports are idempotent; corrections require another confirmed claim and preserve the supersession chain; deletion removes the full chain. Cross-project retrieval is isolated by the existing project-context filter. Research UI and API controls are available. Regression tests were added; **test execution remains OWNER_VERIFICATION_REQUIRED**.


## MIA-LIFE-08-BRAIN — implementation complete (2026-11-25)

Brain and voice can now preview safe internal workflows through the same Brain tool planner. Execution requires a prior Brain/voice preview, a clear execution request in the current user turn, and an owner grant for the EXECUTE-level `workflow.run_internal` capability. Only personal task/list/complete and notification-queue handlers are exposed; external, browser, shell, filesystem and destructive actions are excluded. Workflow tool egress is local-only unless its separate explicit opt-in is enabled. Existing stop and timeout reconciliation protections remain active. Regression tests were added; **test execution remains OWNER_VERIFICATION_REQUIRED**.


## MIA-COMP-01-HANDLERS — implementation complete (2026-11-25)

Every mutating route is now bound at app construction to its live Mia handler identity and declared capability. Unclassified routes, missing/non-Mia handlers, and failed bindings stop app construction. Route-handler tests cover the full inventory and negative cases. This proves registration/binding, not execution-time approval enforcement; COMP-04 remains responsible for that. **Test execution remains OWNER_VERIFICATION_REQUIRED.**


## MIA-LIFE-13 — implementation complete (2026-11-25)

Authenticated companion sync is implemented with separate read/write device scopes, atomic versioned batches, conflict responses, tombstones, replay protection, payload limits and immediate revocation checks. Remote access is disabled by default and, when enabled, is limited to direct HTTPS on exact allowlisted hosts with a live device token. Pairing and owner APIs remain loopback-only. Only non-secret companion_state/preferences are synchronized. Regression tests were added but not executed; real TLS/device checks remain OWNER_VERIFICATION_REQUIRED. LIFE-13 now unblocks MIA-COMP-01-IDENTITY and MIA-COMP-04-OWNER.


## MIA-COMP-01-IDENTITY — complete (2026-11-25)

Added a source-backed owner/device identity and remote trust model. It documents Ed25519 pairing, device-scoped sessions, scopes, revocation, replay/conflict controls, optional HTTPS-only remote companion access and the no-public-unauthenticated boundary. It clearly states that local owner authentication is not yet implemented and remains COMP-04-OWNER work.
