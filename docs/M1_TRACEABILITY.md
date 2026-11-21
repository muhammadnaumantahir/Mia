# M1 (Priority 1) acceptance traceability matrix — MIA-M1-08

**Author:** A3 (Claude Sonnet 5.5 fresh session) · **Source head audited:** `431b220` (main); row 1 updated after MIA-EVAL-06 · **Status of M1:** NOT complete.
This is an evidence map, not a completion claim. "Automated" means a deterministic test with fake providers/engines; it never proves live-model quality, real speech or hardware behaviour.

## How the evidence was gathered (actually run)

- `python -m mia.brain.evaluation` → 26/26 scripted scenarios passed at audit time; 31/31 after MIA-EVAL-06.
- `pytest tests` excluding 8 Chromium/Playwright files (voice_browser, voice_wake_ui, browser_automation, assistant_error_recovery, assistant_mic_safety, assistant_stop_reply, assistant_project_control, gateway_quality_panel): 1808 passed, 1 skipped, 0 failed/errors. The run printed no final summary line, so these counts were tallied from the progress markers. Chromium files were **not** run by A3.
- Source tracing by grep/read of `src/mia/**`, `assistant.html`, tests and docs. Linux sandbox only; no Windows, microphone, speaker or live provider.

## Matrix

| # | Roadmap exit criterion | Source | Automated evidence | Gap / owner check |
|---|---|---|---|---|
| 1 | Repeatable conversation eval suite covers identity, capability inventory, memory recall, follow-up, cross-session/provider continuity, reasoning, ambiguity, uncertainty, corrections, EN/UR/Roman Urdu, voice turn-taking, interruption/recovery | `brain/evaluation.py` (31 scenarios after MIA-EVAL-06; `COVERAGE_MANIFEST` + `coverage_report()`), `tests/fixtures/conversation_eval.json` | `tests/test_brain_evaluation.py` (incl. coverage, non-vacuity tests), `tests/test_conversation_eval.py` | **Closed for what a deterministic suite can cover (MIA-EVAL-06).** Identity (real manifest renderer), capability inventory and provider-switch (EN + Roman Urdu) scenarios now run in the suite; `coverage_report()` fails the test if a roadmap area has no scenario or named test file. Voice turn-taking and interruption/recovery remain **outside** the suite by design (browser tests: `test_voice_browser.py`, `test_assistant_stop_reply.py`, `test_assistant_error_recovery.py`); they were not run by A3. Scenarios prove the identity text, history and memory reach the model and the serving provider changed; they say nothing about live-model answer quality. |
| 2 | Accurate, evidence-grounded self-description; no unsupported feature claims | `brain/self_description.py`, `memory/identity.py`, `brain/capabilities.py` | `test_self_description.py` (11), `test_identity.py` (19), `test_runtime_evidence_budget.py`, wake-word case in `test_conversation_eval.py` | Automated coverage exists. MIA-BRAIN-07 (prompt-block bound) is IN_PROGRESS under a prior A1 session. Owner check: ask a real model about itself (live). |
| 3 | Cross-conversation retrieval with provenance; owner corrections and retention work | `memory/store.py`, `structured.py`, `context.py`, `semantic.py`, `relationships.py`, `maintenance.py` | `test_memory_context.py` (26), `test_memory_lifecycle.py` (30), `test_memory_retention.py` (5), `test_structured_retention.py` (11), `test_project_continuity.py` (12), `test_chat_memory_context.py` | Retention/corrections covered. Inspect/export/delete audit is **MIA-M1-10** (claimed by A6). Record-level project scoping is **MIA-BRAIN-06** (A1, IN_PROGRESS). No duplicate task created. |
| 4 | Text and voice share the same Brain, identity, context and verification path | `brain/orchestrator.py` (one pipeline; `interface` is diagnostics-only), `assistant.html` `submitPrompt` | `test_brain_pipeline.py` (16) sends `interface="voice"` by hand | **VERIFIED GAP (feeds MIA-M1-09, no new task):** `assistant.html` never sends `interface`; spoken turns call `submitPrompt(prompt,{voiceReply:voiceMode})` and post `/api/gateway/chat[/stream]` without it, so the server records them as `text` and the voice-turn judge skip (`orchestrator.py` ~462, "never add judge latency to a spoken conversation") never applies to real UI voice turns. Tests pass because they set the field themselves. Shared Brain path itself is confirmed. |
| 5 | Voice, wake-word controls and failure states have automated coverage; physical checks labelled OWNER_VERIFICATION_REQUIRED | `voice/stt.py`, `tts.py`, `wake.py`, `wake_transport.py`, `assistant.html` | `test_voice_wake.py` (34), `test_voice_wake_transport.py` (23), `test_voice_wake_ui.py` (10), `test_voice_browser.py` (37), `test_voice_api.py`, `test_voice_stt.py` (2), `test_voice_tts.py` (5) | Labelled in `docs/voice/OWNER_VERIFICATION.md`. STT/TTS adapters have thin tests (2 and 5) → **MIA-M1-11** (claimed by A4). Real speech, Windows, hardware: OWNER_VERIFICATION_REQUIRED. |
| 6 | Parallel judges and escalation obey privacy, resource and budget policy; testable with fakes | `brain/judges.py`, `gateway/escalation.py`, `gateway/judge_call.py` | `test_brain_judges.py` (15), `test_brain_judge_pipeline.py`, `test_gateway_escalation.py` (16), `test_gateway_judge_call.py`, 3 judge scenarios in the eval suite | Covered by fakes; off by default. No live-model quality evidence (owner check). Voice turns skipped from judging only when `interface="voice"` (see row 4). |
| 7 | Owner can sustain a multi-turn discussion, switch topics, refer back, ask about itself | whole pipeline | follow-up and continuity scenarios (row 1) | **Owner-only live check.** No automated test can establish this. |

## Child tasks created (only demonstrable gaps)

- **MIA-EVAL-06** (new, A3, P1; implemented, see board): add identity/capability-inventory and provider-switch continuity scenarios to `mia.brain.evaluation`, plus a coverage manifest test that fails when a roadmap category has no scenario. Row 1.

## Observations that are not new tasks

- Row 4 gap is handed to MIA-M1-09 (A1 + A4) with the exact evidence above.
- Stale claims from earlier sessions, left untouched: MIA-EVAL-05 (A3, since 2026-11-21T16:04Z), MIA-BRAIN-07 (A1, 16:05Z), MIA-EVOL-02 (A5, 17:00Z). Their registry rows do not hold them; their integration-log checkpoints should be inspected before resuming or reassigning.
- `docs/STATUS.md` still says recent tests were "not run in this environment"; the tallies above and later agent entries supersede that for the non-Chromium suite.
