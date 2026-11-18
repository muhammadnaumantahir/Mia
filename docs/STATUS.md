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
| 5. Memory and knowledge | In progress | Persistent conversations, FTS5 memory, provenance labels and document parsers. Office DOCX/XLSX/PPTX ingestion now preflights ZIP entry count, expanded size, member size, encryption and compression ratio before invoking parsers; adversarial archive tests were added. Parser integration/export/retention and broader hostile-document testing remain |
| 6. Autonomous agent | Manual execution + opt-in background read-only dispatch | Task Desk proposes/reviews tasks and supports approve/reject/cancel. The API can manually execute registered read-only tools; when MIA_AUTONOMY_ENABLED=true, the background worker filters for already-approved system resource/process/allowlisted-app inventory tools only. It checks CPU/RAM, exposes pause/resume, persists the owner pause across restarts, and records minimal Observatory events. Other tools remain queued for manual flows; interrupted tasks are now recovered to failed with an audit event and require owner re-approval before retry. Broader multi-step workflows, automatic continuation across restart, durable worker leases and target-device checks remain open; bounded 2-5 step read-only sequences are now supported |
| 7. Self-programming | AI draft + Capability Lab + Docker validation | Mia can draft a pure-Python run(input_data) module and pytest file using free-only model routing; drafts are reviewed in-browser and must be explicitly saved. The Lab stores immutable source/test versions, runs Docker validation and registers only passing versions. Registered permission-free candidates can also run once in a fresh isolated Docker container with JSON input/output limits; no runtime activation or hot-loading is enabled. Windows verification, permission broker, activation and rollback remain |
| 8. 30-day experiment | Day 0, explicit start, host verification and optional daily scheduler implemented | Baseline is captured on the Mia host with Ruff, the separately versioned fixed benchmark suite and the full evolving regression suite; all must pass and the manifest is frozen by hash. Daily snapshots are server-generated, resource-aware and can be manually triggered or automatically scheduled after explicit experiment start. Missed days are recorded unavailable; a run that misses its final capture window becomes terminally incomplete, never completed. The report API/viewer verifies stored Day 0 and daily evidence hashes and distinguishes completed from incomplete runs. No real Windows-host baseline, 30-day run or evidence-backed final results exist yet |

## Verification

- Last known fully green CI commit: 19c3fde9d110611a648c9a168b7841a0d035db75 on Windows and Ubuntu with Python 3.13.
- Recent push-triggered Actions runs, including 44365793438, 44365825105, 44365937960, 44365966310, 44366076182 and 44366187428, terminate both Windows and Ubuntu matrix jobs within seconds with `runner_id: 0`, an empty `runner_name`, and no step records. Re-running the latest failed jobs did not change this. The test/lint outcomes are therefore inconclusive; they must not be described as code failures or passing tests. The current development container cannot resolve github.com for a local checkout, so no local test result is claimed.
- Planner, scheduler, read-only task-executor, Task Desk, Capability Lab, capability lifecycle, Docker sandbox, document-ingestion hardening and Day 0 capture tests have not yet been confirmed by a successful CI run. The latest inspected Actions jobs have `steps: null`; runner allocation/execution cannot be verified from available logs.
- New regression coverage was committed for autonomy stop races and hostile Office ZIP containers. The Office archive guard's lint formatting was corrected in `be37487`; all changes remain unverified until a successful Windows and Ubuntu workflow completes.
- Windows UI adapter has mocked contract tests but has not been exercised against real desktop apps on the owner's PC.
- 2026-11-18 commits `7f1598d` and `553e8c1` repair sandbox test fixtures; `514ba85` rechecks live pause/emergency-stop state after resource sampling and immediately before handler execution; `a2fee6d` adds stop-race regression tests; `1501ab3` adds Office archive preflight limits; `1b5ec1b` adds malformed/high-expansion ZIP regression tests. These are committed source changes, not yet verified test passes.

## Not complete

Mia is not yet a finished personal assistant. General multi-step autonomous execution, isolated self-programming activation/rollback, verified OS-level sandbox on the target PC, always-on wake word, real desktop interaction and the actual 30-day experiment remain incomplete.

No Day 0 or daily growth data may be fabricated. Daily snapshots require local host verification and are not auto-filled when Mia is offline.

## Phase completion rule

A phase is complete only after roadmap exit criteria, relevant Windows/Linux tests, target-device checks, security/license review, and evidence tied to the exact commit and successful workflow run.