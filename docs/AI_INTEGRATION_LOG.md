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

## 2026-11-21T07:57Z — MIA-GW-02 DONE (A2)

- Implemented read-only GET /api/gateway/escalation + escalation block in /api/gateway/overview; process-local EscalationStats; streaming escalation documented as unsupported by design.
- Paths: src/mia/gateway/escalation.py, src/mia/brain/orchestrator.py, src/mia/observatory/api.py, tests/test_gateway_escalation.py, docs/architecture/AI_GATEWAY.md, docs/STATUS.md, docs/code-graph/CHANGELOG.md.
- Commit on remote main: 24e591e (verified with git ls-remote). Rebase conflict in docs/STATUS.md resolved by keeping both agents' sections; no markers remain.
- Tests run: full suite 1644 passed, 1 skipped, 1 failed (tests/test_browser_automation.py::test_browser_api_end_to_end, pre-existing). Code Graph refreshed (4012 nodes / 8102 edges).
- Limitations: counters not persisted; no dashboard UI; fake providers only.

## 2026-11-21T09:15Z — MIA-BRAIN-03 completed

- Agent: A1. Status: DONE. Remote main verified via `git ls-remote`: `ba7f6cf` (rebased over A2's MIA-GW-02 commits).
- Owner approved the design: opt-in project assignment; recall prefers the project's history. Implemented: memory schema migration 4 (`conversations.project`, NULL default); `MemoryStore.set_conversation_project` (normalised, <=64 chars, control characters rejected, audited as `conversation_project_set`); `POST /api/conversations/{id}/project` (null clears); project shown in conversation listings.
- Recall rule (`src/mia/brain/context.py`): conversation in a project -> same project first, then unassigned, never another project; temporary chats always excluded; a conversation with no project keeps the previous behaviour. Prior-session check and tail share one generator.
- Decisions to flag: (1) fallback to unassigned chats is a stricter reading of "prefers"; another project's chats are never recalled into a project. (2) A conversation with no project can still recall any project's latest chat (unchanged); revisit if the owner wants strict separation.
- Touched: src/mia/memory/schema.py, src/mia/memory/store.py, src/mia/brain/context.py, src/mia/observatory/api.py (new route + model only), tests/test_project_continuity.py, docs/code-graph/CHANGELOG.md.
- Tests actually run: full suite on the rebased tree = 1633 passed, 16 skipped; `ruff check src tests` clean. Two isolation tests were mutation-checked (a mutation letting other projects' chats through made both fail; source restored). One test of mine had a creation-order flaw that made it vacuous; found by the mutation check and fixed.
- Code Graph: `python scripts/update_code_graph.py` run locally (4026 nodes / 8152 edges); its CHANGELOG entry is committed. It recorded the parent commit `946d180` because the tree was uncommitted at the time. I resolved a CHANGELOG merge conflict with A2's entry by keeping both. No impact analysis via the explorer was run.
- Limits: no UI control yet (filed MIA-UX-02 for A6); structured memory records are not project-scoped (MIA-BRAIN-04); no live model run; CI not used.

## 2026-11-21T09:30Z — MIA-BRAIN-04 claimed

- Agent: A1. Status: IN_PROGRESS. Slice (b) structured-memory retention policy first. Paths: src/mia/memory/**, src/mia/observatory/api.py (memory retention routes only), tests. No gateway/voice/assistant.html edits.

## 2026-11-21T09:10Z — MIA-BASE-02 DONE
- Agent: A5. Commit 6c58fb6 on remote main (verified via git ls-remote).
- Paths: src/mia/codegraph/indexer.py, watcher.py, scripts/update_code_graph.py, tests/test_code_graph.py, tests/test_code_graph_watcher.py, docs/code-graph/README.md, docs/code-graph/CHANGELOG.md, docs/STATUS.md.
- Result: build_graph split into cached per-file extraction (FactCache, salted by indexer source hash) plus a global resolution pass. Output identical to the previous indexer on this repo (cold and warm). Sandbox timings: ~2.5s old, ~1.7s cold, ~0.5s warm.
- Tests actually run: pytest tests/test_code_graph.py tests/test_code_graph_watcher.py tests/test_code_graph_api.py -> 25 passed (before and after final rebase). Full suite not re-run for this commit.
- Limitations: Python only; unreadable-file edge case yields one index error instead of two; Linux sandbox only (Windows OWNER_VERIFICATION_REQUIRED). Ran scripts/update_code_graph.py (graph.json is git-ignored; CHANGELOG entry committed).

## 2026-11-21T09:40Z — MIA-BASE-03 / MIA-FIX-01 DONE
- Agent: A5. Commit ffef797 on remote main (verified via git ls-remote). Path: tests/test_browser_automation.py only.
- Root cause: the fail-closed owner-grant policy for browser.fill/scroll/screenshot is intentional (tests/test_browser_api.py asserts it); the end-to-end test predated it. The test now asserts the 409 first, grants via POST /api/computer/permissions, then continues. No route or policy change; no assertion weakened.
- Tests actually run: tests/test_browser_automation.py + tests/test_browser_api.py -> 15 passed; after final rebase tests/test_browser_automation.py -> 13 passed. Full suite not re-run after this change.

## 2026-11-21T10:05Z — MIA-BASE-04 claimed

- Agent: A5 (same session; resumed on owner Continue). Status: IN_PROGRESS. Paths: src/mia/codegraph/**, scripts/update_code_graph.py, graph tests/docs. A5 had no other IN_PROGRESS task.


## 2026-11-21T15:29Z — MIA-EVAL-03a DONE (A2); MIA-GW-03 claimed

- Added src/mia/gateway/judge_call.py (make_judge_call: pinned candidate, text-only, normal route policy/budget) + tests/test_gateway_judge_call.py (6 fake-provider tests pass). Not wired into the pipeline: that is MIA-EVAL-03 (orchestrator.py, A1/A3) and I did not edit orchestrator.py.
- Commit on remote main: a1609a9 (verified with git ls-remote). Two push rejections/rebase conflicts (docs/AI_TASK_BOARD.md, docs/code-graph/CHANGELOG.md) resolved by keeping upstream's board and both graph entries; no markers remain.
- Limitation: no live-model judge quality evidence.

## 2026-11-21T15:29Z - MIA-UX-03 DONE; MIA-UX-02 (project control) in progress (A6)
- MIA-UX-03: remote main verified at `2915e57` (error-aware failure hints, Retry, aria-busy; tests/test_assistant_error_recovery.py).
- MIA-UX-02: implemented in src/mia/assistant.html with tests/test_assistant_project_control.py (4 tests, all fail against the previous page). Uses A1's existing `POST /api/conversations/{id}/project`; no backend changes. Numbering follows the upstream board (MIA-UX-04 is the wake-word UI). Commit SHA to be recorded after the push is verified.
- Tests actually run before the final rebase: full suite 1696 tests, 1694 passed, 1 skipped, 1 failed (the browser test since fixed by A5 in MIA-BASE-03); ruff clean. After rebasing onto A5's later commits: compile check plus tests/test_assistant_project_control.py, test_assistant_stop_reply.py, test_assistant_error_recovery.py, test_voice_ui_contract.py, test_code_graph.py all pass. Full suite not re-run on the final tree.
- Not verified: real browser / keyboard / screen reader / narrow-screen layout of the new sidebar row.
- Note: the registry task cell for A3 was corrupted earlier; upstream has since replaced it. Another agent marked all agents BLOCKED (provider usage limit); A6 set itself ACTIVE because this session is running.


## 2026-11-21T15:40Z — MIA-VOICE-02 completed (A4), wiring deferred

- Agent: A4. Paths: `src/mia/voice/wake_transport.py` (new), `src/mia/voice/wake.py` (+`expire_if_due`), `tests/test_voice_wake_transport.py` (new), `pyproject.toml` (`voice-wake` extra + `websockets`), `docs/voice/WAKE_WORD_PLAN.md`, board/registry/STATUS.
- Tests actually run: wake + transport 82 passed; full suite excluding `tests/test_voice_browser.py` and `tests/test_browser_automation.py` (no Chromium here): 1676 passed, 2 skipped, 0 failed; `ruff` clean on touched voice files. 10-mutant check of transport guards caught 10/10 (not committed).
- Not wired into `create_app` by design; filed MIA-VOICE-04 (blocked on A1's MIA-BRAIN-05). No Code Graph CHANGELOG entry committed (avoids cross-agent conflicts). Registry: A4 now on MIA-VOICE-03 (needs `assistant.html`; will coordinate with A6 and keep the edit minimal).

## 2026-11-21T15:34Z - MIA-UX-02 DONE (A6)
- Commit `4ae4a328f3c5b2ae28efb3f4ccc38f6708e5dad7` verified on remote main (`git ls-remote` equal and `merge-base --is-ancestor` true after the push).
- Full suite on that head: 1706 tests, 1705 passed, 1 skipped, 0 failed; `ruff check src tests` run (see result in this session). No CI used.
- Delivered: opt-in Project control and conversation-list project badge in src/mia/assistant.html; tests/test_assistant_project_control.py. Still unverified: real browser, keyboard, screen reader, narrow-screen layout.
- Next for A6: MIA-UX-04 (wake-word UI) remains blocked on MIA-VOICE-02 + MIA-BRAIN-05; MIA-VOICE-03 needs assistant.html (coordinate with A4).

## 2026-11-21T15:34Z - MIA-UX-05 claimed (A6)
- No unblocked A6 task existed (UX-04 blocked; VOICE-03 is A4's; M2-M6 gated), so A6 filed MIA-UX-05 from an audit of the one-shot recording path (microphone visibility/stop safety). Paths: src/mia/assistant.html, tests/test_assistant_mic_safety.py only.

## 2026-11-21T15:34Z — MIA-GW-03 DONE (A2)

- Audit finding: routing had no language awareness; Urdu/Roman Urdu turns classified 'simple' rewarded the smallest model. Added src/mia/gateway/language.py and a language-aware term in BrainRouter._score (src/mia/brain/router.py, routing code I treated as gateway/routing scope; no orchestrator edits). Ordering only; explicit owner preferences win; nothing excluded.
- Tests: tests/test_gateway_language_routing.py (16 cases). Full suite run locally: 1718 passed, 1 skipped, 0 failed. Code Graph refreshed (4137 nodes / 8412 edges).
- Commit on remote main: 21a8e6d (verified with git ls-remote). One test of mine initially exposed a real flaw (penalty beat an explicit preference); fixed before commit.
- Filed MIA-GW-04 (gateway-level timeouts). Limitations: size heuristic, not measured Urdu quality; fake candidates only.

## 2026-11-21T15:39Z - MIA-UX-05 DONE (A6)
- Commit `14d748b77c4bc4efdbf9f2c472728f91fbfa3d3f` verified on remote main (ls-remote equal; merge-base --is-ancestor true).
- Paths: src/mia/assistant.html, tests/test_assistant_mic_safety.py, docs/STATUS.md and coordination docs. No backend, voice-session or wake-word changes.
- Tests actually run: full suite 1780 tests, 0 failed, 2 skipped; ruff check src tests clean; the 3 new tests fail against the previous page. No CI used.
- Unverified (OWNER_VERIFICATION_REQUIRED): real browsers/microphones and screen readers.
- A6 status: IDLE. Remaining A6 work is blocked: MIA-UX-04 (wake-word UI) needs MIA-VOICE-02 + MIA-BRAIN-05; MIA-VOICE-03 is A4's and edits assistant.html, so A4 should coordinate with A6 (A6 is not editing the file now). M2-M6 (e.g. MIA-HOLO-*) stay gated on the M1 exit criteria.


## 2026-11-21T15:40Z — MIA-EVAL-03 DONE (A3)

- Commits 9c29972 (code) and be18ccf (Code Graph) on remote main (verified). Files: src/mia/brain/judges.py (env policy, stats, snapshot), src/mia/brain/orchestrator.py (additive ctor args + `_maybe_judge` hook after `_finish_text`, mirrors A2's escalation hook), src/mia/observatory/api.py (read-only GET /api/brain/judges), tests/test_brain_judge_pipeline.py. Uses A2's gateway/judge_call.py unchanged.
- Coordination: A1 (BRAIN-04) and A2 were paused/idle; edits to orchestrator.py/api.py were additive only and rebased cleanly (only conflict: generated code-graph CHANGELOG, resolved by regenerating).
- Tests run on merged tree: full suite 1783 passed, 2 skipped, 0 failed; ruff clean. One further upstream docs commit (A6) arrived during the final rebase and was not re-tested.
- Limits: fake providers only; streamed turns not judged; no live judge-quality evidence (OWNER_VERIFICATION_REQUIRED). Env: MIA_JUDGES_ENABLED/MAX/CONCURRENCY/TIMEOUT_S/TOTAL_TIMEOUT_S/ALLOW_REMOTE/ALLOW_PAID/MAX_COST_USD.
- Follow-ups filed: MIA-EVAL-04 (A3), MIA-UX-06 (A6).

## 2026-11-21T15:45Z — MIA-BASE-04 DONE
- Agent: A5. Commit ef935a2 on remote main (verified via git ls-remote).
- Paths: src/mia/codegraph/impact.py, indexer.py, src/mia/observatory/api.py (impact route query param only), scripts/update_code_graph.py, tests/test_code_graph_impact_git.py (new), docs/code-graph/README.md, docs/code-graph/CHANGELOG.md.
- Gaps fixed: impact ignored uncommitted/staged/untracked changes (now included by default, `uncommitted=false` for committed-only, `includes_uncommitted` reported); renames lost the old path; non-ASCII/space paths could be quoted by git and unmatched (now NUL-separated); graph `commit` hid a dirty tree (now `tree: {dirty, dirty_paths}`, CHANGELOG states clean/dirty). Git-diff path had no tests before.
- Indexed commit for the refreshed graph: ef935a2, tree clean (recorded in docs/code-graph/CHANGELOG.md; graph.json is git-ignored).
- Tests actually run: graph suites 31 passed after final rebase; mutation check (disabling the fixes) made 5 of 6 new tests fail; ruff clean on touched files; full suite `pytest tests` on the tree at ef935a2 = 1724 passed, 1 skipped.
- Limitations: old paths of renamed/deleted files have no node, so their remaining importers are not listed; status endpoint does not report staleness; no auto-refresh on commit; Linux only (Windows OWNER_VERIFICATION_REQUIRED). Filed MIA-BASE-05 and claimed it.

## 2026-11-21T15:45Z — MIA-BASE-05 claimed
- Agent: A5. Status: IN_PROGRESS. Paths: src/mia/codegraph/**, status route only in observatory/api.py, graph tests/docs.

## 2026-11-21T15:50Z — MIA-BRAIN-04 (retention slice) and MIA-BRAIN-05 completed

- Agent: A1. Both DONE. Remote main verified via `git ls-remote`: `ee6fe3f` (retention) and `0d22d02` (self-description). Resumed after a usage-limit pause; both pieces were built and tested in the resumed session.
- **MIA-BRAIN-04 (retention):** `structured_retention_days` setting (default None = keep; 1..36500); `StructuredMemory.retired_older_than`; `MemoryStore.purge_retired_records` (dry run by default; refuses with no period; only superseded/outdated/archived/rejected/expired; live and pending never touched; audited by id only; successors keep no dangling pointer); `POST /api/memory/records/retention`. Nothing is deleted automatically. 11 tests; a mutation that let active/pending records into the purge set made 6 fail.
- **MIA-BRAIN-05 (wake word):** removed the substring match on "wake" in route paths/tool names. The manifest takes `offline_wake_word_status()` and says "operational" only when that report and the engine/model/transport flags are each literally `True`; otherwise "not operational (<reason>)" from a fixed vocabulary, or "not registered" with no evidence. Four mutations (truthy-vs-True, dropped component check, evidence not passed by the API, unvetted reason echoed) are each caught.
- **Defect found and fixed:** `current_identity()` in `src/mia/observatory/api.py` called `ProviderRegistry.keys()`, which does not exist. The AttributeError was swallowed by a broad `except`, so the LIVE RUNTIME EVIDENCE block was never attached to any chat (the self-description feature existed but never reached a model). Now uses `ids()`; a failure is recorded once per process as observatory event `runtime_evidence_failure`. Reverting the fix makes the new regression test fail. I could not date the introduction because the local clone is shallow.
- **Side effect to know about:** with the block now present, the system prompt grew from about 1.1 KB to about 4.1 KB (no tools registered; more with the real tool list). Filed MIA-BRAIN-07 to measure and bound it. Five existing tests (test_identity x2, test_chat_memory_context x3) had asserted the whole system message equalled the bare persona, i.e. they encoded the missing block; they now compare the model-independent persona separately from the evidence block and additionally assert that the evidence reports each run's own provider.
- **For A4 (MIA-VOICE-04) and A6 (MIA-UX-04):** MIA-BRAIN-05 is done, so the wake-route wiring can proceed. At the manifest call site pass the operational transport flag to `offline_wake_word_status(transport_available=...)`; registration of a route alone will correctly not make Mia claim a wake word.
- Touched: src/mia/brain/self_description.py, src/mia/observatory/api.py, src/mia/memory/{settings,store,structured}.py, tests/test_self_description.py, tests/test_conversation_eval.py, tests/test_identity.py, tests/test_chat_memory_context.py, tests/test_structured_retention.py, docs/code-graph/CHANGELOG.md.
- Tests actually run on the rebased tree: `pytest` full suite = 1740 passed, 17 skipped; `ruff check src tests` clean. Code Graph refreshed locally on the clean tree at `0d22d02` (4294 nodes / 8770 edges, 0 parse errors); the explorer's impact analysis was not run. Scripted providers only; no live model run; CI not used.
- Not done: record-level project scoping (MIA-BRAIN-06); bounding the evidence block (MIA-BRAIN-07).


## 2026-11-21T16:10Z — MIA-VOICE-03 completed (A4)

- Agent: A4. Paths: `src/mia/assistant.html` (only the `END_PHRASES` / `STOP_SPEAKING_PHRASES` constants and a comment; no other UI code touched, A6 notified here), `tests/test_voice_browser.py` (+26 tests, `import json` moved to the top), STATUS, checklist, board.
- Tests actually run: full suite with Chromium 1781 passed, 1 skipped, 0 failed; `ruff check src tests` clean; 15 new tests fail against the old HTML.
- Correction to the 07:44Z / 15:40Z entries: they said the two Chromium test files were not run because Chromium was unavailable. That was an unverified assumption; they run with the system Python (which has Playwright) and passed.
- Next for A4: MIA-VOICE-04 is blocked on MIA-BRAIN-05 (A1). Nothing else open in A4's lane.

## 2026-11-21T15:46Z — MIA-EVAL-04 DONE (A3)

- Commit bd50db0 on remote main (verified). Files: src/mia/brain/orchestrator.py (`_maybe_judge` also on the stream path after `_store_reply`; `stored` flag in stream_turn; voice skip), src/mia/brain/evaluation.py (judge scenarios, per-provider candidate id fix), tests/test_brain_judge_pipeline.py, tests/test_brain_evaluation.py.
- Tests run: full suite on rebased tree 1791 passed, 2 skipped, 0 failed; after the final rebase onto A5's docs commit only the judge/eval/pipeline/gateway-judge/compile suites were re-run (pass). Mutation check: removing the `stored` guard fails the disconnect test.
- Code Graph: regenerated locally (4284 nodes) but not committed this time - its generated CHANGELOG conflicts on every concurrent push; MIA-BASE owner (A5) can refresh it.
- Limits: scripted providers only; no live judge-quality evidence.

## 2026-11-21T16:35Z — MIA-VOICE-04 completed (A4)

- Agent: A4. Paths: `src/mia/observatory/api.py` (import, one `register_wake_routes(...)` call, `transport_available=websocket_runtime_available()` at the two status call sites incl. A1's manifest call), `src/mia/voice/wake_transport.py` (+`websocket_runtime_available`), `tests/test_voice_wake_wiring.py` (new, 8 tests), docs.
- Interface note for A1/A6: `offline_wake_word.operational` is true only with engine + owner-supplied model + WebSocket runtime. UI (MIA-UX-04) should read `GET /api/voice/status` -> `offline_wake_word`, then open `WS /api/voice/wake/stream` per `docs/voice/WAKE_WORD_PLAN.md` section 6a, and must show a visible Stop.
- Tests actually run: full suite with Chromium 1835 passed, 1 skipped, 0 failed; ruff clean.
- Known limitation recorded: status cannot detect a non-loopback bind.

## 2026-11-21T16:40Z — MIA-UX-04 claimed by A4 (A6 idle)

- A4 claims MIA-UX-04 because its blockers are DONE and A6 reported no unblocked task. Edits to `src/mia/assistant.html` will be confined to a new, self-contained wake-word panel plus the minimum glue to the existing voice session; no changes to chat, project, error-recovery or stop-reply code. A6: if you resume, rebase before touching `assistant.html` and tell me here.

## 2026-11-21T16:05Z — MIA-BRAIN-07 claimed

- Agent: A1. IN_PROGRESS. Bound the live runtime-evidence prompt block. Paths: src/mia/brain/self_description.py, src/mia/brain/context.py (budget accounting), tests. Will not change the wake/voice routes (A4) beyond the manifest call site.

## 2026-11-21T16:04Z - MIA-UX-06 claimed (A6)
- A4 holds MIA-UX-04 (wake-word UI) and is the active editor of src/mia/assistant.html; A6 leaves that task and file alone. A6 takes MIA-UX-06: read-only Quality controls panel (judges + escalation policy/counters) in src/mia/gateway/dashboard.html with tests/test_gateway_quality_panel.py. No backend changes.

## 2026-11-21T16:30Z — MIA-BASE-05 DONE
- Agent: A5. Commit 4bedddd on remote main (verified via git ls-remote).
- Paths: src/mia/codegraph/indexer.py, impact.py, graph.html, src/mia/observatory/api.py (status route + one import only), scripts/update_code_graph.py, tests/test_code_graph_impact_git.py, tests/test_code_graph_api.py, docs/code-graph/README.md.
- Result: GET /api/code-graph/status returns `stored_graph` freshness (state + reasons source_changed/commit_changed); `--check` is a non-parsing check using the same function; compute_source_digest is tested equal to build_graph's digest. Impact for deleted/renamed paths now lists remaining importers/tests using the merge-base commit's graph from a safe git-archive extraction (built only when a changed path is missing from the current graph). Explorer shows uncommitted-path count.
- Tests actually run: graph suites 36 passed after final rebase; mutation check (removing base-graph use and commit_changed detection) failed 3 tests; ruff clean on touched files. Full suite at 4bedddd in my sandbox: 1838 passed, 2 skipped, 1 failed (tests/test_voice_wake_transport.py::test_status_endpoint_..., A4's area) because my sandbox lacked the declared `websockets` dependency; after installing it that file passes (31 passed). I did not re-run the entire suite after installing it.
- Limitations: no auto-refresh on commit; static imports and inferred TESTED_BY only; Linux only (Windows OWNER_VERIFICATION_REQUIRED). graph.json is git-ignored; no new CHANGELOG entry was generated for this commit.


## 2026-11-21T17:30Z — MIA-UX-04 completed by A4

- Agent: A4 (took the task because its blockers were DONE and A6 was IDLE). Paths: `src/mia/assistant.html` only for the new `#offlineWake` panel + CSS + a self-contained JS block, three glue lines (stop wake on voice start, on Local record, and in the existing `pagehide` handler); `tests/test_voice_wake_ui.py` (new, 10 tests); docs. No chat, project, error-recovery or stop-reply code changed. A6: rebase before touching `assistant.html`.
- Two existing tests collided with my first draft (a second `.voice-privacy` element and a second `pagehide` listener); fixed by renaming my paragraph class and folding my cleanup into the existing handler, not by changing those tests.
- Tests actually run: full suite including Chromium 1846 passed, 1 skipped, 0 failed; ruff clean. Mutation check 10/11 (1 equivalent). A first mutation run was invalid (Chromium path env var missing => all skipped); redone with the full environment.
- Not verified: real speech/hardware/Windows/assistive-tech/narrow layout (see STATUS and OWNER_VERIFICATION section G).

## 2026-11-21T16:15Z — A5 Code Graph maintenance (no task claimed)
- Agent: A5. Ran `python scripts/update_code_graph.py` on a clean tree at 9bfc84a: 4369 nodes, 8952 edges, 0 parse errors; CHANGELOG entry committed. `--check` reported stale (source_changed, commit_changed) before and current after, on the real repository.
- Not started: M2–M6 work. docs/DEFERRED_MILESTONE_BACKLOG.md gates all of it on the M1 exit criteria; A5 cannot declare that gate and has no M1 task open. Needs owner/coordinator: either confirm the M1 gate is met, or approve a named exception.
- Limits: graph.json is git-ignored, so only the CHANGELOG entry is in the repo; Linux sandbox only.

## 2026-11-21T16:15Z - MIA-UX-06 DONE (A6)
- Commit `7def45667906167f222f4364e3795e910c0365d6` verified on remote main (ls-remote and merge-base --is-ancestor).
- Paths: src/mia/gateway/dashboard.html, tests/test_gateway_quality_panel.py, docs. No backend changes; no assistant.html edits (A4 holds MIA-UX-04).
- Delivered: read-only Quality controls panel (judges + escalation policy and counters) on /gateway. Separate pre-existing defect fixed in the same commit: ~6 KB of duplicated truncated script trailing `</html>` in dashboard.html (since commit 7325708) rendered as stray visible text.
- Tests actually run: full suite at the pushed head 1855 tests, 1854 passed, 1 skipped, 0 failed, after installing the declared `websockets` dependency; before installing it, `test_voice_wake_transport.py::test_status_endpoint_reports_session_and_capability_but_no_arming_route_exists` failed (also on commit 2cd67da, before this change), because `audio_transport_available` needs a WebSocket runtime. ruff clean. No CI. The 3 new tests fail on the previous dashboard.
- Unverified (OWNER_VERIFICATION_REQUIRED): real-browser rendering and narrow-screen layout, screen readers.
- Note for A4/owner: contributors who installed the project before the `websockets` dependency was added will see that voice test fail until they reinstall (`pip install -e .[dev]`).
- A6 status: IDLE. Remaining A6 items: MIA-UX-04 is held by A4; M2-M6 stay gated.

## 2026-11-21T16:40Z — A5 integration check of main at bdc8054 (no task claimed)
- Agent: A5. Ran `pytest tests` on the tree at bdc8054 in a Linux sandbox with declared dependencies installed (incl. websockets): 1865 passed, 1 skipped, 0 failed, ~220 s. `ruff check src tests scripts`: clean. Chromium-dependent tests ran only to the extent they run in this sandbox; Windows/physical-device checks remain OWNER_VERIFICATION_REQUIRED.
- M1 gate: not declared met by A5. docs/ROADMAP.md still lists remaining Priority 1 work (full-history semantic indexing, model-assisted extraction, streaming speech I/O, complete text/voice pipeline verification). A5 has no open M1 task; M2-M6 stay deferred pending owner/coordinator decision.
## 2026-11-21T16:10Z — MIA-GW-04 DONE (A2)

- Gateway-level provider timeouts in src/mia/gateway/service.py (attempt 180s, stream idle 120s, env overridable): ProviderTimeout -> fallback + breaker, never retried on the same provider, hung call cancelled, paid reservation kept until expiry. tests/test_gateway_timeouts.py (9 tests; reservation rule mutation-checked). Docs: docs/architecture/AI_GATEWAY.md.
- Commit on remote main: 2cd67da (verified with git ls-remote). Full suite on my final run: 1850 passed, 1 skipped, 0 failed; the one failure on my first run (tests/test_voice_wake_transport.py status test) happens when the optional `voice-wake` extra's `websockets` is not installed (a plain `pip install -e .` does not install it); it passes with websockets installed. Not a product defect, but the test has an unguarded dependency on that extra - suggestion for A4: skip or monkeypatch `websocket_runtime_available`. (My interim note claiming a plain reinstall fixes it was wrong and is corrected here.)
- Not covered: total time across several fallback candidates is attempts x timeout; a per-turn deadline would be a separate task. No live-provider evidence.

## 2026-11-21T17:00Z — MIA-EVOL-02 claimed (owner-approved exception)

- Agent: A5. Status: IN_PROGRESS. The owner replied "approve" to A5's request for an exception to the M2-M6 gate and named no other task; A5 interpreted that as approval for MIA-EVOL-02, the task it proposed first. If the owner meant a different task, say so and A5 will stop. Scope: audit first, then one highest-impact safeguard; paths src/mia/evolution/**, tests, docs.


## 2026-11-21T19:27Z — MIA-BRAIN-06 claimed (A1, fresh session)

- Agent: A1 (Claude Sonnet 5.5, fresh session). A2 was already registered by another live session ten seconds earlier, so A2 was not taken. Slot A1 was unregistered; the older A1 claim MIA-BRAIN-07 (IN_PROGRESS, 2026-11-21T16:05Z) belongs to a previous session and is left untouched until its checkpoint is inspected. Scope: short design note first, then record-level project scoping for structured memory; paths src/mia/memory/**, src/mia/brain/context.py, tests, docs.

## 2026-11-21T20:29Z — MIA-GW-05 claimed (A2)

- Agent: A2 (fresh session, Anthropic Claude Sonnet 5.5). Status: IN_PROGRESS. Scope: trace live gateway vs M1 requirements; implement the single highest-impact source-backed gap not covered by GW-03/GW-04, fake-provider tests. Paths: src/mia/gateway/**, tests, docs. No FreeLLMAPI/OmniRoute.

## 2026-11-21T20:30Z — MIA-M1-08 claimed (A3, fresh session)

- Agent: A3 (Anthropic Claude Sonnet 5.5, fresh session). Status: IN_PROGRESS. A1 and A2 are held by other sessions, so neither was taken. A3 was unregistered; the older A3 claim MIA-EVAL-05 (IN_PROGRESS, 2026-11-21T16:04Z) belongs to a previous session and is left untouched until its checkpoint is inspected. Scope: M1 exit-criterion traceability matrix (source, tests, owner checks); gap tasks only where demonstrable. Paths: docs only unless a gap task is separately claimed.

## 2026-11-21T20:33Z — MIA-M1-11 claimed (A4, fresh session)

- Agent: A4 (Anthropic Claude Sonnet 5.5, fresh session). Status: IN_PROGRESS. A1-A3 are held by other sessions; A4 was unregistered with no open A4 claim. Scope: audit actual STT/TTS, streaming, EN/Urdu/Roman Urdu handling, fallback and latency reporting; deterministic adapter tests and fixtures only, no real-speech quality claims. Paths: src/mia/voice/**, tests, docs.

## 2026-11-21T20:31Z — A6 registered; MIA-M1-10 claimed (fresh session)

- Agent: A6 (Anthropic Claude Sonnet 5.5, fresh session). A3 was taken by another live session at 20:30Z (a simultaneous claim of MIA-M1-08), so A3 was not used. A6 was UNREGISTERED and its owner-facing-controls scope fits MIA-M1-10 (A1 + A6). Status: IN_PROGRESS. Plan: source-backed audit first of conversation and structured memory inspect/correct/export/delete; then implement only verified gaps. A1 (live, MIA-BRAIN-06) owns `src/mia/memory/structured.py`/store schema work; A6 will not edit those files concurrently and will record any proposed interface here first.

## 2026-11-21T20:33Z — A5 registered (fresh session); MIA-EVOL-02 resumed

- Agent: A5 (Anthropic Claude Sonnet 5.5, fresh session). A1-A4 and A6 were held by other live sessions (A4 had registered seconds earlier with a 2026-11-21T20:33Z heartbeat), so none were taken; A5 was UNREGISTERED, so it was the only free slot.
- No A5-owned M1 task exists (M1 tasks are A1/A3/A4/A6). The OPEN M1 tasks MIA-M1-09 (A1 + A4) and MIA-M1-12 (A4 + A6) are left for the A4/A1/A6 sessions; A5 is not a named owner of either.
- Inspected the previous A5 checkpoint: MIA-EVOL-02 was claimed at 2026-11-21T17:00Z (commit 8b31bfa, the owner-approved exception recorded on the board) with no implementation commits after it, and no live session owns slot A5. Resuming it under the same scope: audit src/mia/evolution/** against the catalog, then implement the single highest-impact missing safeguard with tests. Generated code must not run in the privileged process; failed validation must block activation.

## 2026-11-21T20:34Z — MIA-M1-08 DONE; MIA-EVAL-06 claimed (A3)

- Agent: A3. Commit 87a9b36 on remote main (verified with merge-base --is-ancestor). Deliverable: docs/M1_TRACEABILITY.md (criterion -> source -> tests -> owner checks).
- Tests actually run: `python -m mia.brain.evaluation` 26/26; `pytest tests` excluding 8 Chromium files: 1808 passed, 1 skipped, 0 failed (tallied from progress markers; no summary line printed). Chromium files not run.
- Findings: (1) eval suite lacks identity/capability, provider-switch continuity and category-coverage checks -> new MIA-EVAL-06 (claimed now by A3). (2) `assistant.html` never sends `interface="voice"`, so real voice turns are recorded as text and are not exempt from judge latency; handed to MIA-M1-09 (A1+A4), no new task. (3) Stale claims MIA-EVAL-05, MIA-BRAIN-07, MIA-EVOL-02 left untouched.
- Paths for EVAL-06: src/mia/brain/evaluation.py, tests/test_brain_evaluation.py, docs.

## 2026-11-21T20:50Z — MIA-M1-11 DONE (A4)
- Paths: src/mia/voice/stt.py, src/mia/observatory/api.py (voice routes only), tests/test_voice_language_latency.py, docs/STATUS.md. Details in STATUS.md "MIA-M1-11".
- Tests run: 83 passed, 0 failed across voice/STT/TTS/language-routing/voice-UI-contract suites; ruff clean; new tests fail without the source change. Full suite not completed here (sandbox time limit). Code Graph refresh not run.
- Limits: no real-speech or real-latency evidence; Roman Urdu has no Whisper code; streaming STT/TTS still absent. Follow-up gap for the M1 matrix (A3, MIA-M1-08): streaming speech I/O.

## 2026-11-21T21:00Z — MIA-M1-12 claimed (A4)

- Agent: A4. Status: IN_PROGRESS. Scope: audit interruption/barge-in, stop/mute, mic release, retry, timeouts and partial-response persistence across text/voice; fix verified gaps with tests. A6 is on MIA-M1-10 (memory controls) and may touch assistant.html, so A4 will audit and prefer backend/test changes, re-fetching before any assistant.html edit.

## 2026-11-21T21:20Z - MIA-M1-12 DONE (A4)
- Paths: src/mia/brain/orchestrator.py (stream_turn error handler), tests/test_brain_interruption_recovery.py, docs/STATUS.md. A1 owns the Brain; the change is a two-line correctness fix to the streaming error path, recorded here for A1 review.
- Tests run: 31 passed, 0 failed (new file + brain pipeline + judge pipeline); new duplicate test failed before the fix. Chromium suites and full suite not run. Code Graph refresh not run.
- Open: buffered tool-turn cancellation test; owner-only hardware checks.

## 2026-11-21T21:35Z - MIA-M1-09 claimed (A4)

- Agent: A4. Status: IN_PROGRESS. Starting point: A3's verified gap (M1_TRACEABILITY row 4): assistant.html never sends `interface` for spoken turns. Scope: fix that with tests, then trace text vs voice through identity/context/Brain/gateway/verification/persistence with contract tests. assistant.html is also touched by A6 (MIA-M1-10): edits will be minimal and re-fetched immediately before commit.

## 2026-11-21T20:45Z — MIA-GW-05 DONE (A2)

- Agent: A2 (Anthropic Claude Sonnet 5.5 session). Commit `e382c476f53be1564336740b8d8f67ad54c2c759` on remote main (verified with `git ls-remote`).
- Paths: src/mia/gateway/contracts.py (`ModelUnavailableError`), providers.py (HTTP 404/410 mapping), service.py (fallback in `complete`/`stream`, health degrade, end-of-chain error), tests/test_gateway_model_unavailable.py (13 new), docs/architecture/AI_GATEWAY.md, docs/STATUS.md, task board.
- Gap found by audit: a model removed/renamed at its provider (404/410) aborted the whole fallback chain as a generic permanent error. Existing tests that make 400-style errors abort the chain were left untouched.
- Tests actually run: new file 13 passed; mutation check (handlers removed -> stream and HTTP tests fail; 404 mapping removed -> HTTP tests fail); gateway+brain subset passed on the rebased tree; full suite before rebase: 1925 collected, no failures, 1 skipped (Chromium path unset); ruff clean. Full suite was not re-run after the final rebase onto other agents' commits (only the gateway/brain subset was).
- Limitations / follow-ups: registry entry not marked stale on 404 (health degrade is process-local); no per-turn total deadline across fallbacks; no live-provider evidence.

## 2026-11-21T20:43Z — MIA-EVAL-06 DONE (A3)

- Commit c364c65 on remote main (verified: that commit is an ancestor of origin/main after two rebases over A4 pushes; no force-push). Paths: src/mia/brain/evaluation.py, tests/test_brain_evaluation.py, docs/M1_TRACEABILITY.md, docs/STATUS.md.
- Tests actually run: `python -m mia.brain.evaluation` 31/31; targeted brain tests 49 passed; `pytest tests` excluding 8 Chromium files 1815 passed, 1 skipped, 0 failed (before the final rebase over A4's MIA-M1-11/12 commits; not re-run after); ruff clean; mutants (no provider switch; identity text ignored) each failed the new tests. Chromium files not run.
- Limitations: scripted providers only, no live-model quality; voice turn-taking/interruption remain browser tests outside the suite.
- Open for MIA-M1-09 (A1+A4): assistant.html never sends interface="voice" (see M1_TRACEABILITY row 4). A3 did not touch assistant.html or orchestrator.py.
- A3 status: IDLE. Not claimed next: MIA-EVAL-05 (P2) is held by a prior A3 session's claim with no checkpoint found in this log; it should be inspected by the owner/coordinator before reassignment.

## 2026-11-21T20:46Z — MIA-GW-06 created and claimed (A2)

- Agent: A2. No eligible A2 task remained (M1 queue fully DONE/claimed; M2+ have no A2-only work and later milestones are gated). Created MIA-GW-06 from the documented GW-04 limitation: per-turn total deadline across fallback candidates. Paths: src/mia/gateway/service.py, tests/test_gateway_turn_deadline.py, docs.

## 2026-11-21T21:55Z - MIA-M1-09 DONE (A4)
- Paths: src/mia/assistant.html (two request bodies only; A6 is on MIA-M1-10 in the same file, merged cleanly via pull --rebase), tests/test_voice_browser.py, docs/STATUS.md.
- Tests run: voice browser + stop-reply + UI contract suites passed (72); new tests fail without the fix; ruff clean; full suite and Code Graph refresh not run. Closes the row 4 gap in docs/M1_TRACEABILITY.md (A3 may update its matrix).

## 2026-11-21T22:10Z - MIA-VOICE-05 created (A4, OPEN, unclaimed)

- No dependency-ready A4 task remained (HOLO-04 needs HOLO-01 and HOLO-03; M1-13 is the acceptance gate). Source audit produced one unique task: replies are spoken only after the full streamed reply arrives. Details in the board row. Not started: it edits the shared assistant.html, so it should be claimed deliberately (A4 or A6) after A6's MIA-M1-10 edits land.

## 2026-11-21T20:47Z — MIA-EVAL-05 re-claimed and implemented (A3)

- Agent: A3. Checked first: no `live_eval.py`, test or checkpoint for EVAL-05 exists on main and the A3 slot has no other live session, so the stale 2026-11-21T16:04Z claim was taken over by this session.
- Paths (new files only plus docs): src/mia/brain/live_eval.py, tests/test_live_eval.py. Deviation from the row text: models are pinned by a wrapper that exposes a single local model to the gateway rather than via routing preferences (same effect, provider-agnostic).
- Tests actually run: tests/test_live_eval.py 6 passed; three mutants (no pin, no local-only filter, no model cap) each failed tests; ruff clean. No real model was run.

## 2026-11-21T20:51Z — MIA-M1-10 DONE (A6)

- Commit 3703885 on remote main (verified with `git ls-remote`). Paths: `src/mia/memory/store.py`, `src/mia/observatory/api.py` (conversation delete route + one preview route only), `tests/test_memory_owner_controls.py` (new, 15 tests), docs. `structured.py` was not touched (A1's MIA-BRAIN-06 area).
- Gaps proven by running the code, then fixed: (1) `purge_older_than(dry_run=False)` left `memory_usage` rows for purged conversations and knowledge items, contradicting `usage.py`'s own invariant, and wrote no audit entry; (2) `delete_conversation` wrote no audit entry; (3) deleting a conversation left structured memories captured from it recallable with no owner-visible indication; (4) `export_data` omitted each conversation's `temporary` and `project`.
- Behaviour: derived memories are kept by default (they may be deliberate owner facts) but always listed; `forget_derived=true` deletes each on its own (no correction chain, so an owner correction survives) and also works after the conversation is gone. Audit entries hold ids and counts only. Export gains two additive fields; `schema_version` stays 1.
- Tests actually run: new file 15 passed; 6 memory suites passed after final rebase; full suite before final rebase 1926 passed, 1 skipped (websockets installed); `ruff check src tests` clean. Mutation check: against the original source the new API/store tests fail. CI not used.
- Not done / limits: no UI control (the Assistant has no delete-conversation button); no import/restore of an export; export excludes the audit log and edit-history snapshots; API deletes do not require a confirm flag (preview + opt-in flag only); no live-model or browser evidence. A1: if MIA-BRAIN-06 adds a record-level `project`, include it in `structured.export()` consumers.

## 2026-11-21T20:52Z — MIA-HOLO-01 claimed (A6)

- Agent: A6. The owner told this session in chat to start MIA-HOLO-01; MIA-VOICE-05 (M1, A4) was still OPEN, so this is recorded as an owner-directed start, not a gate pass. Scope: typed runtime-state/event contract (idle, listening, thinking, speaking, interrupted, tool-running, approval, failed, offline) mapped to real API evidence, with stale/unknown handling and contract tests. No renderer, no new SPS layer, no fabricated activity. A1 co-owns by assignment; no edits to A1 files planned.

## 2026-11-21T20:52Z — MIA-EVAL-05 DONE (A3)

- Commit e63d04c on remote main (verified as ancestor of origin/main). Tests: live_eval 6 passed; full non-Chromium suite 1888 tests, 0 failed, 1 skipped; ruff clean; 3 mutants caught. Chromium files not run. No real model run: OWNER_VERIFICATION_REQUIRED.
- A3 status: IDLE. Remaining open tasks that match A3 are all later-milestone (M5/M6) or shared with agents who hold them; M1-13 is blocked until M1-09/M1-10 finish.

## 2026-11-21T20:53Z — MIA-GW-06 DONE (A2)

- Agent: A2 (Anthropic Claude Sonnet 5.5 session). Commit `11e392f946bf8846003c999f3cf66150946c8208` on remote main (verified with `git ls-remote`). A STATUS.md rebase conflict with concurrent A3/A6 entries was resolved by keeping all sections.
- Paths: src/mia/gateway/service.py, tests/test_gateway_turn_deadline.py (10 new), docs/architecture/AI_GATEWAY.md, docs/STATUS.md, task board.
- Tests actually run: new file 10 passed; 3 mutation checks each failed the intended tests; full suite before the final rebase: no failures, 1 skipped (Chromium path unset); after the rebase only the gateway subset was re-run (passed); ruff clean.
- Remaining A2 candidates (not claimed): mark registry entry stale on HTTP 404; a mid-stream paid-spend cut-off and rate-limit header ingestion (listed as not-yet-done in AI_GATEWAY.md). M2+ A2 tasks are gated until the M2 queue is accounted for. A2 status: IDLE.

## 2026-11-21T20:54Z — MIA-GW-07 created and claimed (A2)

- Agent: A2. No eligible A2 task (M1 only has MIA-VOICE-05 open, A4's; M3+ gated). Scope: empty-model-output fallback, paths src/mia/gateway/**, tests/test_gateway_empty_response.py, docs.

## 2026-11-21T20:59Z — MIA-HOLO-01 DONE (A6)

- Commit 1691e42 on remote main (verified with `git ls-remote`). Paths: `src/mia/observatory/runtime_state.py` (new), `src/mia/observatory/api.py` (one import + one `register_runtime_routes` call), `tests/test_runtime_state.py` (new, 41 tests), docs.
- Contract: states unknown/idle/listening/thinking/speaking/interrupted/tool_running/awaiting_approval/failed/offline. Each non-unknown state is reachable only from registered evidence kinds with a stated real backing (chat SSE events, the page's voice session, wake session, computer-task rows, approval rows, backend reachability). No fresh evidence is `unknown`, never `idle`; stale evidence is ignored and listed; unknown kinds, future/naive timestamps and server-only kinds sent by a client are rejected; free-text detail is dropped; clients send `age_seconds`, not timestamps.
- Defect found while mapping: `ApprovalStore.list_pending()` does not filter by expiry, so an expired pending request would read as "awaiting approval" forever. The collector filters expired rows itself; `list_pending` is unchanged (A5/computer area).
- Tests actually run: new file 41 passed; full suite 2050 passed, 1 skipped; `ruff check src tests` clean. Mutation checks: disabling the TTL check fails 2 tests; removing the expiry filter fails 1.
- Limits: no UI consumes the contract yet; chat, voice and wake evidence is client-reported (the backend has no in-flight-turn tracker, so it cannot see a running chat turn by itself); `paused`/`completed` tasks add no state; no live-hardware evidence. Interface for A1/A4: HOLO-04 should post client evidence to `/api/runtime/resolve`; adding an evidence kind means adding it to `EVIDENCE_KINDS` with a backing and test.
- Next: MIA-HOLO-02 (renderer spike) depends on HOLO-01, now done.

## 2026-11-21T20:59Z — MIA-HOLO-02 claimed (A6)

- Agent: A6. Dependency MIA-HOLO-01 is DONE. Scope: compare browser 3D options (bundle size, WebGL support, Windows hardware, licensing, accessibility), build a minimal isolated renderer spike, document an evidence-based choice and fallback. No full visual build. New files only plus docs.

## 2026-11-21T21:00Z — MIA-GW-07 DONE (A2)

- Agent: A2. Commit `054a85076468c9f57e254d92475b0913d0d60359` on remote main (verified with `git ls-remote`). Two STATUS.md rebase conflicts with concurrent agents were resolved by keeping all sections; stray backslash-escaped backticks in my docs text were fixed.
- Paths: src/mia/gateway/{contracts,providers,service}.py, tests/test_gateway_empty_response.py (10 new), tests/test_gateway_model_unavailable.py (GW-05 reservation test corrected: it never attempted the paid candidate because of free-first ranking), docs/architecture/AI_GATEWAY.md, docs/STATUS.md, task board.
- Tests actually run: new tests pass; four mutation checks fail the intended tests; full suite before the last rebase: no failures, 1 skipped (Chromium path unset); after the rebase the gateway subset (all passed) and ruff only. 
- A2 status: IDLE. Remaining candidate A2 work (not claimed): rate-limit remaining-quota header ingestion, mid-stream paid-spend cut-off. No eligible M1 A2 task; M3+ gated.

## 2026-11-21T21:01Z — MIA-BRAIN-06 DONE (A1)

- Commit `44f45e6` verified on remote main (merge-base check after push). Paths: src/mia/memory/{schema,structured,context,capture}.py, src/mia/observatory/api.py, tests/test_record_project_scope.py, docs/architecture/MEMORY_AND_KNOWLEDGE.md.
- Delivered: nullable structured-record `project` (migration 5); recall filter on keyword, intent, semantic and linked paths; capture tagging and scoped duplicate/conflict/update; owner API to set/clear project (audited). Opt-in; default behaviour unchanged.
- Tests actually run: 17 new tests (mutation-checked on the four recall filters; capture scoping not mutation-checked); full suite before rebase 1928 passed, 1 skipped, 0 failed with `websockets` installed; after rebase only the memory/capture/context/project suites were re-run (all passed); ruff clean. No CI.
- Not covered: no UI for record project; scope is a post-retrieval filter (pool can shrink); not a security boundary; no live-model evidence.

## 2026-11-21T21:01Z — MIA-BRAIN-07 DONE (A1, record reconciliation)

- The implementation landed earlier in commit `5b0c3f7` (ancestor of remote main, confirmed with merge-base) by a previous A1 session that never updated the board. This fresh A1 session did not rewrite it; it re-verified and recorded it.
- Verified now: `pytest tests/test_runtime_evidence_budget.py tests/test_self_description.py` all passed; `build_context` counts the system message (identity + evidence block) in the fixed token cost against `token_budget`. Compact block by default, full block only when the turn asks about identity/tools/voice/providers/architecture.
- Not verified: live-model behaviour; detector false negatives/positives are only exercised on scripted prompts.

## 2026-11-21T21:03Z — MIA-LIFE-12 claimed (A2, owner-approved M3 exception)

- Agent: A2. The owner approved an M3 exception in chat in reply to A2's offer, which named MIA-LIFE-12. Dependency (gateway + runtime telemetry) is available. A6 is on MIA-HOLO-02 and not touching dashboard.html/observatory api.py usage routes; A6: tell me here if you need those files.

## 2026-11-21T21:04Z — MIA-LIFE-01 claimed (A1, owner-approved exception)

- Owner instruction in chat: "get the other milestones tasks and do them"; treated as approval to start M2-M6 work ahead of the milestone gate (same precedent as A5/A2). Explicit task dependencies still apply: chain LIFE-01 -> LIFE-02 -> LIFE-04 -> LIFE-05. Scope: audit existing memory/projects/life settings first, then the owner-scoped personal-data model, provenance, retention, export/delete tests. Paths: src/mia/memory/**, tests, docs.

## 2026-11-21T21:07Z — MIA-HOLO-02 DONE (A6)

- Commit 1176c8e on remote main (verified with `git ls-remote`). New paths only: `spikes/holo-renderer/**` (isolated; nothing in `src/mia` imports it; generated `spike.html` and `node_modules` are git-ignored), `tests/test_holo_renderer_spike.py`, `docs/architecture/HOLO_RENDERER_DECISION.md`, `.gitignore` (3 lines).
- Evidence: minimal-scene bundles built and gzipped here: OGL 14,041 B, three.js 133,157 B, Babylon core 302,092 B; licences from package.json (Unlicense, MIT, Apache-2.0). Decision: three.js, with OGL as the documented fallback; reversal criteria in the decision doc.
- Tests actually run: new file 8 passed in Chromium (software GL); full suite 2070 passed, 1 skipped; ruff clean. The tests skip when node/npm or Chromium is unavailable (they run `npm ci` once, which needs the npm registry). One real bug found and fixed by a test: `display:block` overrode the `hidden` attribute, showing canvas and fallback together.
- Limits: no real GPU/Windows/driver evidence; WebGL2-specific behaviour not checked; Babylon's maintenance date not checked; no raw-WebGL variant measured. Interface for HOLO-03/05/06: only `renderer.js` imports three.js; use `createHoloRenderer`; states are the HOLO-01 names.
- Next: MIA-HOLO-03 (scene and visual system) is unblocked by this task.
## 2026-11-21T22:30Z - MIA-VOICE-05 claimed (A4)

- A6's MIA-M1-10 has not touched assistant.html on main yet (last edit 65cbec8). Editing speakReply/submitPrompt/streamReply minimally; will re-fetch before commit.

## 2026-11-21T23:05Z - MIA-VOICE-05 DONE for the tools-off streaming path (A4)
- Paths: src/mia/assistant.html (streamReply onDelta, speakReply append mode, createStreamingSpeaker, submitPrompt wiring), tests/test_voice_browser.py, tests/test_assistant_stop_reply.py (one assertion), docs. No overlap with A6 edits at pull time (rebased cleanly).
- Tests: see STATUS.md. Pre-existing test_voice_wake_ui.py timeouts in this sandbox (baseline identical). Full suite and Code Graph refresh not run.
- Follow-up needed (A1/A2): default tools-on voice turns are buffered server-side, so speech still starts after the full reply; streaming tool-bearing turns when no tool is called would close it.

## 2026-11-21T21:11Z — MIA-LIFE-01 DONE, MIA-LIFE-02 claimed (A1)

- LIFE-01 commit `5c5f14f` verified on remote main. Paths: src/mia/memory/{personal,schema,store}.py, src/mia/observatory/api.py, tests/test_personal_data.py, docs/architecture/MEMORY_AND_KNOWLEDGE.md. Delivered: `personal_items` (migration 6), provenance, project filter, credential refusal, hard delete with id-only audit, owner-triggered retention (preview default), export, REST API, privacy contract.
- Tests actually run: 14 new tests (4 mutations caught: purge scope, audit text, timezone requirement, project filter); full suite 2106 passed, 1 skipped, 0 failed (progress-dot count; `websockets` installed); ruff clean. After the final rebase only the new personal/project tests were re-run.
- Not covered: no UI, no recurrence/timezone display, no Brain intents, not encrypted at rest, no live evidence. A5 (co-owner) was not consulted on encryption boundaries; the boundary is documented as 'none yet, see LIFE-11'.
- LIFE-02 (A1) claimed next: recurrence policy, duplicate handling, status history, timezone semantics on top of `personal_items`.

## 2026-11-22T09:29Z — MIA-HOLO-03 claimed (A6)

- Agent: A6, resumed after a pause; re-read main, board and registry first. Dependencies HOLO-01/02 DONE. Scope: a responsive holographic scene module with the ten HOLO-01 visual states, isolated from Brain/business logic, served by the app as a static asset and shown on a dedicated page, with deterministic tests. Not binding live events (HOLO-04), accessibility fallback depth (HOLO-05) or profiling budgets (HOLO-06). No edits to A1/A2/A4 areas; `assistant.html` is not changed in this task.

## 2026-11-22T09:29Z — MIA-M1-13 claimed (A3)

- Agent: A3. Dependency check: every other M1 row on the board is DONE (M1-08..12, BRAIN-06/07, VOICE-05, GW-01..06, EVAL-03/05/06). Scope: re-run suite and eval on current main, update the criterion matrix, write docs/M1_EVIDENCE_REPORT.md separating automated completion from owner-only checks. Docs only. Does not declare M1 accepted; the owner decides.

## 2026-11-22T09:50Z - MIA-LIFE-09 claimed (A4, owner-approved exception)
- Owner approved A4 taking MIA-LIFE-09 by name (M2->M3 gate exception, same form as A1/A2/A5). Dependencies: LIFE-01 DONE; LIFE-02 (A1) still IN_PROGRESS, so A4 builds the source-agnostic notification core (`src/mia/life/notifications.py`, own SQLite file, IANA-timezone/DST-aware quiet hours, dedupe, expiry, snooze, caps, explain) with a narrow owner API. Task-sourced notifications (due dates/recurrence) are NOT built here; they plug in via `submit()` once LIFE-02 lands. Accessible UI is the A6 half and is left open. Existing `life_outreach` inbox and fixed-offset `in_quiet_hours` are untouched.

## 2026-11-22T09:35Z — MIA-M1-13 report delivered (A3)

- Commit 9dddaef on remote main (verified). docs/M1_EVIDENCE_REPORT.md written from a run on 5a64019: eval 31/31; full pytest incl. 8 Chromium files 2119 tests, 0 failed, 1 skipped; ruff clean. Owner-only checks listed per criterion. M1 is NOT declared accepted: that needs the owner (real model, microphone, Windows).
- A3 status: IDLE. Every remaining open task is later-milestone (M2+) or assigned to other agents; A3-led OBS-05 is P6 and M6 work has not been approved by the owner for exception, so it is not claimed.

## 2026-11-22T09:37Z — MIA-LIFE-02 DONE, MIA-LIFE-04 claimed (A1)

- LIFE-02 commit `3dc0fa5` verified on remote main. Paths: src/mia/memory/{personal,schema,settings,store}.py, src/mia/observatory/api.py, tests/test_task_manager.py, docs/architecture/MEMORY_AND_KNOWLEDGE.md. Delivered: priority, recurrence (daily/weekly/monthly, owner-timezone wall clock, DST-safe, month-end clamp, complete() spawns next once), duplicate refusal (409), structural history without text, owner_timezone setting, overdue/due_before filters, migration 7.
- Tests actually run: 16 new tests (7 mutations caught: fixed 24h step, month clamp, skip missed, duplicate scope, history text, spawn, idempotent complete); full suite 2135 passed, 1 skipped, 0 failed by progress-dot count; ruff clean; after the final rebase only the personal/task/project suites were re-run. Not covered: UI, Brain intents (LIFE-03), reminder delivery, calendar, sub-tasks, fixed-calendar recurrence, no live evidence.
- LIFE-04 (A1 data/API side) claimed: project goals/status/milestones/decisions, links to tasks and structured records, project-scoped context, cross-project leakage tests. The A6 UI half is not started.

## 2026-11-22T10:40Z - MIA-LIFE-09 backend half DONE (A4, owner-approved exception)
- Paths: src/mia/life/notifications.py, src/mia/life/notification_api.py, src/mia/observatory/api.py (import + 2 wiring lines + `notifications` parameter on create_app), pyproject.toml (tzdata, win32 only), tests/test_notifications.py, tests/test_notification_api.py, docs/STATUS.md.
- Tests: see STATUS.md; 50 new tests, 4 mutation checks, regression suites passing; full suite and Code Graph refresh not run.
- Open for others: A6 UI (list, snooze/dismiss, settings, "why" panel, accessibility); A1 producers after LIFE-02 (task due/recurrence); voice-session hook calling voice_digest() (A4 can do once a producer exists).

## 2026-11-22T09:43Z — MIA-HOLO-03 DONE (A6)

- Commit 0df4a2d on remote main (verified with `git ls-remote`). Paths: `frontend/holo/**` (moved from `spikes/holo-renderer`, sources), `src/mia/observatory/hologram.html` (built, committed, 542 KB), `src/mia/observatory/api.py` (one `GET /hologram` route), `pyproject.toml` (package-data entry), `.gitignore`, `tests/test_holo_scene.py` (replaces `test_holo_renderer_spike.py`), docs. `assistant.html` and A1/A2/A4 files untouched.
- Behaviour: ten states with distinct motion/shape signatures; unrecognised names show `unknown`, never `idle`; statement text from `/api/runtime/contract` with an embedded fallback; page states plainly that it is a preview.
- Real bug found by a test and fixed: `STATE_LOOKS[state]` accepted inherited keys, so `?state=constructor` (or `setState('constructor')`) selected a nonexistent state. Now `Object.hasOwn`. Regression test sends such names straight to the renderer; page-level tests alone could not catch it because the page normalises first.
- Tests actually run: new file 22 passed, 5 consecutive runs stable; full suite 2132 passed, 1 skipped; ruff clean. Mutation checks: reintroducing the inherited-key bug fails 1 test; unknown->idle fails 1 test; a source edit without rebuilding fails the staleness test. One test was initially flaky (the page re-applies its picker state after the contract fetch, overwriting a directly-driven renderer); fixed by waiting for network idle in the test opener.
- Limits: software GL only, no real-GPU/Windows evidence (HOLO-06); not wired to live events (HOLO-04); accessibility is basic (status live region, decorative canvas, text fallback) with no screen-reader or contrast audit (HOLO-05); the committed 542 KB bundle bloats the repo diff each rebuild (the staleness test guards drift, not size).
- Next: MIA-HOLO-04 (A6 + A4 + A1) is unblocked: post client evidence to `POST /api/runtime/resolve` and call `window.__holo.show(state)` equivalent (`show` in `frontend/holo/src/main.js`).

## 2026-11-22T09:45Z — MIA-OBS-01 claimed (A3, owner-approved M6 exception)

- Agent: A3. Owner approved M6 work in chat. Existing observatory code (store, baseline, snapshot capture/scheduler, report) already covers much of OBS-02/03/07; OBS-01 is limited to demonstrated gaps (see board row). A5 is not consulted yet; A5 owns code-graph/evolution paths, none touched.

## 2026-11-22T09:45Z — MIA-HOLO-04 claimed (A6 lead)

- Agent: A6. Owner said "continue" (or work another milestone); HOLO-04 chosen as the in-milestone next task. Plan: a small evidence reporter in `assistant.html` (chat stream start/delta/done/error/stop, voice listening/muted/speaking/interrupted, wake listening) published on a same-origin BroadcastChannel; `/hologram` resolves it with server evidence via `POST /api/runtime/resolve`, handles backend loss/reconnect, cancellation and offline. A4/A1 files are not edited beyond the minimal reporter hook in `assistant.html`; any A4-owned voice-state wiring is read-only here and noted for A4 review.
## 2026-11-22T09:46Z — MIA-LIFE-04 data/API half delivered, MIA-LIFE-05 claimed (A1)

- LIFE-04 (A1 half) commit `b75556d` verified on remote main. Paths: src/mia/memory/{projects,schema,store}.py, src/mia/observatory/api.py, tests/test_project_workspace.py, docs/architecture/MEMORY_AND_KNOWLEDGE.md. Delivered: project registry (goal, status), milestones, isolated workspace view over tasks/decisions/conversations, discovered-project listing, REST API, migration 8, export.
- Tests actually run: 13 new tests (7 isolation/lifecycle mutations caught); full suite 2150 passed, 1 skipped, 0 failed by progress-dot count; ruff clean; after the final rebase only the three LIFE suites were re-run. NOT done: the A6 UI half, project rename, Brain prompt context from goal/milestones, URL addressing of names containing '/'. Task stays IN_PROGRESS until the UI half is done or the owner accepts the split.
- LIFE-05 claimed (A1): configurable daily/weekly briefing from owner-approved sources only (tasks, milestones), citing record ids, timezone, freshness and missing sources; quiet-hours flag; never invents calendar events; deterministic fixtures.

## 2026-11-22T09:46Z - A7 role defined and MIA-LANG-01 claimed (A7)
- Owner approved a seventh role in chat ("Add a seventh role you define") because A1-A6 were all held by live sessions (the A4 slot was taken by another session minutes earlier, so it was not overwritten). Role: A7 multilingual language layer (docs/AI_DEVELOPMENT_SYSTEM.md, prompts/AI_AGENT_SPECIALIZATIONS.md, registry).
- Task MIA-LANG-01: gap from the MIA-M1-11 audit - local TTS has no language-aware voice choice. Scope on the board. No overlap with A4's MIA-LIFE-09 (`src/mia/life/notifications.py`); `api.py` hunks will be small and re-fetched before commit.

- A3 correction on the OBS-01 row: the suspected missing-stored-as-zero bug is not reachable (capture rejects snapshots with zero fixed-benchmark tests), so no code was added for it. The unit bug and the mutable table were real.

## 2026-11-22T11:20Z - MIA-LIFE-09 task producer DONE (A4)
- Paths: src/mia/life/task_notifications.py (new), src/mia/life/notifications.py (open_for_source, withdraw), src/mia/life/notification_api.py (producers hook), src/mia/observatory/api.py (producer wiring, 4 lines), tests/test_task_notifications.py, tests/test_notification_api.py (demo source), docs. Reads A1's PersonalData through its public list/get only; no changes to A1's files.
- Tests: 60 notification tests + regression suites passing; full suite not run. Timezone-duplication follow-up recorded in STATUS.md.

## 2026-11-22T10:05Z - MIA-LANG-01 DONE (A7)
- Commit `61be039d4a248f725308fd54928c6c82bd0eb364` on remote main (verified with `git ls-remote`; rebased over one intervening commit, no conflicts). Paths: src/mia/voice/voice_select.py (new), src/mia/observatory/api.py (speak route, status, input model; small hunks), tests/test_voice_language_selection.py (new, 14 tests), docs.
- Tests: see STATUS.md. Limits: heuristic voice matching; UI not wired; real Urdu voice and quality are OWNER_VERIFICATION_REQUIRED; Code Graph not regenerated.
- A7 now IDLE. Next: MIA-LANG-02 (UI wiring) is OPEN; it edits `assistant.html`, so A6/A4 are asked to say here if they hold that file before A7 claims it.

## 2026-11-22T09:53Z — MIA-LIFE-12 DONE (A2, owner-approved M3 exception)

- Agent: A2 (Anthropic Claude Sonnet 5.5 session). Commit `43d81cf18b4b26e00fbb6552cd9289bfbdaa0b4c` on remote main (verified with `git ls-remote`). Authorization: the owner replied "Approve an M3 exception" in chat to A2's offer that named MIA-LIFE-12.
- Paths: src/mia/observatory/usage_dashboard.py (new), src/mia/observatory/api.py (one route + life workload probe), src/mia/gateway/dashboard.html (panel only; Quality panel untouched), tests/test_usage_dashboard.py (14 new), docs/architecture/AI_GATEWAY.md, docs/STATUS.md, task board. A STATUS.md rebase conflict with a concurrent agent was resolved by keeping all sections.
- Tests actually run: new file 14 passed; five mutation checks each failed the intended test; existing quality-panel tests pass; the full suite (about 12 minutes, run detached because it now exceeds the 300 s tool limit) finished with no failures and 1 skipped (Chromium path unset) before the final rebase; after it only the dashboard and quality-panel tests plus ruff were re-run. No real browser was used.
- Note for other agents: the full suite no longer fits a single 300 s command here; run it detached with output to a file.
- A2 status: IDLE. The M3 exception covered this one task only; no other M3 task was touched.

## 2026-11-22T09:54Z — MIA-LIFE-05 DONE (A1)

- Commit `e5ea839` verified on remote main (origin/main equals local after push). Paths: src/mia/memory/briefing.py, src/mia/observatory/api.py (GET /api/briefing), tests/test_briefing.py, docs/architecture/MEMORY_AND_KNOWLEDGE.md. Delivered: deterministic model-free daily/weekly briefing from tasks, reminders, milestones and project goals; per-item citations; timezone, generation time and freshness; explicit calendar-missing note; private items hidden by default; project filter; goal follow-up; quiet-hours delivery flag.
- Tests actually run: 21 new tests with a fixed clock (10 mutations; 1 survivor found and killed by a new boundary test); full suite 2240 passed, 1 skipped, 0 failed by progress-dot count (21st test run separately); ruff clean; after the final rebase the four LIFE suites were re-run.
- Not covered: scheduler/notification delivery (LIFE-09), persisted briefing preferences, UI, Brain intent, calendar integration, model-written summaries, live evidence. A3 (co-owner) was not consulted.
- A1 now IDLE. Remaining A1-involved tasks all depend on A5/A6/A3 co-owners or other agents' work: LIFE-03 (UI + intents, A6), LIFE-06/08/10 (A5), LIFE-14, COMP-01/04, EVOL-01, OBS-08, and the A6 UI half of LIFE-04.

## 2026-11-22T10:04Z — MIA-OBS-01 DONE (A3)

- Commit 79c55dd on remote main (verified). Paths: src/mia/observatory/measurements.py (new), store.py, snapshot_capture.py, tests/test_observatory_measurements.py, docs/observatory/MEASUREMENTS.md.
- Delivered: versioned metric definitions (unit, range, sampling, source, missing semantics), enforced in `record_metric`; non-finite values rejected; unregistered metrics accepted but tagged; every sample records schema/definition version; `metric_samples` immutable by trigger; fixed the daily snapshot storing benchmark duration with unit `percent` (now `seconds`).
- Tests actually run: 6 new tests; related observatory/experiment/baseline/benchmark files 67 passed; three mutants (wrong unit, no triggers, no validation) each failed tests; full suite on the merged tree 2228 tests, 0 failed, 0 errors, 1 skipped; ruff clean.
- Limits: samples written before this change keep their old data (immutable) and carry no definition version; pre-fix duration samples have the wrong unit and should be read as seconds. Nothing here measures any model.
- Next candidates for A3: OBS-02 (needs a look at the existing baseline.py against 'refuse to overwrite' and missing-stays-missing) and OBS-05 (benchmark suite). Not claimed yet.

## 2026-11-22T10:07Z — MIA-OBS-02 claimed (A3)

- Agent: A3. Audited the existing Day 0 baseline; scope limited to the demonstrated gaps on the board row. A5 not consulted; no A5 paths touched.

## 2026-11-22T10:20Z - MIA-LANG-02 claimed (A7)
- Owner said to check other milestones and complete their tasks; LANG-02 is the highest-priority open P1 and is A7's own follow-up. Scope: "Auto (match language)" local voice option in `assistant.html` (`refreshVoices`, `fetchLocalSpeech`, `speakReply` local branch only) using `language: "auto"` and `/api/voice/status` `local_tts_languages`. A6: your HOLO-04 reporter hook is a different region; tell me here if you disagree.

## 2026-11-22T10:07Z — MIA-LIFE-11 claimed (A2)

- Agent: A2. Next A2 task after LIFE-12; M2 still has OPEN A6 tasks so M3 is only reachable through the owner's M3 exception ("Approve an M3 exception" then "continue" x2). Owner can stop this at any time. Paths: src/mia/life/backup.py, src/mia/backup_cli.py, pyproject optional extra, tests/test_life_backup.py, docs.

## 2026-11-22T12:10Z - MIA-LIFE-09 UI + voice hook DONE (A4, owner-approved exception)
- Paths: src/mia/assistant.html (new Notifications panel + settings + announceNotifications in startVoiceConversation, 1 added line there; A6 not editing assistant.html at pull time), src/mia/life/notification_api.py (voice-digest route), tests/test_voice_browser.py (4 tests), tests/test_notification_api.py (1 test), docs/STATUS.md.
- Tests: see STATUS.md; 97 UI/regression tests passing; full suite and Code Graph refresh not run. A6 may review accessibility; a real screen-reader pass is owner-only.

## 2026-11-22T10:18Z — MIA-OBS-02 DONE (A3)

- Commit 0adae09 on remote main (verified). Paths: src/mia/observatory/baseline.py, store.py (shape check), tests/test_baseline_capture.py (+7 tests), docs/observatory/MEASUREMENTS.md.
- Delivered: Day 0 manifest now records declared-dependency versions (null when not installed; explicit unavailable when pyproject is unreadable), a host CPU/memory sample taken before the test run (unavailable is explicit, never 0), and `not_captured` reasons for model inventory, latency and cost. Overwrite refusal was already enforced by immutability triggers and was left as is.
- Tests actually run: related observatory/baseline/experiment/benchmark files 76 passed; three mutants (resource as zero, null as version, no store check) each failed tests; full suite 2304 tests, 0 failed, 0 errors, 1 skipped; ruff clean.
- Limits: no real model inventory or latency/cost baseline exists at Day 0 by design; a real Day 0 capture on the owner's machine is still an owner action. Daily captures also run the new probes (about 0.5 s extra).

## 2026-11-22T10:55Z - MIA-LANG-02 DONE (A7)
- Commit `81ee534e0bddbd279f87c253665de046a2c67177` on remote main (verified with `git ls-remote`; rebased over A4's MIA-LIFE-09 `assistant.html` commit 1984ac2 with no conflicts and the affected Chromium suites re-run). Paths: src/mia/assistant.html (voice dropdown, fetchLocalSpeech, speakReply local branch), tests/test_assistant_voice_language.py (new). Evidence in STATUS.md. A7 is IDLE.
- Correction: the first push was rejected (main had advanced) and the commit was re-based, so its final SHA is 81ee53438a15f66ed0e2a146b02e61a8a9f519e1 (81ee534); an earlier version of this entry cited the pre-rebase SHA. Tests were re-run after the first rebase; after the second rebase (A3 records/baseline commits, no overlap with my files) only the new test file was re-run.

## 2026-11-22T10:20Z — MIA-LIFE-11 DONE (A2, owner-approved M3 exception)

- Agent: A2 (Anthropic Claude Sonnet 5.5 session). Commit `39060dd13240bc89f902cb028bfadf39614b04a9` on remote main (verified with `git ls-remote`). Authorization: the owner approved an M3 exception for A2 and then said "continue" twice after A2 named LIFE-11 as its next M3 task; the owner can revoke this at any time.
- Paths: src/mia/life/backup.py, src/mia/backup_cli.py, pyproject.toml (optional `backup` extra + dev, `mia-backup` script), tests/test_life_backup.py (55 new), docs/architecture/BACKUP_RESTORE.md, docs/STATUS.md, task board. A STATUS.md rebase conflict with a concurrent agent was resolved by keeping all sections.
- Tests actually run: new file 55 passed; ten mutation checks each fail the intended tests (one, the unauthenticated final-chunk flag, initially survived because truncation was only caught by a later gzip/tar layer; crypto-layer tests were added and it is now caught); full suite (detached, about 12 minutes) finished with no failures and 1 skipped (Chromium path unset) before the final rebase; after it only the backup and dashboard tests plus ruff were re-run. Real SQLite and real encryption; no real-device or Windows testing.
- Security review requested: A5 (co-owner) was not consulted. Please review key derivation parameters, the format, the exclusion list (provider keys, permission/approval stores) and the never-overwrite rule for the spend ledger before relying on backups.
- A2 status: IDLE. Next A2 M3 candidate would be MIA-LIFE-13 (device sync/remote access) which is security-heavy and should not start without A5 and an explicit owner go-ahead.

## 2026-11-22T10:29Z — MIA-OBS-05 claimed (A3)

- Agent: A3. Audit: no benchmark registry, run record or regression comparison exists; only the pass/fail foundation suite, the 31-scenario eval and the opt-in live harness. Scope is the bounded first slice on the board row; the rest of OBS-05 stays open and is stated as such.

## 2026-11-22T15:50Z — MIA-LIFE-14 claimed (A1)

- Owner instruction: "check other milestones and complete that tasks". A1 re-read the board; A1 co-owned tasks needing A5 (COMP-01/04, LIFE-06/08/10, EVOL-05/06) are left for A5 (security-critical or A5-led). LIFE-14 depends on LIFE-05 (done), LIFE-09 (partial; backend + task notifications done by A4) and LIFE-12 (done).
- Scope: stalled-work suggestions only (idle project goals, idle open tasks) with deterministic relevance, duplicate/stale/low-confidence suppression and withdrawal of pending suggestions whose evidence changed, delivered through the existing OutreachEngine policy (quiet hours, caps, emergency stop, cooldown, dismissed memory). Due/overdue notifications remain LIFE-09's. Paths: src/mia/life/suggestions.py, life settings, API, tests, docs.

## 2026-11-22T16:05Z - MIA-LIFE-14 claimed (A7)
- Re-checked the board first: A3 had claimed MIA-OBS-05 and A2 finished MIA-LIFE-11, so A7 took the next roadmap-order task instead. Audit: outreach (`outreach.py`) and due-date notifications already enforce quiet hours, daily caps, gaps, cooldowns, dismissal suppression, opt-in and emergency stop. Gap: no producer for stale unfinished tasks without a due date, no suggestion-specific opt-in/novelty rule. Scope: new `src/mia/life/suggestions.py`; small additive hunks in `notifications.py` (settings + history lookup) and the `api.py` producers list; tests. No UI changes (settings are API-only in this slice).

## 2026-11-22T16:20Z - RETRACTION: A7 did NOT claim MIA-LIFE-14; A7 claims MIA-LIFE-06
- The previous A7 entry ("MIA-LIFE-14 claimed (A7)") is WRONG and withdrawn: A1 claimed MIA-LIFE-14 at 15:50Z and still holds it. A7 made no code or board/registry change for LIFE-14 and will not. Cause: A7's claim script did not stop when its "task is OPEN" check failed. A7's notes from that audit (stale no-due-date tasks have no producer; `NotificationCenter.submit` does not block re-suggesting after a notification expires, so a novelty rule is needed) are offered to A1 here for use.
- A7 now claims MIA-LIFE-06 (board row updated; verified OPEN immediately before claiming).

## 2026-11-22T16:45Z - A7 releases MIA-LIFE-06, claims MIA-LIFE-03
- MIA-LIFE-06 released (nothing was built): its backlog dependency "M4 filesystem permission contract available" is unmet. MIA-LIFE-03 claimed instead (dependency LIFE-02 DONE; verified OPEN immediately before claiming). Files: new `src/mia/life/tasks.html`, a small `/tasks` page route in `src/mia/observatory/api.py`, new Chromium test file. `assistant.html` is not touched.

## 2026-11-22T15:55Z — MIA-HOLO-04 done (A6)

- Agent: A6. Added live event binding (assistant.html reporter -> BroadcastChannel -> /hologram). Touched A4/A1-shared `assistant.html` with add-only hooks. Fixed HOLO-01 supersession so a new turn or owner Stop replaces an old failure; ties between mutually superseding kinds fall back to priority. Full suite and ruff pass; mutation checks fail the right tests. Next A6 candidates: HOLO-05, HOLO-06.

## 2026-11-22T16:00Z — MIA-LIFE-14 DONE (A1)

- Commit `6f52281` verified on remote main (origin/main equals local after push). Paths: src/mia/life/suggestions.py, src/mia/life/store.py (3 settings, `withdrawn` outreach state), src/mia/observatory/api.py (POST /api/life/suggestions/run), tests/test_life_suggestions.py, docs/architecture/MEMORY_AND_KNOWLEDGE.md.
- Delivered: stalled-work suggestions (idle goals, idle non-private open tasks) with deterministic relevance; suppression reasons stale / low_confidence / low_relevance / duplicate reported per candidate; shared OutreachEngine policy (opt-in, emergency stop, paused, quiet hours, daily cap, gap, backlog, dismissed memory, cooldown); evidence-cited rationale; withdrawal of pending suggestions whose evidence changed; opt-out and emergency stop withdraw all; dry run by default. Due/overdue stays with LIFE-09.
- Real defect found while testing and fixed: with min_outreach_gap_minutes = 0 the engine compared the run's start time against a message stamped moments later in the same run, got a negative gap and wrongly blocked a second send; decisions after a send now use a monotonic clock.
- Tests actually run: 19 new tests (15 mutations; 1 survivor found and killed by a tighter test); full suite 2405 passed, 1 skipped, 0 failed by progress-dot count; ruff clean; after the final rebase the new, briefing, task-notification and life/outreach/ui/inbox/capture/backup suites were re-run. Not covered: scheduler (the autonomous runner does not call it), notification-center delivery, UI, persisted suppression journal, live evidence. A3 (co-owner) was not consulted.

## 2026-11-22T16:03Z — MIA-OBS-05 first slice DONE, task left PARTIAL (A3)

- Commit a8f2d6d on remote main (verified). Paths: src/mia/observatory/benchmarks.py (new), measurements.py (+benchmark_pass_rate), tests/test_observatory_benchmarks.py (10 tests), docs/observatory/BENCHMARKS.md.
- Delivered: scenario-hash-versioned suites per family, immutable run records plus pass-rate samples, `compare_runs` that refuses non-equivalent conditions and reports Wilson intervals, small-sample flag and flipped items, and an explicit `NOT_YET_VERSIONED` list.
- Tests actually run: 34 across benchmarks/measurements/eval files; four mutants (ignore version, point-estimate verdict, order-dependent hash, record empty run) each failed a test; full suite 2385 tests, 0 failed, 0 errors, 1 skipped; ruff clean.
- NOT done, so the task is not marked DONE: fixed benchmark workloads for latency, cost and resource use; a scored voice benchmark; live-model benchmarks (owner-run only). No CLI or dashboard wiring (OBS-06/07). Scripted results say nothing about live-model quality.
- A3 status: IDLE.

## 2026-11-22T16:04Z — MIA-OBS-04 claimed (A3)

- Agent: A3. OBS-03 (A2+A5) is OPEN on the board; code audit shows opt-in scheduler, resource bounds, deferral, explicit offline-day events already exist. Not touched. Pause/resume days belong to the OBS-08 lifecycle. OBS-04 slice reads existing stores only.

## 2026-11-22T18:10Z - MIA-LIFE-03 UI half DONE (A7)
- Commit `466517f` on remote main (verified with `git ls-remote`; rebased over A1's LIFE-14 commits with no conflicts; full suite re-run on the rebased tree: exit 0). Paths: src/mia/life/tasks.html (new), src/mia/life/life.html (one nav link), src/mia/observatory/api.py (`/tasks` route, 7 lines), tests/test_tasks_workspace.py (new). `assistant.html` untouched.
- Process note: the post-rebase check in the push command named a non-existent test file and errored, but the push still went ahead; the full suite was then run on the pushed tree and passed. LIFE-03 remains open for A1's Brain-action half. A7 is IDLE.

## 2026-11-22T16:19Z — MIA-OBS-04 first slice DONE, task left PARTIAL (A3)

- Commit f7f00c5 on remote main (verified). Paths: src/mia/observatory/attribution.py (new), tests/test_observatory_attribution.py (8 tests), docs/observatory/ATTRIBUTION.md. No A5 or other owners' paths touched.
- Delivered: per-window commit and capability-transition candidates joined to metric deltas; every row `causal_claim_allowed: false`; gaps, uncovered spans and unavailable sources stay visible; not-attributable and unobserved factors stated in every report.
- Tests actually run: 8 new; four mutants (causal allowed, unavailable source counted, commit ignored, always-covered) each failed tests; full suite 2465 tests, 0 failed, 0 errors, 1 skipped; ruff clean.
- NOT done / handoffs: (1) model/provider attribution needs a per-model usage breakdown in the usage ledger window summary (gateway owners); (2) dependency attribution needs `dependencies` stored in each daily snapshot (snapshot_capture, small additive change, not made here); (3) no controlled comparison exists, so no causal claim is possible; (4) OBS-03 is OPEN on the board although the scheduler, resource bounds and offline-day events exist in code; its owners (A2+A5) should confirm or close it, and pause/resume days depend on OBS-08.
- A3 status: IDLE.

## 2026-11-22T17:15Z — MIA-HOLO-05 claimed (A6)

- Agent: A6. Owner said "continue". Scope: `frontend/holo` and `/hologram` only (no shared-file edits): an always-available text/icon view equivalent to the 3D one, reduced-motion and forced-colors handling, labels/keyboard order, WCAG contrast and target-size checks, 320 px reflow. Rebuilds `hologram.html`.

## 2026-11-22T17:06Z — MIA-OBS-07 claimed (A3)

- Agent: A3. Existing `build_experiment_report` already verifies hashes and lists missing/unavailable days and events; the generator composes it with attribution and benchmark runs. A6 not consulted (no dashboard paths touched); OBS-06 stays OPEN.

## 2026-11-22T17:06Z — MIA-OBS-03 claimed (A2)

- Agent: A2. No in-gate A2 task was eligible (LIFE-13/COMP-02 blocked on the unmet identity/threat-model dependency). Claimed OBS-03 on the owner's repeated "continue" (same convention as A3's OBS-04 and A6's HOLO-05 claims). Owner can stop it at any time.

## 2026-11-22T17:30Z - MIA-COMP-01 claimed (A7)
- Verified OPEN immediately before claiming. Scope: `docs/SECURITY_THREAT_MODEL.md`, a typed capability registry `src/mia/security/capabilities.py` mapping every real side-effecting tool/route to side effects, approval class, scope, resource bounds and recovery, and a coverage test that fails when code gains an unregistered side-effecting route. No enforcement changes (that is MIA-COMP-04). Dependency gap recorded: LIFE-13 design not done, so device identity is an explicit assumption.

## 2026-11-22T13:10Z - MIA-LIFE-09 timezone unification (A4)
- Paths: src/mia/life/notifications.py (follow_owner_timezone + provider), src/mia/life/notification_api.py (field), src/mia/observatory/api.py (provider wiring, read-only on A1's settings), src/mia/assistant.html (one checkbox in the notification settings), tests (notifications, notification API, voice browser), docs/STATUS.md.
- Tests: see STATUS.md. Resolves the follow-up recorded after the task producer.

## 2026-11-22T17:50Z - A4 review: HOLO-04 voice wiring OK with streamed speech; LIFE-09 leftovers closed
- Added tests/test_voice_browser.py::test_runtime_evidence_keeps_speaking_held_across_streamed_sentences (mutation-checked). No source changes in this step; assistant.html untouched. LIFE-09 leftover "route outreach/briefings through the center" is closed by design (LIFE-14 keeps disjoint content); remaining LIFE-09 items are owner-only checks and push-to-talk announcement.
## 2026-11-22T18:05Z — MIA-HOLO-05 done (A6)

- Agent: A6. Only `frontend/holo`, `hologram.html` and a new test file touched; no shared files. Next A6: HOLO-06 (needs A5 for budgets).

## 2026-11-22T17:24Z — MIA-OBS-07 generator DONE (A3)

- Commit 69c56fa on remote main (verified). Paths: src/mia/observatory/report_generator.py (new), tests/test_observatory_report_generator.py (10 tests), docs/observatory/REPORT.md. Existing `build_experiment_report` reused unchanged.
- Delivered: deterministic report (sample sizes with small-sample flags, attribution rows, benchmark first-vs-last under equivalent conditions, rollback days, evidence-derived confounders, limitations, claims not allowed), markdown rendering with content hash, `verify_report`, read-only.
- Tests actually run: 10 new; five mutants (time in hash, hidden missing days, no small-sample flag, unavailable source counted as rollback x2 until a missing test was added) each failed a test; full suite 2477 tests, 1 failure, 0 errors, 1 skipped; ruff clean.
- The 1 failure is `tests/test_holo_live.py::test_streaming_stop_is_interrupted_and_partial_error_is_failed` (Playwright 10 s timeout, HOLO area, not touched by A3). It passed 3 of 3 times when re-run alone, so it looks load-sensitive, but that is a judgement from three runs, not proof; HOLO owners should watch it.
- NOT done: no real Day 0-30 report exists (needs the owner's run); no dashboard display (OBS-06); OBS-04/05 remain PARTIAL, so the report inherits their gaps and says so.
- A3 status: IDLE.

## 2026-11-22T18:40Z - A4: voice design note for MIA-COMP-10 (no task claimed)
- Added docs/voice/VOICE_COMPUTER_ACTIONS_DESIGN_NOTE.md: verified facts about the current voice pipeline, voice-specific hazards, proposed rules and acceptance tests for COMP-10, and owner decisions needed. Documentation only; MIA-COMP-10 stays OPEN and unclaimed, blocked on COMP-04 (broker) and COMP-09 (approval/audit/undo). A1/A6 should read it before building those. No code or tests changed.
## 2026-11-22T19:10Z — MIA-HOLO-06 claimed (A6 lead)

- Agent: A6. Owner said "continue". Scope: `frontend/holo` only. Plan: one committed budgets file (bundle bytes, draw calls, triangles, frame cost), a pure quality governor that steps frame rate/pixel ratio down under sustained cost or late frames and back up slowly, a visible note when detail is reduced, and repeatable Chromium checks plus a profile report. Real-GPU numbers cannot be measured here and will be marked unmeasured.

## 2026-11-22T17:29Z — A3 closing OBS-04 and OBS-05 partials

- Agent: A3, on the owner's instruction. Planned paths: src/mia/gateway/ledger.py (additive `models` key in `window_summary` only, so snapshots can show which models served a window), src/mia/observatory/snapshot_capture.py (store `dependencies` in each snapshot), attribution.py, benchmarks.py, report_generator.py, tests, docs. Existing consumers and tests of `window_summary` are kept working.
## 2026-11-22T20:10Z — MIA-HOLO-06 done (A6)

- Agent: A6. `frontend/holo` only, plus one HOLO-01 fix in `runtime_state.py` (exact-timestamp ties: a result now beats the activity it ends) found when a live test flaked under load. Hologram milestone tasks HOLO-01..06 are all DONE. Real-GPU measurement remains open for whoever has a GPU: run the profile report on that machine.

## 2026-11-24T06:20Z — MIA-LIFE-04 UI half claimed (A6)

- Agent: A6. Owner said "continue"; A1's data/API half is on main (b75556d) and the board lists the UI half as open for A6. Plan: a standalone `/projects` page (`src/mia/life/projects.html`, same pattern as `/tasks`) over the existing `/api/projects*` routes: project list incl. discovered-but-unregistered names, create, goal/status edit, milestones (add/edit/done/delete), strictly read-only isolated tasks/decisions/chats, confirmed delete, stale-response guard so a fast switch never shows another project's items. Shared-file edits are add-only: one `/projects` route in `api.py` and one nav link in `life.html`. No API change planned; the `/`-in-name limitation is surfaced in the UI, not fixed.

## 2026-11-24T06:07Z — MIA-LIFE-03 Brain-actions half claimed (A1)

- Verified immediately before claiming: board row is PARTIAL with the UI half DONE by A7 (commit 466517f) and the Brain half open; no agent is registered on it. Scope: deterministic English task intents (add, list, complete, set priority, delete) through a new optional `task_actions` hook beside the existing clarification short-circuit in src/mia/brain/orchestrator.py; all changes go through memory.personal (the store the /tasks page and API use); every delete and every bulk change needs an explicit confirmation; temporary chats are read-only. Paths: src/mia/brain/task_actions.py, small additive orchestrator and api.py wiring, tests, docs.

## 2026-11-24T06:10Z - MIA-COMP-01 first slice pushed (A7); MIA-COMP-04 claimed
- Commit 3162dd1 on main (remote SHA verified equal to local HEAD after two rebases; the first two push attempts were refused: a git-proxy 403 until the repo was attached to the session, then two non-fast-forward races with other agents).
- Added `docs/SECURITY_THREAT_MODEL.md`, `src/mia/security/local_guard.py` (on for the production `app`, off by default in `create_app`), `src/mia/security/capabilities.py`, `tests/test_local_guard.py`, `tests/test_route_capabilities.py`.
- Run: full suite exit 0 and ruff clean before the rebase; after the final rebase only ruff and the two new test files were re-run (pass). The full suite was not re-run on the final tree.
- Not verified: guard behaviour in a real browser against a hostile page; handler-level proof of each route classification; device identity (LIFE-13).
- MIA-COMP-04 claimed by A7 after confirming it was OPEN on the freshly rebased board.

## 2026-11-24T08:30Z - MIA-COMP-09 claimed (A4, owner-approved exception)
- Owner approved A4 taking MIA-COMP-09 by name. A1/A6 not consulted (A1 idle, A6 on LIFE-04 UI). Reproduced defect first: a failed `/api/approvals/{id}/execute` (even a payload mismatch) leaves the approval stuck in `approved`, so the owner can neither retry nor reject until it expires, and nothing is audited. Scope: fix recovery, audit failures, duplicate-submit and expiry tests, then recoverable pending approvals after a reload and an exact-action review card if time allows. Touches computer/approvals.py, the execute/reject routes in observatory/api.py, and the approval card in assistant.html (A6 is on LIFE-04 UI in the same file: edits will be minimal and re-fetched). Enforcement stays COMP-04.

## 2026-11-24T10:10Z - MIA-COMP-09 slice 1 DONE (A4): approval recovery and failure audit
- Paths: src/mia/computer/approvals.py (release, clearer state messages), src/mia/observatory/api.py (/api/approvals/{id}/execute failure handling only), tests/test_approval_recovery.py, docs/STATUS.md. assistant.html untouched in this slice.
- Tests: see STATUS.md. Task stays IN_PROGRESS: recoverable pending cards, exact-action review and undo surface remain.

## 2026-11-24T12:30Z - MIA-COMP-09 slice 2 (A4): restorable approval cards
- Paths: src/mia/computer/approvals.py (card_json column with migration, save_card/pending_cards/expires_at), src/mia/observatory/api.py (register_tool card hook, GET /api/computer/approvals/cards), src/mia/assistant.html (renderApprovalCards effect/expiry lines, restorePendingApprovals; A6's LIFE-04 UI edits merged cleanly at pull), tests/test_approval_cards.py, tests/test_voice_browser.py (3 tests). Task stays IN_PROGRESS.
- Tests: see STATUS.md.

## 2026-11-24T06:30Z - MIA-COMP-04 slice 1 (A7), commit 354a752
- Finding: Mia already enforces approvals per adapter (`PermissionPolicy.authorize`, `ApprovalStore.consume`), so no duplicate broker was written. Added `tests/test_broker_enforcement.py` (real app, real filesystem adapter and SQLite, only the clock mocked in the expiry test). Mutation check: forcing `_computer_permission_requires_approval` to return False made 8 of its tests fail; source restored.
- Correction to my COMP-01 inventory: browser fill/select/check/scroll/tabs/screenshot are grant-gated (409 without an active owner grant), not preview/execute. Added approval class `grant_gated`; `browser/navigate` is now classed external-network (low-risk fetch), `browser/close` a safety control.
- Known gap (not fixed): the approve endpoint is plain HTTP, so any local process can self-approve. The Host/Origin guard stops browsers only; per-caller authentication is COMP-02/LIFE-13.
- Pre-existing failure, not mine: `tests/test_life_web.py::test_quiet_hours_block_background_cycles_but_not_owner_requests` fails on clean main at 06:19 UTC (it assumes the current time is outside the default quiet hours). A1 owns that area; left untouched.
- Run: new tests pass; full suite has that one clock-dependent failure; ruff clean.

## 2026-11-24T06:19Z — MIA-OBS-03 DONE (A2)

- Agent: A2 (Anthropic Claude Sonnet 5.5 session). Commit `16f92b3d4f090dafb1d852f0c01159eef4149de7` on remote main (verified with `git ls-remote`). Claimed on the owner's repeated "continue" (same convention as A3's OBS-04 / A6's HOLO-05 claims); A5 not consulted.
- Paths: src/mia/observatory/snapshot_scheduler.py, store.py (additive `find_events`, one additive report-accounting block for paused days), api.py (two POST routes + 409 mapping), tests/test_experiment_scheduler_days.py (23 new), docs/architecture/EVOLUTION_OBSERVATORY.md, docs/STATUS.md, task board. A STATUS.md rebase conflict with a concurrent agent was resolved by keeping all sections. A3's attribution/baseline code was not touched.
- Tests actually run: new file 23 passed; eight mutation checks each fail the intended tests; existing experiment scheduler/protocol/activity tests pass; ruff clean. Full suite (detached): everything passed except `tests/test_holo_live.py::test_streaming_*`, an intermittent Playwright test from A6's HOLO-04/05 work. It reproduces on a clean origin/main checkout (4 of 5 alternating rounds failed there, 0 of 5 on my tree; it also failed intermittently on my tree earlier), so it is flaky independent of this change. The full suite was not re-run after the final rebase; only the scheduler and dashboard tests plus ruff were.
- Request to A6: `test_holo_live.py` streaming tests time out intermittently (Playwright `wait_for_function`, 10 s); please check for a race or raise the wait before relying on it as a gate.
- A2 status: IDLE. Remaining A2 items are LIFE-13/COMP-02 (blocked on the unmet identity/threat-model dependency) and EVOL-08/09 (need A3 and a go/no-go).
## 2026-11-24T06:45Z — MIA-LIFE-04 UI half done (A6)

- Agent: A6. New: `src/mia/life/projects.html`, `tests/test_projects_workspace_page.py`. Shared files, add-only: one `/projects` route in `api.py`, one nav link in `life.html`. `pyproject.toml` package-data now also ships `tasks.html` (A7's page was missing from installed packages) and `projects.html`. No API change.
- FYI for the owner/A1: `tests/test_life_web.py::test_quiet_hours_block_background_cycles_but_not_owner_requests` fails on clean main whenever the wall clock is inside the default quiet hours (22:00-07:00 UTC): it asserts quiet hours are inactive at now()+1 min. Time-of-day dependent, not caused by this change; not touched.

## 2026-11-24T06:25Z — MIA-LIFE-03 Brain-actions half DONE (A1)

- Commit `5424535` verified on remote main (origin/main equals local after push). Paths: src/mia/brain/task_actions.py (new), src/mia/brain/orchestrator.py (optional `task_actions` hook, `_direct_task`, `task_action` in the result and stream done payload), src/mia/observatory/api.py (one wiring line + import), tests/test_task_actions.py, docs/architecture/AI_BRAIN.md.
- Delivered: explicit English task commands (add, list, complete, set priority, delete, bulk delete/complete) answered by the Brain with no model call, acting on the same `memory.personal` store as the task page and API; every delete and every multi-task change needs an explicit "yes" (any other message cancels, 10-minute expiry, memory-only, per conversation, only the ids shown and still matching are touched, > 200 refused); private tasks invisible to chat; temporary chats read-only; credential-like text refused without echo; audit actor `brain`.
- Tests actually run: 41 new tests (19 + 3 mutations of the task-action and orchestrator-hook logic; 3 survivors found and killed with new tests); brain, task and personal suites re-run after the final rebase. Full suite before the final rebase: 2585 passed, 1 skipped, 1 failed. The one failure, tests/test_life_web.py::test_quiet_hours_block_background_cycles_but_not_owner_requests, is NOT from this change: it fails identically on a clean checkout of origin/main because it depends on the time of day (it fails whenever the real clock is inside the default 22:00-07:00 UTC quiet window; checked at 06:23 UTC). Its owner should pin the clock or the window.
- Not covered: Roman Urdu/Urdu and other non-English commands (they fall through to the model), reminders and notes from chat, rename or due-date edits, undo, a browser check of the chat surface, owner-only accessibility checks, live-model evidence.

## 2026-11-24T06:27Z — MIA-OBS-04 and MIA-OBS-05 closed (A3)

- Commits on remote main (verified): fdb81e3 (OBS-04 closure), 36b2b06 (OBS-05 closure), c240a68 (board and registry). Paths: src/mia/gateway/ledger.py (additive `models` key in `window_summary` only; existing ledger and usage tests unchanged and passing), src/mia/observatory/ snapshot_capture, attribution, benchmarks, measurements and report_generator, tests, docs/observatory/ATTRIBUTION.md and BENCHMARKS.md.
- OBS-04: usage windows list the models that served requests; each daily snapshot stores `dependencies`; attribution adds `models_used` and `dependency_versions` candidates; a window lacking evidence on either side is listed as unknown, never unchanged. Still correlation-only: there is no controlled comparison, by design.
- OBS-05: voice and cost-accounting suites from fixed deterministic test files (skips never counted as passes; a crash or empty run records nothing), and a repeated timing/Python-heap benchmark compared only within one environment with a 25% noise tolerance. Live-model quality stays owner-run.
- Tests actually run: eight mutants over the two pieces; three exposed missing tests (snapshot dependencies, skipped tests, unavailable source) that were then added and re-checked; ruff clean. Full suite 2571 tests, 1 failure, 0 errors, 1 skipped.
- The 1 failure is tests/test_life_web.py::test_quiet_hours_block_background_cycles_but_not_owner_requests (LIFE area, not touched by A3). It failed every time when run alone at 06:26 UTC on this host and asserts quiet hours are inactive at the real current time plus one minute, so it appears to depend on the wall clock (probably a night-time quiet window; the exact default window was not confirmed). LIFE owners should inject a fixed daytime clock. Not changed by A3.
- A3 status: IDLE.

## 2026-11-24T07:10Z — MIA-OBS-06 claimed (A6 lead)

- Agent: A6. Owner said "continue". Plan: read-only `observatory/trend_view.py` that composes the existing integrity-checked report, OBS-07 `generate_report` and the stored metric series into a display model (per-day state: captured / missed / explained-unavailable / failed or deferred capture / not yet reached; series with gaps kept as gaps; Day 0 vs latest only where both exist; change markers from attribution windows; every point linked to its snapshot manifest hash), one add-only route `GET /api/experiments/{id}/trends`, and a standalone page `observatory/trends.html` (no charting library; each chart has a table twin). Writes nothing; shows nothing when there is no evidence; integrity failures are shown, not hidden. The existing 816-line `dashboard.html` is not rewritten (one link only).

## 2026-11-24T06:31Z — MIA-OBS-06 claimed (A3)

- Agent: A3. Additive only: existing experiment controls, report panel and routes are not changed; the new panel and GET routes sit beside them. A6 idle and not consulted; no A6 paths in flight.

## 2026-11-24T06:31Z — MIA-OBS-08 claimed (A2, owner-approved M6 exception)

- Agent: A2. Owner: "you can work on Milestone 6". Taking the backend lifecycle slice of OBS-08; A1/A6 may take the UI (dashboard) half. I will not touch A3's baseline/attribution code.

## 2026-11-24T06:50Z - MIA-COMP-04 slice 2 pushed (73abe6b); MIA-LIFE-08 claimed (A7)
- COMP-04 slice 2: `tests/test_broker_terminal.py` (6 tests; mutation of the terminal approval consume made 4 fail; source restored). Remaining COMP-04 adapters: process terminate, app launch, desktop, browser click/actions, background tasks, Brain, voice.
- Claimed LIFE-08 after verifying OPEN on the freshly rebased board; P3 outranks P4 and the owner said to work on other milestones.

## 2026-11-24T07:20Z - MIA-LIFE-08 backend pushed (A7), commit 4510573
- Reuses `TaskEngine` (own DB `mia-workflows.sqlite3`); 4 internal handlers; 14+ tests in `tests/test_workflows.py` incl. plan-time refusal of 8 external/destructive capability names, restart/interrupted-step, timeout, stop-all. Mutation check: removing the stop-all `control` link failed the stop-all test; restored.
- Regression I caused and fixed before pushing: using `include_router` made `app.routes` contain wrapper objects (this FastAPI version), which broke `tests/test_life_ui.py::test_every_endpoint_the_page_calls_exists` (A6) and would have hidden routes from route enumeration. Routes are now registered directly on `app`. The COMP-01 coverage test also now descends into included routers so such routes cannot be skipped silently.
- Run: full suite exit 1 with exactly one failure, the pre-existing clock-dependent `tests/test_life_web.py` quiet-hours test; ruff clean.
- A7 is IDLE. COMP-04 stays PARTIAL (remaining adapters listed on its board row).

## 2026-11-24T11:49Z — MIA-EVOL-01 claimed (A1)

- Owner instruction: "start EVOL-01". Audit before claiming: real persisted signals are failed agent tasks (TaskStore, status failed + last_error) and corrections (structured records with origin corrects); there is NO persisted evaluation store (evaluations must be submitted explicitly) and a goal alone is not evidence of a gap (only an explicit owner request for a capability is). No consent model existed. A5's active EVOL-02 covers the generation/validation pipeline and is not overlapped.
- Scope: new standalone gap store (own sqlite file) with per-source owner consent (off by default), minimum-evidence thresholds, deterministic confidence and impact, duplicate links, revocation that deletes evidence, no model or inference from code volume. Paths: src/mia/evolution/gaps.py, small API block, tests, docs.

## 2026-11-24T12:00Z - A7 resumes; MIA-LIFE-07 claimed
- Re-read prompts/AI_MASTER_AGENT.md and the registry/board on freshly synced main (1c2cfa6). Other agents' heartbeats are old (A2/A3 06:31Z, A5 2026-11-21) but heartbeats are informational and A3 committed at ~11:00Z, so no claim was reassigned. A5's EVOL-02 claim has a 3-day-old heartbeat; flagged for the owner/A5, not taken.
- Blocked, exact reasons: LIFE-06 and LIFE-10 wait on COMP-04/COMP-06 and LIFE-06/07; COMP-05/06/08 list COMP-04 as a dependency; LIFE-13 needs COMP-02 style authentication.

## 2026-11-24T11:52Z — MIA-OBS-08 backend slice DONE (A2, owner-approved M6 exception)

- Agent: A2 (Anthropic Claude Sonnet 5.5 session). Commit `8edb6c09b789e8bbe78eee107945b66206da711a` on remote main (verified with `git ls-remote`). Authorization: owner said "you can work on Milestone 6". A1/A6 not consulted.
- Paths: src/mia/observatory/experiment_export.py (new), store.py (`stop_experiment`, stopped-run day accounting), api.py (stop, export, privacy notice, start body), tests/test_experiment_lifecycle.py (29 new), tests/test_life_web.py (one-test fix), docs/architecture/EVOLUTION_OBSERVATORY.md, docs/STATUS.md, task board. A real api.py import conflict with A3's OBS-06 work and a STATUS.md conflict were resolved by keeping both sides; route table checked for duplicates (none).
- Tests actually run: new file 29 passed; eleven mutation checks fail the intended tests (two survived at first because the outer hash masked the per-item checks; a baseline-link cross-check and rehash tests were added). Full suite before the final rebases: everything passed except `tests/test_life_web.py::test_quiet_hours_block_background_cycles_but_not_owner_requests`, a pre-existing time-of-day bug (it used the real clock plus one minute as an instant "outside quiet hours" and failed whenever the suite ran between 22:00 and 07:00 UTC; it failed 3 of 3 at 06:48 UTC). Fixed in four lines with a clock-independent instant. After the rebases only the experiment, observatory and life_web test files plus ruff were re-run; the full suite was not.
- For A6: dashboard controls for stop, export and the privacy notice are not built; the endpoints above are ready. For A5: please review the privacy notice wording (`FIELD_DISCLOSURE`) and the checksum-not-signature limit before this is shown to the owner as a guarantee.
- A2 status: IDLE.

## 2026-11-24T15:10Z - MIA-COMP-09 slice 3 (A4): undo for approved file writes
- Paths: src/mia/computer/filesystem.py (FileUndoConflict, undo_write, recovery-copy check; additive), src/mia/observatory/api.py (FileWriteUndo model, POST /api/computer/files/write/undo), src/mia/assistant.html (Undo button in the execute handler), tests/test_write_undo.py, tests/test_voice_browser.py (1 test). Task stays IN_PROGRESS (partial).
- Tests: see STATUS.md. Note for A5 (filesystem owner) and A1/A5 (COMP-04): the undo route should be routed through the broker once it exists.

## 2026-11-24T11:55Z — MIA-OBS-06 DONE (A3)

- Commit f7411ed on remote main (verified). Paths: src/mia/observatory/{store.py (two read getters), attribution.py (sample ids in rows), api.py (three GET routes), dashboard.html}, tests/test_observatory_evidence_api.py (5), tests/test_observatory_evidence_dashboard.py (6, real Chromium + real uvicorn), docs/observatory/DASHBOARD.md. Existing experiment controls and report panel unchanged.
- Delivered: read-only Evidence panel (coverage with missing/offline days, per-metric trends drawn only from recorded samples and dashed across evidence gaps, candidate changes, benchmark/timing runs, confounders, limitations, version metadata) with drill-down to the raw stored event and metric records; honest empty states; stored text inserted as text only.
- Pre-existing bug found and fixed: `$` was never defined in observatory/dashboard.html, so a top-level `$('autonomyResume').addEventListener(...)` threw and the rest of the page script, including every startup call (experiment panel, scheduler status, tasks, capabilities, system resources), never ran. `$` is now an alias of `el`; a regression test asserts no page error and automatic load. This also means the existing experiment panel was not auto-loading before this commit.
- Tests actually run: 11 new; four mutants (no `$`, no dashed gaps, innerHTML, wrong sample id) each failed tests; full suite 2720 tests, 1 failure, 0 errors, 1 skipped; ruff clean.
- The 1 failure is again `tests/test_life_web.py::test_quiet_hours_block_background_cycles_but_not_owner_requests` (LIFE, not A3's). Correction of an earlier note: it fails when run during the 22:00-07:00 UTC default quiet window (the full run started about 06:36 UTC and failed; alone at 06:26 UTC it failed) and passes when run alone at 11:54 UTC. So it is wall-clock dependent; LIFE owners should inject a fixed daytime clock.
- Not done: no human has looked at the page, so visual quality is unreviewed. OBS-08 (lifecycle controls, privacy notice, export) not started: it acts on the owner's real experiment.
- A3 status: IDLE.

## 2026-11-24T11:55Z — MIA-LIFE-04 residual claimed (A2)

- Agent: A2 (same registered session; owner's "Resume All Open Tasks" prompt re-read prompts/AI_MASTER_AGENT.md). Registry check at 2026-11-24T11:55Z: A1 EVOL-01 (hb 11:49Z), A3/A6 OBS-06 (hb 06:31Z/07:10Z, recent A3 commit 06:40Z), A4 COMP-09 (08:30Z), A5 EVOL-02 (hb 2026-11-21T20:33Z, no commits since; STALE but not reassigned: EVOL-02 depends on EVOL-01 which A1 is doing, so it is not eligible anyway), A7 LIFE-07 (live).
- Eligibility audit: LIFE-10 not eligible (needs LIFE-06 OPEN/blocked on M4 file permissions and LIFE-07 in progress); LIFE-13 needs the device model that COMP-02 defines; COMP-02 is M4 while M3 tasks are OPEN. Earliest-milestone dependency-ready work is the LIFE-04 residual (A1's half). Paths: src/mia/memory/projects.py, brain context, projects API/page, tests, docs.

## 2026-11-24T11:57Z — A3 resumed on the owner's "Resume All Open Tasks" prompt; MIA-OBS-08 dashboard half claimed

- Re-read prompts/AI_MASTER_AGENT.md on synced main. Registry check: A1 EVOL-01, A2 LIFE-04 residual, A4 COMP-09, A7 LIFE-07 have fresh heartbeats; A5 (EVOL-02) last heartbeat 2026-11-21T20:33Z with no evolution-path commits since 2026-11-20 and A6 (OBS-06) 07:10Z. Neither was reassigned: stale heartbeat is not proof of abandonment, and EVOL-02 sits under an owner exception granted to A5.
- Overlap note for A6: MIA-OBS-06 was completed by A3 (commit f7411ed, read-only Evidence panel with drill-down). A6's planned `trend_view.py` / `trends.html` is complementary (per-day state model, standalone page) and can proceed as an additive extension; it must not re-implement `GET /api/experiments/{id}/evidence` or the record routes.
- Eligibility survey (dependencies, not specialization): M3 LIFE-06/10 blocked (need COMP-01/04 and LIFE-06/07); LIFE-13 has no defined device-identity model and belongs to A2+A5; all unclaimed M4 tasks (COMP-02/03/05/06/07/08/10) depend on COMP-01/COMP-04, both PARTIAL under A7; COMP-11 needs COMP-02..10; EVOL-03..09 are M5 behind that and have no owner exception. The only eligible unclaimed work is the OBS-08 dashboard half, which the owner's M6 approval covers.
- Scope: lifecycle controls in `src/mia/observatory/dashboard.html` built on A2's backend (privacy notice display with acknowledgement sent on Start, pause/resume automatic capture, two-step Stop, Export with failure shown), tests in real Chromium. Nothing is started, stopped or exported on the owner's real experiment by this work.

## 2026-11-24T12:20Z - MIA-LIFE-07 backend pushed (A7), commit 9668d84
- New `src/mia/life/research.py` (+ `/api/research/*` routes, `create_app(research=...)`), `tests/test_research.py` (22 tests incl. real HTTP app, real life-store allowlist, `HttpxFetcher` over `httpx.MockTransport`), COMP-01 inventory rules for the new routes.
- Mutation checks: removing the verbatim-quote check failed 2 tests; removing the injection-overlap refusal failed 1; source restored.
- Run: full suite exit 0 and ruff clean on the tree before the final rebase; after it only the research/route/workflow/life-ui tests were re-run (pass). No live-internet fetch was run: the real network path was exercised only through a mock transport. A live check against a real allowlisted site is OWNER_VERIFICATION_REQUIRED.
- Observation: `test_life_web.py` quiet-hours test passed at 12:03Z, confirming the earlier failure was clock-dependent (it fails during default quiet hours). Still worth fixing by A1 with a pinned clock.
- A7 resumes MIA-COMP-04 (remaining adapters: process terminate, app launch, desktop, browser click/actions, background tasks, Brain, voice).
## 2026-11-24T11:30Z — MIA-OBS-06 additive extension: per-day trends page (A6)

- Agent: A6. A3 completed OBS-06 (f7411ed, Evidence panel) while A6's claim was open; per A2's overlap note this is an additive extension and does not re-implement `GET /api/experiments/{id}/evidence` or the record routes. The board row stays DONE (A3).
- Added: `observatory/trend_view.py` (read-only display model), `GET /api/experiments/{id}/trends`, standalone `/trends` page (`observatory/trends.html`), one link in `dashboard.html`'s header, `trends.html` in package data. 16 model tests and 22 Chromium tests; 8 mutations caught (two escaped at first and led to stronger tests).
- What it adds over the Evidence panel: each day is shown in words as captured, missed, paused, capture failed, deferred, unavailable, due today or not yet reached (future days are never called missed); metric charts keep gaps as gaps and draw one dot per real sample; before/after only where two days and one unit were recorded; every point links to its snapshot hash; integrity failures replace the picture instead of partly drawing it.
- Observation for A3: `build_experiment_report` lists days that have not happened yet as missing/unaccounted while a run is still `running`; the trends page corrects this at display time (needs the clock), the stored report does not.
- Limits: Chromium only; no real Day 0-30 data exists, so the page has been exercised on scripted real-store fixtures, not a real 30-day run.

## 2026-11-24T12:16Z — MIA-OBS-08 dashboard half DONE (A3); M6 software complete

- Commit 6cd66e7 on remote main (verified). Paths: src/mia/observatory/dashboard.html (additive lifecycle block and JS), tests/test_observatory_lifecycle_dashboard.py (10, real Chromium + real uvicorn, temporary databases only), docs/observatory/DASHBOARD.md. No backend files touched; A2's routes used as-is.
- Bug caught by the new tests and fixed in the same change: an inline `display:block` overrode the `hidden` attribute so the acknowledgement row showed with no experiment.
- Tests actually run: 10 new; six mutants (no acknowledgement gate, version not sent, no stop confirmation, export ignores refusal, pause enabled when capture is off, notice as HTML) each failed tests; full suite 2804 tests, 0 failed, 0 errors, 1 skipped (the LIFE quiet-hours test passes: the run began 12:01 UTC, outside the 22:00-07:00 window); ruff clean.
- OWNER-REQUIRED (not claimed): run the real 30-day experiment, press Start/Stop/Export on your own machine, look at the page in a browser, decide whether to require acknowledgement server-side.
- A3 status: IDLE.

## 2026-11-24T12:17Z — MIA-LIFE-07 UI half claimed (A6)

- Agent: A6. Owner's "Resume All Open Tasks" prompt; master prompt re-read. Registry check: A1 EVOL-01, A2 LIFE-04 residual, A4 COMP-09, A7 COMP-04 have recent heartbeats; A5 (EVOL-02) last heartbeat 2026-11-21T20:33Z, not reassigned by me (A5 is a different specialisation). Nobody owns the research UI. Plan: standalone `/research` page over A7's `/api/research/*`; no API change. Next after it: workflow UI half of LIFE-08 (no owner for a UI), then COMP-10/07 UI halves when their dependencies move.

## 2026-11-24T13:20Z - MIA-COMP-09 DONE except owner-only checks (A4); full-suite verification
- Paths this step: test isolation in tests/test_notification_api.py, test_task_notifications.py, test_approval_recovery.py, test_write_undo.py, test_voice_language_latency.py, test_approval_cards.py (per-test MIA_DATA_DIR); docs. No source changes.
- Verified: full suite in four parallel groups (details and the three environment/load findings in STATUS.md). A6/A2 may want `test_holo_budget.py` and `test_projects_workspace_page.py` to be less load-sensitive; not changed by A4.

## 2026-11-24T12:20Z - A4 resume check (owner's "Resume All Open Tasks" prompt): no eligible task; heartbeat corrected
- Re-read prompts/AI_MASTER_AGENT.md and re-checked board/registry on synced main (d558a64). A4 registry heartbeat corrected to the real UTC time (an earlier entry carried a mistyped future time). No A4 task is eligible: MIA-COMP-10 needs MIA-COMP-04 (PARTIAL, A7 active) and the M4 gate is closed without a named owner exception; LIFE-06/10/13 and COMP-02/03/05-08/11 have unmet dependencies; EVOL-01 is A1's; EVOL-03..09 are gated and not A4's.
- Possible abandoned claim for the owner to confirm: MIA-EVOL-02 (A5). Its owner exception names A5, A5's registry heartbeat is 2026-11-21T20:33Z, and no A5 commit exists after e786b19 (2026-11-22T01:33Z). A4 has NOT reassigned it: it is a security-sensitive M6 task, the exception was granted to A5, EVOL-01 (its stated dependency) is not done, and liveness cannot be proven from heartbeats alone.

## 2026-11-24T13:10Z - MIA-COMP-04 slice 3 (A7), commit d6e1573
- New `tests/test_broker_apps_processes.py` (app launch with a recording subprocess stand-in; process terminate against a real throwaway child) and 2 stop-all tests in `tests/test_broker_enforcement.py`.
- BUG FOUND AND FIXED: with an active owner grant, `POST /api/computer/apps/launch/execute` and `POST /api/computer/files/write/execute` still succeeded after `stop-all` (the launcher and filesystem adapter had no link to `AutomationControl`). Both now call `assert_active()` before consuming approvals; `create_app` links the shared control; the write route no longer turns the stop into a 400 (AutomationStopped is an OSError subclass), so both return the app-wide 423.
- Run: full suite exit 0 and ruff clean before the final rebase; after it the broker, route-capability, application and process test files were re-run (pass). The first push command timed out in the shell after the commit had already reached remote (verified with merge-base --is-ancestor); no duplicate commit.
- Not covered: desktop control and browser click/actions over HTTP, background tasks, Brain, voice. OWNER_VERIFICATION_REQUIRED: real desktop app launch and real stop-all drill on the owner's machine.
## 2026-11-24T12:59Z — MIA-LIFE-07 UI half done (A6)

- Agent: A6. New: `src/mia/life/research.html`, `tests/test_research_page.py` (28 tests, real service + store, scripted fetcher, no network). Shared files, add-only: `/research` route in `api.py`, nav link in `life.html`, package-data in `pyproject.toml`. No API change.
- Verified: ruff clean; full suite ran to completion in the background (about 14 min) with no failure.
- Limits: never run against a real site (owner-required check: allow one real host on the Autonomous life page and read one page); Chromium only; no real screen reader.
- Next claim: MIA-LIFE-08 workflow UI (no one owns a UI).

## 2026-11-24T13:00Z — MIA-LIFE-08 workflow UI claimed (A6)

- Agent: A6. Owner prompt "Resume All Open Tasks". A7's backend is on main (4510573); the board lists no UI owner. Plan: standalone `/workflows` page over `/api/workflows` (compose with preview, run, pause, cancel, retry-step, events, report); no API change; external/destructive steps stay refused as the backend already does.

## 2026-11-24T17:30Z — MIA-LIFE-08 workflow UI DONE (A6)

- Commit c0311f2. New `/workflows` page over A7's `/api/workflows`; no API change. Add-only: route in `api.py`, nav link in `life.html`, package-data entry.
- Verified: `tests/test_workflows_page.py` (17 Chromium tests, real engine and stores), mutation checks (innerHTML, stale guard, cancel confirmation, step cap, empty-plan guard, approval tag all caught; the run-button busy guard is an equivalent mutant because the button is also disabled), full suite passed, ruff clean.
- Owner-required: real screen reader pass; browsers other than Chromium. Retry only re-arms a step; the owner then runs the workflow (engine behaviour, shown in the page text).
- Next: no eligible task left for A6 that is unowned (COMP-10/COMP-07 UI halves wait on COMP-09/04/05).

## 2026-11-25T05:31Z - MIA-COMP-10 claimed (A4, owner-approved exception)
- Owner approved A4 taking MIA-COMP-10 by name. Plan (from the voice design note): structured spoken read-back generated server-side from the pending action (not model prose); voice turns never approve (approval stays an on-screen action; a spoken "yes" is just a turn); spoken stop / Interrupt / Esc / ending the conversation rejects the approvals created by that voice turn; uncertain local transcripts are not auto-sent. Touches src/mia/observatory/api.py (_remember_card: one field), a new src/mia/voice/action_readback.py, src/mia/assistant.html (voice flow, A6 is on LIFE-08 workflow UI in the same file: minimal edits, re-fetch before commit). A7 owns COMP-04 broker work: nothing here changes permission checks.

## 2026-11-25T05:32Z — MIA-EVOL-08 claimed (A3, owner exception)

- Agent: A3. Owner exception for EVOL-08 and EVOL-09 given in chat. Deliverable: docs/evolution/FOUNDATION_MODEL_FEASIBILITY.md with a go/no-go per option, grounded in repo evidence (data Mia actually has, consent rules, evaluation strength) and dated external sources. No training, downloads of models, or spending. Hardware and budget facts only the owner has are listed as owner-required inputs, not assumed. A1 (EVOL-01) and A5 (EVOL-02) paths are not touched.

## 2026-11-25T05:33Z — MIA-EVOL-01 DONE (A1)

- Commit `02c96d1` verified on remote main (origin/main equals local after push). Paths: src/mia/evolution/gaps.py (new, own sqlite file), src/mia/observatory/api.py (/api/evolution/gaps routes), src/mia/security/capabilities.py (one rule classifying them), tests/test_capability_gaps.py, docs/architecture/CAPABILITY_GAPS.md.
- Delivered: consent-gated (off by default, per source) gap discovery from failed tasks, memory corrections, submitted evaluation failures and explicit owner requests; minimum evidence per signature (2/3/2) inside 90 days, below-threshold signatures reported not created; deterministic confidence and impact; cited, credential-scrubbed evidence (corrections keep no text); duplicate links; revoke deletes evidence and orphan gaps; dry-run scan by default; status workflow and event log. No model, no inference from code volume.
- Audit finding recorded for others: Mia has NO persisted evaluation store, so evaluation evidence only exists when submitted; a goal alone is not evidence of a gap.
- Defect of mine caught by the suite and fixed: my new mutating routes were unclassified and failed tests/test_route_capabilities.py (two tests); a classification rule was added (not a test change).
- Tests actually run: 19 new tests (21 mutations; 1 survivor found and killed). Last full run before the final rebase: 2761 passed, 1 skipped, 1 failed. The failure, tests/test_projects_workspace_page.py::test_deleting_a_project_keeps_its_tasks_decisions_and_chats_and_says_so (A6's real-browser /projects page test: #notice reads 'Showing project' instead of 'kept' after a delete), is NOT from this change: it fails identically on a clean checkout of origin/main (verified). It needs its owner (A6). Also known: tests/test_life_web.py::test_quiet_hours_block_background_cycles_but_not_owner_requests fails inside the 22:00-07:00 UTC window (time-dependent). After the final rebase only the gap, route-capability, personal, task-action and suggestion suites were re-run.
- Not covered: UI, scheduler, hand-off to the evolution pipeline (A5/A3), link from a gap to a capability candidate, backup of mia-gaps.sqlite3, live evidence. A3 (co-owner) was not consulted.

## 2026-11-25T05:41Z - MIA-COMP-10 DONE except owner-only checks (A4, owner-approved exception)
- Paths: src/mia/voice/action_readback.py (new), src/mia/observatory/api.py (readback on pending actions and restored cards; 3 lines), src/mia/assistant.html (read-back lines on cards, voice announcement, cancel-on-stop/interrupt/end, doubtful-transcript gate, in-word period fix in splitSpeechChunks; A6 is in the same file for LIFE-08: rebased cleanly), tests/test_action_readback.py, tests/test_voice_browser.py (7 tests + harness confidence argument), docs/STATUS.md. No permission/broker code changed (A7's COMP-04 area untouched).
- Tests: see STATUS.md.

## 2026-11-25T05:46Z — MIA-LIFE-10 claimed (A1)

- Owner instruction: "check open and continue development". Board re-read: A1-involved open tasks are LIFE-06 (blocked on the M4 filesystem contract), LIFE-10, EVOL-05/06 (blocked on A5's EVOL-03/04), COMP-01/04 (A7 active) and LIFE-08 residual. LIFE-10 depends on LIFE-04 (done), LIFE-07 (backend done) and LIFE-06 (OPEN). Proceeding on the owner's instruction with file nodes explicitly out of scope; the graph covers people, topics, projects, tasks, decisions, records and research sources/claims.
- Audit: memory_relationships links structured records only (no provenance, confidence, confirmed/inferred or cross-store nodes); research data lives in a separate sqlite (research_sources, research_claims). Scope: new memory-DB tables (migration 9), deterministic inference only from owner-created entities, project isolation, integrity check, API. Paths: src/mia/memory/graph.py, schema, store, api, security rule, tests, docs.

## 2026-11-25T05:54Z — MIA-EVOL-08 and MIA-EVOL-09 DONE (A3)

- Commit 2c5c743 on remote main (verified). Paths (new files only): docs/evolution/FOUNDATION_MODEL_FEASIBILITY.md, docs/evolution/SMALL_MODEL_ADAPTATION.md, docs/evolution/adaptation_manifest.example.json, src/mia/evolution/adaptation_gates.py, tests/test_adaptation_gates.py. Nothing under A1's EVOL-01 or A5's EVOL-02 paths was changed.
- EVOL-08: option-by-option go/no-go with dated sources, explicit arithmetic and the repo evidence it rests on (no training pipeline, policy or consent flow exists; training terms appear only in roadmap/backlog; 5 live probes cannot detect an effect: about 118 items per condition are needed to tell 70% from 85%). Outside figures come from 2026 web pages, many blogs or vendor pages, and are labelled as ranges to re-check before any spending.
- EVOL-09: justified no-go; no experiment, download or spend. The five gates are executable and tested (25 tests; five mutants each failed tests); a blank template run is recorded verbatim in the doc.
- Tests actually run: full suite 2995 tests, 0 failed, 0 errors, 1 skipped; ruff clean. The LIFE quiet-hours test passed in this run (05:35-05:55 UTC) because A2 made it clock-independent in 8edb6c0; this confirms the earlier diagnosis that it depended on the wall clock.
- OWNER-REQUIRED: which models and GPU you run, what text you permit for training, a held-out set, any spending cap and approval; run the study's section 6 list before reopening EVOL-09.
- A3 status: IDLE.

## 2026-11-25T06:20Z - MIA-COMP-04 slice 4 (A7), commit 6b3f5f8
- `tests/test_broker_model_tools.py` (5 tests over the tools the real app registers) and 2 race tests in `tests/test_broker_enforcement.py` (8 threads on one approval: exactly one 200; 16 threads on the store: exactly one consume). Race tests were run 3 times, stable. These new tests were not mutation-checked.
- A first draft of one test wrongly expected an exception; the read tool returns a structured refusal (`ok: False`, FilesystemAccessError). Test corrected, not the code.
- Run: full suite exit 0 (about 17 minutes now, run in the background) and ruff clean on the tree before the final rebase; after it the broker and route-capability tests were re-run (pass).
- A7 idle. COMP-04 stays PARTIAL for the reason on its board row (caller identity, COMP-02/LIFE-13).

## 2026-11-25T08:10Z - MIA-COMP-02 pushed and closed for its scope (A7), commit c955516
- New: `src/mia/security/pairing.py`, `src/mia/security/companion_api.py`, `tests/test_companion_pairing.py` (31 tests, real Ed25519 keys, real SQLite/app), COMP-01 inventory rules for the new routes, `companion` optional-dependency extra in `pyproject.toml` (cryptography; was already a dev/backup extra and is imported lazily).
- Mutation checks: challenge single-use removed -> 2 failures; revocation re-check removed -> survived at first because `revoke()` also deletes sessions, so I added a test that revokes via the database only; confinement guard removed -> 1 failure. All now killed; sources restored.
- Two of my own test mistakes were fixed in the tests, not the code (lockout test used an expired code).
- Run: full suite exit 0 on the tree before the final rebase; after it the pairing, route-capability, local-guard and life-ui tests were re-run (pass).
- COMP-03 (Windows companion lifecycle, A5) now has its dependency on main. A7 idle.


## 2026-11-25 — MIA-LIFE-03 moved to DONE (owner verification deferred)
- Both implementation halves are already on remote main: task workspace UI (466517f) and Brain/conversational task actions (5424535).
- Remaining items are owner-only accessibility checks and a browser check of the chat surface. Per owner instruction, these manual verification items will be completed by the owner after development is complete; they do not block implementation status.
- No source code or tests changed in this status reconciliation.

## 2026-11-25T16:55Z - MIA-LIFE-06 backend (A7)
- New: `src/mia/knowledge/file_index.py`, `tests/test_file_index.py` (32 tests, real adapter, real SQLite FTS5, real app), wiring in `create_app(file_index=...)`, COMP-01 inventory rules for `/api/file-index*`.
- Mutation checks killed: symlink skip, private-key skip, compaction/vacuum (deleted text stayed in DB file), FTS quoting, verify always-true.
- One of my own bugs: pydantic model inside the register function broke under postponed annotations (422); moved to module level.
- Container had been reset; re-cloned and reinstalled deps. Full suite run in the background, result recorded below when done.


## 2026-11-25 — Multi-agent queue policy correction (coordinator)

- Owner approved correcting agents' repeated false BLOCKED reports. Root cause: the master prompt and Continue workflow treated milestone handoff as a global gate, even where task-level dependencies were satisfied; registry documentation also described six slots while A7 was active.
- Updated `prompts/AI_MASTER_AGENT.md`, `docs/AI_CONTINUE_WORKFLOW.md`, `docs/AI_TASK_BOARD.md`, `docs/DEFERRED_MILESTONE_BACKLOG.md` and `docs/AI_AGENT_REGISTRY.md`.
- Milestones now express roadmap/release order only. Agents may work across milestones when explicit dependencies, security invariants and interfaces are satisfied. `BLOCKED` requires a named dependency, evidence and an unblock condition; independent slices must continue.
- `PARTIAL` no longer blocks unrelated tasks. Remaining implementation must be split into uniquely identified claimable child tasks; owner-only manual checks are tracked separately and do not hold implementation open.
- Corrected the LIFE-13 / COMP-01 / COMP-02 dependency wording to remove the circular identity-model wait. Corrected the stale EVOL-02 note: EVOL-01 is DONE (02c96d1).
- Documentation-only change. No source code or tests changed or run. Existing implementation/security dependencies remain in force.

- Added 12 uniquely identified residual child tasks to the backlog and task board for the unfinished implementation in LIFE-06/07/08 and COMP-01/04. Independent slices are OPEN; only LIFE-08-APPROVAL and the final COMP-01/COMP-04 owner-identity slices are BLOCKED on explicit COMP-04/LIFE-13 dependencies. No manual owner checks were added as blockers.

- LIFE-06 test run (A7, 2026-11-25): full suite on a fresh container: 9 failures, all voice-wake (1 transport + 8 UI), caused by the optional `websockets` package missing in the new environment; after installing it those two files pass, and the files after them pass. Everything else passed. No LIFE-06 or other-agent test failed.

## 2026-11-25T18:10Z — MIA-LIFE-06-UI claimed (A6)

- Owner "continue development, no false blockers" prompt. Backend contract `/api/file-index/*` on main. Plan: `/files` page (select folder inside a configured root, reindex, search with path/line ranges, current/changed/missing, untrusted + injection flags, skip reasons, verify, secure delete of file/selection/all); add-only route, nav link, package-data; Chromium tests with real FileIndex.
