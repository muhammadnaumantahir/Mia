# Mia multi-agent integration log

Append-only coordination history. Use UTC timestamps. Do not rewrite or delete earlier records.

## 2026-11-21 — System initialized

- Added the six specialist roles A1–A6 and a separate coordinator prompt.
- Created the shared task board and agent registry. All six worker slots begin unregistered; no external AI agent is claimed to be connected or running.
- Main-only integration is mandatory. Parallel work is allowed on disjoint scopes; commits and shared-file updates must be serialized and based on the latest remote state.
- Initial tasks target the current highest-priority development work: Code Graph baseline, conversation continuity, gateway escalation, reasoning evaluation, offline wake-word prerequisites and conversation UX.
- No feature implementation or test result is implied by this coordination-system setup.

## 2026-11-21T07:26Z — MIA-GW-01 claimed

- Agent: A2 (Claude, claude.ai session). Status: IN_PROGRESS.
- Paths: docs/AI_AGENT_REGISTRY.md, docs/AI_TASK_BOARD.md. No implementation yet.

## 2026-11-21T07:27Z — MIA-BRAIN-01 claimed

- Agent: A1 (Claude, claude.ai session). Status: IN_PROGRESS.
- Paths: docs/AI_AGENT_REGISTRY.md, docs/AI_TASK_BOARD.md. No implementation yet. Planned scope: src/mia/brain/** and src/mia/memory/** plus matching tests; will not touch src/mia/gateway/** (A2, MIA-GW-01).

## 2026-11-21T07:40Z — MIA-BASE-01 claimed

- Agent: A5 (Claude, claude.ai session). Status: IN_PROGRESS.
- Paths: docs/AI_AGENT_REGISTRY.md, docs/AI_TASK_BOARD.md, docs/AI_INTEGRATION_LOG.md. Initial finding: watcher and scripts/update_code_graph.py always run a full build_graph on any change; implementation follows.

## 2026-11-21T07:28Z — MIA-EVAL-01 claimed

- Agent: A3 (Claude, claude.ai session). Status: IN_PROGRESS.
- Paths: docs/AI_AGENT_REGISTRY.md, docs/AI_TASK_BOARD.md. Planned scope: evaluation scenarios/runner and verifier-adjacent tests (tests/** eval files); will not touch src/mia/brain/**, src/mia/memory/** (A1) or src/mia/gateway/** (A2) without coordinator agreement.

## 2026-11-21T07:29Z — MIA-VOICE-01 claimed

- Agent: A4 (Claude, claude.ai session). Status: IN_PROGRESS.
- Paths: docs/AI_AGENT_REGISTRY.md, docs/AI_TASK_BOARD.md, docs/AI_INTEGRATION_LOG.md. No implementation yet. Planned scope: src/mia/voice/** and matching tests/test_voice_*.py plus docs/voice/**; will not touch src/mia/brain/**, src/mia/gateway/** or src/mia/assistant.html without recording an interface proposal here first.

## 2026-11-21T08:05Z — MIA-BRAIN-01 completed

- Agent: A1 (Claude, claude.ai session). Status: DONE. Remote main verified via `git ls-remote`: commits `5887f86` and `e9b15b7`.
- **Main was unimportable.** `src/mia/brain/verify.py` had a literal `\n` (SyntaxError) and `src/mia/observatory/api.py` had a mis-indented capture block (IndentationError), both from earlier web-editor commits. Fixed in `5887f86`. All other agents were affected; this is the first commit that lets the package import.
- `verify.py`: the low-confidence "short reply" rule counted characters (<25), so a five-word Roman Urdu hedge was missed while a shorter Urdu one was caught. Now counts words (<=6). Existing test `test_urdu_and_roman_urdu_uncertainty_phrases_are_detected` had been failing for this reason.
- `tests/test_structured_memory.py`: missing `ContextBudget` import (NameError).
- `e9b15b7`: `has_persistent_prior_conversation` scanned 5 conversations (one is always the current chat) while recall scanned 50. With >=4 recent temporary chats Mia asked the owner to re-explain context it could have recalled. Both now share `_prior_persistent_conversation_ids` / `_PRIOR_CONVERSATION_SCAN`. Regression test: `test_prior_context_check_finds_persistent_history_behind_many_temporary_chats` (written first and confirmed failing).
- Touched: src/mia/brain/verify.py, src/mia/brain/context.py, src/mia/observatory/api.py, tests/test_structured_memory.py, tests/test_brain_context.py.
- Tests actually run: `pytest` full suite = 1277 passed, 16 skipped (87s); `ruff check src tests` clean. Baseline before fixes: collection error in Brain package. CI not used.
- Not done / limits: no live Ollama or provider run; Code Graph snapshot not regenerated (A5 owns MIA-BASE-01); project-scoped continuity not addressed, filed as MIA-BRAIN-02.
- Agent-process note: A2-A5 were registered before `5887f86` landed, so any tests they ran earlier on Brain/Observatory code would have failed to import; they should pull.

## 2026-11-21T08:10Z — MIA-BRAIN-02 claimed

- Agent: A1. Status: IN_PROGRESS. Slice: provider-switch and memory-correction recall regression coverage in tests/test_brain_*; src/mia/brain/** and src/mia/memory/** only. Project-scoped conversation continuity is a design change and is held for owner decision.

## 2026-11-21T07:41Z — MIA-UX-01 claimed

- Agent: A6 (Claude, claude.ai session). Status: IN_PROGRESS.
- Paths claimed: src/mia/assistant.html and its tests (tests/test_*assistant*, tests/test_*ui*); docs/ux/**. Will not touch src/mia/brain/**, src/mia/memory/** (A1), src/mia/gateway/** (A2) or src/mia/voice/** (A4); any voice-state wiring will go through the existing UI/API contract only.
- Note: A6 first started on A3's task by mistake before re-reading the board; those local edits duplicated A1's fixes and were discarded without being pushed.

## 2026-11-21T08:25Z — MIA-BRAIN-02 completed (provider-switch slice)

- Agent: A1. Status: DONE. Remote main verified via `git ls-remote`: `f61cdd3`.
- Added `tests/test_brain_continuity.py` (3 tests): memory recalled in a new session after the serving provider changes; private memory withheld from a remote-only provider after a switch; owner correction (supersede) wins after a switch. The privacy test has a positive control and was mutation-checked: disabling the `_remote_context_allowed` guard in `src/mia/brain/context.py` made it fail; the source was restored (no src diff).
- No source change was needed: the audited behaviour already held. These are new regression guards, not a defect fix.
- Tests actually run: full suite 1280 passed, 16 skipped; `ruff check src tests` clean. Deterministic scripted providers only; no live model quality claimed. CI not used. Code Graph not regenerated.
- Split MIA-BRAIN-03 (project-scoped continuity) as DEFERRED pending an owner decision.


## 2026-11-21T07:44Z — MIA-VOICE-01 completed (A4)

- Agent: A4. Status: DONE. Remote `main` verified with `git ls-remote`: `ee0daa0e907e69e157bb5442a7dd193d86fe52eb`.
- Touched paths: `src/mia/voice/wake.py` (new), `tests/test_voice_wake.py` (new), `src/mia/observatory/api.py` (+1 import, +`offline_wake_word` key in `/api/voice/status`), `tests/test_voice_api.py` (+2 tests), `docs/voice/WAKE_WORD_PLAN.md` (new), `docs/voice/OWNER_VERIFICATION.md`, `docs/voice/VOICE_COMPLETION_CHECKLIST.md`, `docs/architecture/VOICE.md`, `docs/research/INTEGRATION_MATRIX.md`.
- Tests actually run: `tests/test_voice_wake.py` 51 passed; targeted voice/self-description/continuity files 79 passed after rebase; full suite excluding `tests/test_voice_browser.py` and `tests/test_browser_automation.py` (Chromium not available here): 1330 passed, 2 skipped, 0 failed; `ruff check` clean on touched files. A 9-mutant check on `wake.py` (opt-in, stop, close, unlimited listening, storm guard, buffer clear, NaN handling, patience, `audio_retained`) was caught 9/9; mutants were not committed.
- Real-engine evidence (scratch venv, not committed): openWakeWord 0.6.0 + onnxruntime 1.32 on Python 3.13, 2-core Linux sandbox, upstream `hey_jarvis` model: ~3-5 ms/80 ms frame, 0 activations on 20 s of synthetic noise through Mia's session. Not a false-accept measurement; no real-speech positive was possible here (no TTS/microphone).
- Findings: upstream pre-trained models are CC BY-NC-SA 4.0 (non-commercial); wake words are English-only; `pip install openwakeword` fails on Linux + Python 3.13 (`tflite-runtime` pin). `brain/self_description.py` infers `wake_word` from any route containing "wake" - filed as MIA-BRAIN-04 before any wake route is wired.
- Not delivered / OWNER_VERIFICATION_REQUIRED: audio transport, UI, real-speech detection, false-accept/miss rates, Windows install (`OWNER_VERIFICATION.md` section G).
- Code Graph: `python scripts/update_code_graph.py` run locally (3,936 nodes, 7,924 edges, 0 parse errors; includes `voice/wake.py`). The tracked `docs/code-graph/CHANGELOG.md` entry was reverted, not committed.
- Filed: MIA-VOICE-02 (claimed by A4), MIA-BRAIN-04 (A1), MIA-UX-02 (A6, blocked), MIA-VOICE-03 (A4, needs `assistant.html`; A6 owns it).

## 2026-11-21T07:44Z — Broken-main note and duplicate fix dropped (A4)

- At 07:30Z `main` failed to import: `src/mia/observatory/api.py` (mis-indented block from 24da0ab) and `src/mia/brain/verify.py` (literal `\n` from c4b12f4) were SyntaxErrors, so `create_app` and most tests could not load. A4 prepared an identical syntax-only fix locally; A1 pushed the same fix first (`5887f86`). A4 discarded its unpushed duplicate commit and rebased onto `5887f86` without rewriting remote history.
- Residual failures seen before A1's fix (verifier Urdu uncertainty phrases, a missing `ContextBudget` import in a test) were left to A1/A3 and are no longer failing in the full run above.

## 2026-11-21T07:44Z — MIA-VOICE-02 claimed (A4)

- Agent: A4. Status: IN_PROGRESS. Paths: `src/mia/voice/wake_transport.py`, `tests/test_voice_wake_transport.py`, `docs/voice/**`. Will not edit `src/mia/assistant.html` (A6, MIA-UX-01) or `src/mia/brain/**` (A1). The one-line wiring in `src/mia/observatory/api.py` waits for MIA-BRAIN-04.
- Interface proposal for A6/A1: transport spec is `docs/voice/WAKE_WORD_PLAN.md` section 6; `offline_wake_word_status(transport_available=True)["operational"]` is the only evidence Brain should use for self-description.

## 2026-11-21T07:50Z — MIA-UX-01 completed; MIA-UX-03 claimed
- Agent: A6. MIA-UX-01 status: DONE. Remote main verified (`git ls-remote` = `5ba0e2f2f9031e132452c49bc3c3f364830b872b`; `merge-base --is-ancestor` confirmed).
- Implemented: Stop ■ button + Esc for in-flight text replies (AbortController on both the streaming and tool-enabled paths); partial streamed text is kept, labelled as partial and never spoken; aborting the buffered tool path warns that the server may already have processed the request; `#hint` is an aria-live status region.
- Touched: src/mia/assistant.html, tests/test_assistant_stop_reply.py, docs/STATUS.md, docs/code-graph/CHANGELOG.md (generated entry, 3846 nodes / 7778 edges, 0 parse errors, via `python scripts/update_code_graph.py`; impact analysis not separately run — change is HTML/JS only).
- Tests actually run: new file 5 tests (all fail against the pre-change page, pass after); UI contract tests (voice/identity/life/memory/assistant-inbox) pass; after rebasing onto A4/A1 commits re-ran tests/test_assistant_stop_reply.py + tests/test_voice_ui_contract.py = pass. Full suite was run on my change *before* rebasing: 1340 tests, 1338 passed, 1 skipped, 1 failed (MIA-FIX-01, pre-existing, unrelated). The full suite was NOT re-run after the rebase. `ruff check src tests` clean. No CI used.
- Limits: Node harness exercises JS logic only (no real browser/screen reader: OWNER_VERIFICATION_REQUIRED). Server-side behaviour on client disconnect (partial persistence, usage-ledger settlement) not traced — worth a gateway-side check by A2.
- Process notes: A6 initially started on A3's task by mistake (before re-reading the board) and discarded those local edits unpushed (they duplicated A1's compile fixes). Sandbox clock differs from other agents' timestamps by ~30 min; times above are this session's `date -u`.
- Filed MIA-FIX-01 (browser test vs owner-grant policy) and MIA-UX-03 (claimed by A6). A4's MIA-UX-02 (wake-word UI) remains blocked on MIA-VOICE-02 + MIA-BRAIN-04.


## 2026-11-21T07:51Z — MIA-GW-01 DONE (A2)

- Finding: verifier confidence was only traced/returned; nothing used it to route or retry, so quality escalation was not wired (gateway fallback is failure-based only).
- Implemented: src/mia/gateway/escalation.py (pure, opt-in, fail-closed policy), Brain._maybe_escalate hook (non-streaming only), tests/test_gateway_escalation.py, docs/architecture/AI_GATEWAY.md, docs/STATUS.md.
- Commits on remote main: de4f185 (feature), 84d43a7 (fix). Remote head verified with git ls-remote.
- Tests actually run: tests/test_gateway_escalation.py passes (fake providers). Full suite: 1405 passed, 1 skipped, 1 failed (tests/test_browser_automation.py::test_browser_api_end_to_end, already failing before this change; not touched).
- Code Graph refreshed with scripts/update_code_graph.py (3979 nodes / 8050 edges, 0 parse errors).
- Incident (mine): de4f185 accidentally included git conflict markers in docs/code-graph/CHANGELOG.md (docs only, no code impact); fixed in 84d43a7 keeping both graph entries.
- Observed, not mine: main briefly had syntax errors in brain/verify.py and observatory/api.py (A1 fixed in 5887f86); gateway hard-fails empty provider replies, so the empty_response trigger is unreachable on the non-streaming path.
- Limitations: no live-provider quality evidence; streaming turns do not escalate; no settings UI. Follow-up filed as MIA-GW-02.

## 2026-11-21T08:40Z — MIA-BRAIN-03 claimed

- Agent: A1. Status: IN_PROGRESS. Owner approved project-scoped continuity (conversation belongs to a project; recall prefers that project's history). Constraints: opt-in, owner-visible, temporary chats stay isolated. Paths: src/mia/memory/** (schema/store), src/mia/brain/context.py, src/mia/observatory/api.py (project routes only) and tests. Will not touch gateway (A2), voice (A4) or assistant.html (A6) without an interface note here.

## 2026-11-21T07:54Z — MIA-EVAL-01 DONE, MIA-EVAL-02 claimed (A3)

- Commit 186f5d0 on remote main (verified). Paths: src/mia/brain/evaluation.py, src/mia/brain/verify.py, tests/test_brain_evaluation.py, tests/test_brain_verify.py, tests/test_source_compiles.py.
- Tests run: full suite 1618 passed, 1 failed, 1 skipped (failure = MIA-FIX-01, browser grant, out of scope); after rebase onto A2 commits only targeted suites were re-run (eval, verify, escalation, pipeline, routing, compile guard) and passed; `python -m mia.brain.evaluation` 23/23.
- Verifier defect fixed: honest "failed/failure/cannot" (and Roman Urdu/Urdu) wording was flagged failure_not_reported. A tool-failure scenario initially false-passed because the tool was refused at stage `offered`; scenarios now assert real tool status.
- Limits: scripted providers only; no live-model quality claim. Code Graph regenerated locally (4003 nodes, 8088 edges); tracked CHANGELOG change reverted, not committed.
- MIA-EVAL-02 claimed (A3): new files only (src/mia/brain/judges.py, tests/test_brain_judges.py); will not edit gateway/** (A2) or orchestrator without agreement.

## 2026-11-21T08:30Z — MIA-BASE-01 DONE; MIA-BASE-02 claimed
- Agent: A5. Commit bb01d32 on remote main (verified via git ls-remote).
- Paths: src/mia/codegraph/indexer.py, watcher.py, scripts/update_code_graph.py, tests/test_code_graph.py, tests/test_code_graph_watcher.py, docs/code-graph/README.md, docs/STATUS.md.
- Result: source-backed gap report (full rebuild on any change; no per-file cache); behaviour-preserving indexer speed-up (old vs new graphs identical on same tree); diff_snapshots/write_graph; watcher history records changed paths and rebuild time; update script builds once.
- Tests actually run: pytest tests/test_code_graph.py tests/test_code_graph_watcher.py tests/test_code_graph_api.py -> 23 passed. Full suite (before final rebase): only test_browser_automation::test_browser_api_end_to_end failed, also failing without A5 changes; filed as MIA-BASE-03.
- Limitations: true incremental indexing NOT implemented (MIA-BASE-02). Code Graph snapshot not regenerated/committed (graph JSON is git-ignored).
- Also: main-breaking syntax errors found were already fixed by A1 (5887f86); A5 duplicate dropped.


## 2026-11-21T07:56Z — MIA-EVAL-02 DONE (module only), MIA-EVAL-03 filed (A3)

- Commit 387420f on remote main (verified). New: src/mia/brain/judges.py, tests/test_brain_judges.py. No existing file edited.
- Tests run: tests/test_brain_judges.py (20), test_brain_evaluation, test_brain_verify, test_gateway_escalation, test_source_compiles all pass; ruff clean on src+tests. Full suite not re-run after this commit.
- Limits: fake providers only; judges are not called anywhere in the live pipeline yet; no claim about real judge-model quality. Wiring filed as MIA-EVAL-03 (needs A1 orchestrator + A2 gateway agreement).
