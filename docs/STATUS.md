# Mia project status

**Last updated:** 2026-11-18  
**Repository:** muhammadnaumantahir/Mia  
**Default branch:** main  
**Overall state:** Active implementation — not release-ready

## Phase status

| Phase | Status | Evidence / next gate |
|---|---|---|
| 0. Research and reuse audit | In progress | Source/license matrix and upstream pins recorded; complete SBOM and legal/dependency review remain |
| 1. Foundation | Implemented foundation; CI verification blocked | SPS registry, FastAPI app, SQLite observatory, memory/task/capability stores, approval flow and dashboards exist. Full cross-platform regression confirmation remains blocked by GitHub jobs that end before runner assignment |
| 2. AI gateway | In progress | Ollama and OpenAI-compatible adapters, free-first router and fallback; streaming, capability metadata and cost ledger remain |
| 3. Voice | In progress | Push-to-talk, local Faster-Whisper STT and browser TTS; target-PC benchmark and verified feminine voice remain |
| 4. Computer intelligence | In progress | Scoped filesystem, approval-bound writes, allowlisted app launch, browser and Windows visible-window/text-control adapter; stronger OS sandbox and target-PC verification remain |
| 5. Memory and knowledge | In progress | Persistent conversations, FTS5 memory, provenance labels and document parsers; parser integration/export/retention tests remain |
| 6. Autonomous agent | Task Desk + manual read-only execution | Task Desk submits model-assisted plans, lists proposals, and supports approve/reject/cancel. A safe CPU/RAM task demonstrates explicit approval and one registered read-only tool through POST /api/tasks/{task_id}/execute; preview and mutation tools remain excluded. Background scheduler still has no production executor/API lifecycle, and target-device checks remain open |
| 7. Self-programming | AI draft + Capability Lab + Docker validation | Mia can draft a pure-Python run(input_data) module and pytest file using free-only model routing; drafts are reviewed in-browser and must be explicitly saved. The Lab stores immutable source/test versions, runs Docker validation and registers only passing versions. No network, read-only candidate mount, non-root worker and bounded resources; Windows verification, activation and rollback remain |
| 8. 30-day experiment | Local Day 0 capture + explicit start implemented | Dashboard captures a clean-commit Day 0 on the Mia host using Ruff and the full pytest suite; the immutable manifest is hash-verified and Start remains a separate manual action. No real baseline has been captured on the owner's Windows machine yet |

## Verification

- Last known fully green CI commit: 19c3fde9d110611a648c9a168b7841a0d035db75 on Windows and Ubuntu with Python 3.13.
- Recent Actions failures returned runner ID 0 with no steps/logs. GitHub ended both matrix jobs before assigning runners; test/lint results remain unknown and neither success nor code failure is established.
- Planner, scheduler, read-only task-executor, Task Desk, Capability Lab, capability lifecycle, Docker sandbox and Day 0 capture tests have not yet been confirmed by a successful CI run. The latest workflow (run 44332049350) failed both matrix jobs with runner ID 0, zero steps and no downloadable logs; test/lint outcomes remain unknown.
- Windows UI adapter has mocked contract tests but has not been exercised against real desktop apps on the owner's PC.

## Not complete

Mia is not yet a finished personal assistant. Production autonomous planning/execution, isolated self-programming, hot activation, verified OS-level sandbox, always-on wake word, real desktop interaction and the 30-day experiment remain incomplete.

No Day 0 or daily growth data may be fabricated.

## Phase completion rule

A phase is complete only after roadmap exit criteria, relevant Windows/Linux tests, target-device checks, security/license review, and evidence tied to the exact commit and successful workflow run.