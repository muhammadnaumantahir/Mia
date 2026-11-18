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
| 3. Voice | Partial implementation | Push-to-talk Faster-Whisper STT, optional offline pyttsx3/SAPI5 WAV synthesis, metadata-backed local voice selector and browser fallback are connected. A metadata-reported female voice is preferred when available; no voice pack is downloaded. Windows voice inventory/latency, microphone tests and wake-word verification remain open |
| 4. Computer intelligence | In progress | Scoped filesystem, approval-bound writes, allowlisted app launch, browser and Windows visible-window/text-control adapter; stronger OS sandbox and target-PC verification remain |
| 5. Memory and knowledge | In progress | Persistent conversations, FTS5 memory, provenance labels and document parsers; parser integration/export/retention tests remain |
| 6. Autonomous agent | Manual execution + opt-in background read-only dispatch | Task Desk proposes/reviews tasks and supports approve/reject/cancel. The API can manually execute registered read-only tools; when MIA_AUTONOMY_ENABLED=true, the background worker filters for already-approved system resource/process/allowlisted-app inventory tools only. It checks CPU/RAM, exposes pause/resume, persists the owner pause across restarts, and records minimal Observatory events. Other tools remain queued for manual flows; interrupted tasks are now recovered to failed with an audit event and require owner re-approval before retry. Broader multi-step workflows, automatic continuation across restart, durable worker leases and target-device checks remain open; bounded 2-5 step read-only sequences are now supported |
| 7. Self-programming | AI draft + Capability Lab + Docker validation | Mia can draft a pure-Python run(input_data) module and pytest file using free-only model routing; drafts are reviewed in-browser and must be explicitly saved. The Lab stores immutable source/test versions, runs Docker validation and registers only passing versions. Registered permission-free candidates can also run once in a fresh isolated Docker container with JSON input/output limits; no runtime activation or hot-loading is enabled. Windows verification, permission broker, activation and rollback remain |
| 8. 30-day experiment | Day 0, explicit start, host verification and optional daily scheduler implemented | Baseline is captured on the Mia host with Ruff, the separately versioned fixed benchmark suite and the full evolving regression suite; all must pass and the manifest is frozen by hash. Daily snapshots are server-generated, resource-aware and can be manually triggered or automatically scheduled after explicit experiment start. Missed days are recorded unavailable; a run that misses its final capture window becomes terminally incomplete, never completed. The report API/viewer verifies stored Day 0 and daily evidence hashes and distinguishes completed from incomplete runs. No real Windows-host baseline, 30-day run or evidence-backed final results exist yet |

## Verification

- Last known fully green CI commit: 19c3fde9d110611a648c9a168b7841a0d035db75 on Windows and Ubuntu with Python 3.13.
- Recent Actions runs, including 44346916562, ended both Windows and Ubuntu matrix jobs before any steps were recorded. Test/lint outcomes remain inconclusive, not passed or failed by code evidence. The current development container cannot resolve github.com for a local checkout, so no local test result is claimed.
- Planner, scheduler, read-only task-executor, Task Desk, Capability Lab, capability lifecycle, Docker sandbox and Day 0 capture tests have not yet been confirmed by a successful CI run. Latest inspected run 44346916562 failed both matrix jobs before any steps were recorded; runner allocation/test outcomes remain inconclusive. The current development container cannot resolve github.com for a local checkout, so no local test result is claimed.
- Windows UI adapter has mocked contract tests but has not been exercised against real desktop apps on the owner's PC.
- 2026-11-18 follow-up commits `7f1598d` and `553e8c1` repair sandbox test-fixture defects: the Docker fake now has its `Path` dependency at module scope, and capability execution fixtures contain real Python newlines. These are source-level corrections only; the test suite has not yet been re-run, so they are not recorded as passing.

## Not complete

Mia is not yet a finished personal assistant. General multi-step autonomous execution, isolated self-programming activation/rollback, verified OS-level sandbox on the target PC, always-on wake word, real desktop interaction and the actual 30-day experiment remain incomplete.

No Day 0 or daily growth data may be fabricated. Daily snapshots require local host verification and are not auto-filled when Mia is offline.

## Phase completion rule

A phase is complete only after roadmap exit criteria, relevant Windows/Linux tests, target-device checks, security/license review, and evidence tied to the exact commit and successful workflow run.