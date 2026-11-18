# Mia project status

**Last updated:** 2026-11-18  
**Repository:** muhammadnaumantahir/Mia  
**Default branch:** main  
**Overall state:** Active research and foundation implementation — not release-ready

## Verified repository baseline

- Repository was empty when work began; no existing code was overwritten.
- Repository is private and the default branch is main.
- The ten canonical SPS layers remain unchanged.
- Candidate sources and licenses have an initial audit; no upstream source code has been copied wholesale.

## Phase status

| Phase | Status | Evidence / next gate |
|---|---|---|
| 0. Research and reuse audit | In progress | Source/license matrix and upstream commit pins recorded; complete SBOM and full legal/dependency review remain |
| 1. Foundation | In progress | SPS registry, FastAPI app, SQLite observatory, immutable experiment baselines/snapshots and dashboard; current head still needs green CI |
| 2. AI gateway | In progress | Ollama and OpenAI-compatible adapters, free-first router, price guard and transient fallback; streaming, model capability metadata and monthly cost ledger remain |
| 3. Voice | In progress | Push-to-talk browser capture, local Faster-Whisper STT, editable transcript and browser TTS; target-PC benchmark and verified feminine voice selection remain |
| 4. Computer intelligence | In progress | Scoped filesystem, approval-bound writes, allowlisted app launch and isolated public-web browser; Windows UI automation and stronger OS/network sandbox remain |
| 5. Memory and knowledge | In progress | Persistent conversations, FTS5 memory, source provenance/trust labels and optional document parsers; parser integration and export/retention tests remain |
| 6. Autonomous agent | Foundation in progress | Persistent task state machine, approval/rejection API, task-control dashboard, evidence-backed deduplicated proposals and opt-in single-cycle dispatcher; scheduler, planner, real telemetry and governed tool executor remain |
| 7. Self-programming | Foundation in progress | Versioned candidate source, lineage, validation/registration APIs and evidence ledger; external validation worker, OS sandbox, activation and rollback remain blocked |
| 8. 30-day experiment | Planned | No Day 0 baseline has been captured. Start only after the sandbox, fixed benchmarks, stop conditions and reproducible environment manifest are ready |

## CI verification

- Last fully verified green run remains commit 19c3fde9d110611a648c9a168b7841a0d035db75 on Windows and Ubuntu with Python 3.13.
- Newer GitHub Actions runs, including c058b38118bab2fca6f0b7c8db2f26fe3fba533b, returned failed jobs with runner ID 0, no steps and no downloadable logs. Those results do not establish whether the code passed or failed.
- The current repository head must be re-run on actual Windows and Linux runners before release claims are made.
- Latest observed Actions jobs still fail before runner assignment (runner ID 0, no steps/logs); this is an infrastructure observation, not passing test evidence.

## Explicitly not complete

Mia is not yet a finished personal assistant. It does not have a running autonomous scheduler, automatic multi-step tool planning, self-generated code execution, hot activation, a verified OS-level code sandbox, unrestricted terminal access, always-on wake word or a completed 30-day experiment.

The 30-day experiment has not started. The observatory must not display invented daily growth data.

## Phase completion rule

A phase becomes complete only when:
1. The roadmap exit criteria are met.
2. Automated tests pass on Windows and Linux where relevant.
3. Target-device tests pass for hardware-specific components.
4. Security and license checks are recorded.
5. The evidence links to the exact commit, workflow run and test result.
