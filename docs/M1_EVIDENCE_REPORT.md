# M1 evidence report and owner-check handoff — MIA-M1-13

**Author:** A3 (Claude Sonnet 5.5 session) · **Evidence head:** `5a64019` (main) · **Verdict:** automated development work for M1 is complete; **M1 is not accepted**, because the criteria below that need a real model, a real microphone or Windows can only be confirmed by the owner. This report does not declare M1 complete.

## What was actually run on `5a64019` (Linux sandbox)

| Check | Result |
|---|---|
| `python -m mia.brain.evaluation` | 31/31 scripted scenarios passed (deterministic, fake providers) |
| `pytest tests` (entire suite, including the 8 Chromium/Playwright files: voice_browser 64, voice_wake_ui 10, browser_automation 13, assistant_error_recovery 5, assistant_mic_safety 3, assistant_stop_reply 5, assistant_project_control 4, gateway_quality_panel 3) | 2119 tests, 0 failed, 0 errors, 1 skipped (`test_computer_tasks`) |
| `ruff check src tests scripts` | clean |
| Live model, real speech, Windows desktop, real microphone/speaker | **not run** (no such environment here) |

The earlier traceability audit (`docs/M1_TRACEABILITY.md`, MIA-M1-08) was done at `431b220`; the table below supersedes its status column.

## Criterion by criterion

| # | Roadmap Priority 1 exit criterion | Automated completion | Owner-only check (OWNER_VERIFICATION_REQUIRED) |
|---|---|---|---|
| 1 | Repeatable conversation evaluation covers the required areas | **Done.** 31 scenarios incl. identity, capability inventory, provider-switch (EN + Roman Urdu); `coverage_report()` test fails if an area has none (MIA-EVAL-06). Voice turn-taking/interruption live in browser tests, which passed. | Run `python -m mia.brain.live_eval --models <your local models>` (MIA-EVAL-05) and read the pass counts. Five probes only; a smoke check, not a benchmark. |
| 2 | Accurate, evidence-grounded self-description | **Done.** Manifest renderer, identity and prompt-block bound (MIA-BRAIN-07), scenarios prove the manifest reaches the model. | Ask your real model about itself in a few turns; confirm it claims nothing the app cannot do. |
| 3 | Cross-conversation retrieval with provenance, corrections, retention | **Done.** Record-level project scoping (MIA-BRAIN-06); delete/retention leave no usage rows, are audited, optionally forget linked memory (MIA-M1-10). | Use it for a few days: inspect, correct, export and delete real memories; confirm they behave as you expect. |
| 4 | Text and voice share one Brain path | **Done.** UI now sends `interface` = voice/text (MIA-M1-09; confirmed in `assistant.html`), so the voice-turn judge skip applies to real spoken turns. | Hold one spoken and one typed conversation and confirm they feel like the same assistant. |
| 5 | Voice, wake-word controls and failure states have automated coverage | **Done** for server and browser behaviour: STT language validation and latency report (MIA-M1-11), no duplicate interrupted reply (MIA-M1-12), sentence-by-sentence speech while a reply streams (MIA-VOICE-05). | Listed in `docs/voice/OWNER_VERIFICATION.md`: real microphone, real speakers, wake word in a noisy room, Windows, urdu/roman-urdu speech quality. |
| 6 | Judges and escalation obey privacy, resource and budget policy | **Done** with fakes; judges are off by default; gateway fallback, per-turn deadline and empty-output fallback added (MIA-GW-04..07). | If you enable `MIA_JUDGES_*`, check latency and cost on your own models. |
| 7 | Owner can sustain a multi-turn discussion, switch topics, refer back | Supported by follow-up/continuity scenarios; no test can establish it. | **Owner-only.** Have a 15–20 turn conversation including a topic switch and a reference back. |

## Known limitations that stay open

- Default tools-on turns are still buffered server-side, so sentence-by-sentence speech applies to the tools-off streaming path only (MIA-VOICE-05 note).
- Gateway health degrade after a model-not-found response is process-local (MIA-GW-05 follow-up).
- Every automated model result uses scripted providers; none says anything about answer quality of a real model.
- Keyword checks in the live harness can mis-grade unusual phrasings.
- Row 4 of the earlier audit is fixed, but only a real spoken session shows how it feels.

## Handoff

1. Review this report and the commit list since `431b220`.
2. Do the owner checks above (items 1, 2, 3, 4, 5 and 7 need you; 6 only if you enable judges).
3. If you accept M1, tell the coordinator; M2+ tasks already in progress ran under owner-approved gate exceptions and are unaffected by this report.
