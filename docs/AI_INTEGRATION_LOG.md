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
