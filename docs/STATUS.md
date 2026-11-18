# Mia project status

**Last updated:** 2026-11-18  
**Repository:** muhammadnaumantahir/Mia  
**Default branch:** main  
**Overall state:** Active foundation implementation — not release-ready

## Verified repository baseline

- Repository access verified.
- Repository visibility: private.
- Default branch: main.
- Repository was empty when work began.
- No pre-existing files or implementation were overwritten.
- Canonical SPS layer definitions preserve exactly the ten agreed layers.

## Phase status

| Phase | Status | Evidence / next gate |
|---|---|---|
| 0. Research and reuse audit | In progress | Initial upstream source/license matrix and commit pins recorded; complete SBOM, full dependency review and final legal review remain |
| 1. Foundation | In progress | SPS registry, FastAPI app, SQLite observatory, experiment preparation, immutable Day 0/daily snapshots, dashboard and test suite; verify current code on Windows and Linux |
| 2. AI gateway | In progress | Ollama and OpenAI-compatible adapters, provider registry, free-first routing, cost guard and transient fallback; add model capability metadata, streaming and runtime benchmark validation |
| 3. Voice | In progress | Push-to-talk browser recording, local Faster-Whisper API and browser speech output; install/benchmark on target PC, test female voice availability, add optional wake-word only after privacy/false-trigger tests |
| 4. Computer intelligence | In progress | Scoped filesystem, one-time approval, app allowlist/launch, isolated public-web browser and approval-gated click; add Windows UI automation, document creation and stronger OS/network sandbox |
| 5. Memory and knowledge | In progress | Persistent conversations, recent-session restore, FTS5 memory, source provenance, trust labels and text-first document ingestion; test PDF/DOCX/XLSX/PPTX optional parsers and retention/export |
| 6. Autonomous agent | Planned | Planner, scheduler, task-state machine, budgets, resource-aware workload and recovery |
| 7. Self-programming | Planned | Capability lifecycle, isolated generation/validation, disk versions, activation and rollback |
| 8. 30-day experiment | Planned | Start only after immutable baseline, fixed benchmarks, sandbox and stop conditions pass |

## CI verification

- Last fully verified green CI run: commit 19c3fde9d110611a648c9a168b7841a0d035db75, with install, lint and tests succeeding on both Windows and Ubuntu using Python 3.13.
- Newer Actions runs have returned failed jobs with no assigned runner, no step list and no downloadable logs. Therefore, the latest computer, memory and document-ingestion changes are not yet CI-verified. Do not treat this runner-side status as a passing test.
- Retry a stable repository head when GitHub assigns runners and preserve the exact run URL/commit as evidence.

## What is not yet complete

Mia is not yet a full personal assistant. The current interfaces and adapters are a foundation, not finished voice, browser, desktop, memory or reasoning experiences. Always-listening wake word, verified feminine TTS on the target PC, process management, unrestricted terminal operations, multi-step autonomous execution, self-programming/hot activation and the 30-day experiment are not implemented.

The 30-day experiment has not started. The observatory can prepare a baseline and store immutable snapshots, but no real Day 0 benchmark has been captured.

## Phase completion rule

A phase moves to Complete only when:
1. Its exit criteria in docs/ROADMAP.md are met.
2. Automated tests pass on Windows and Linux where relevant.
3. Security and license checks are recorded.
4. Target-device smoke tests pass for hardware-specific components.
5. Evidence is linked in this file.