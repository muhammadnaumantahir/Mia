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

