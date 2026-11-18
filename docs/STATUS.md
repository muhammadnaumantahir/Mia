# Mia project status

**Last updated:** 2026-11-18  
**Repository:** muhammadnaumantahir/Mia  
**Default branch:** main  
**Overall state:** Active implementation — not release-ready

## Phase status

| Phase | Status | Evidence / next gate |
|---|---|---|
| 0. Research and reuse audit | In progress | Source/license matrix and upstream pins recorded; complete SBOM and legal/dependency review remain |
| 1. Foundation | In progress | SPS registry, FastAPI app, SQLite observatory, experiment baseline/snapshot schema and dashboard; current head needs green CI |
| 2. AI gateway | In progress | Ollama and OpenAI-compatible adapters, free-first router and fallback; streaming, capability metadata and cost ledger remain |
| 3. Voice | In progress | Push-to-talk, local Faster-Whisper STT and browser TTS; target-PC benchmark and verified feminine voice remain |
| 4. Computer intelligence | In progress | Scoped filesystem, approval-bound writes, allowlisted app launch, browser and Windows visible-window/text-control adapter; stronger OS sandbox and target-PC verification remain |
| 5. Memory and knowledge | In progress | Persistent conversations, FTS5 memory, provenance labels and document parsers; parser integration/export/retention tests remain |
| 6. Autonomous agent | Manual read-only task execution added | Free-first model-assisted planner creates reviewable proposals only. Approved tasks with an explicit registered read-only tool descriptor can be executed once through POST /api/tasks/{task_id}/execute after CPU/RAM checks; previews and mutations remain excluded. Background scheduler still has no production executor/API lifecycle, and the Windows/Linux target-device checks remain open |
| 7. Self-programming | Foundation in progress | Candidate source, lineage, validation/registration APIs; isolated worker, activation and rollback remain |
| 8. 30-day experiment | Planned | No Day 0 baseline captured. Start only after sandbox, fixed benchmarks and reproducible environment manifest are ready |

## Verification

- Last known fully green CI commit: 19c3fde9d110611a648c9a168b7841a0d035db75 on Windows and Ubuntu with Python 3.13.
- Recent Actions failures returned runner ID 0 with no steps/logs. These are inconclusive and do not prove code failure or success.
- The planner and scheduler regression tests have not yet been confirmed by a successful CI run.
- Windows UI adapter has mocked contract tests but has not been exercised against real desktop apps on the owner's PC.

## Not complete

Mia is not yet a finished personal assistant. Production autonomous planning/execution, isolated self-programming, hot activation, verified OS-level sandbox, always-on wake word, real desktop interaction and the 30-day experiment remain incomplete.

No Day 0 or daily growth data may be fabricated.

## Phase completion rule

A phase is complete only after roadmap exit criteria, relevant Windows/Linux tests, target-device checks, security/license review, and evidence tied to the exact commit and successful workflow run.