# Computer Access & Automation: audit and checklist

**Audit date:** 2026-11-19
**Baseline:** `main` at `8b624a6`; `pytest` = 450 passed, 1 skipped (Linux, Python 3.12, no Windows host).
Voice, Memory (persistent identity, structured records, audit log) and the AI Brain/gateway code were present and green before this feature started.

## What exists today (verified by reading source and running tests)

| Area | Module | Working | Gaps |
|---|---|---|---|
| Filesystem | `computer/filesystem.py` (267 lines) | Configured roots, traversal and symlink checks, list/search-by-name/read UTF-8, preview->approve->execute write bound to file hashes, blocked credential/.env/.git writes, 2 MB cap | No create-folder, rename, copy, move, delete, compare, duplicates, content search, metadata, project inspection, backup/recovery on edit, junction handling on Windows, encoding detection |
| Approvals | `computer/approvals.py` (235 lines) | SQLite one-time approvals bound to a payload digest, pending list, approve/reject/consume | No expiry policy review, no revocation, no session/persistent grants, no risk levels |
| App launch | `computer/applications.py` (161 lines) | Owner allowlist, fixed args, `shell=False`, preview/approve/execute | No discovery of installed apps, no launch-failure detection, no window focus/min/max/close |
| Browser | `computer/browser.py` (167 lines) | Playwright isolated context, public-host-only URL filter, page text, click with approval | No tabs, forms, scrolling, structured extraction, downloads/uploads, screenshots, post-action verification |
| Desktop UI | `computer/windows_ui.py` (299 lines) | pywinauto: visible windows, text-control metadata, approved text entry | No clicks/keys/scroll, screenshots, window management, observe-act-verify loop; **never run on real Windows** |
| Terminal | none | n/a | **Missing entirely** (docs state shell is disabled) |
| Process/system | `agent/resources.py`, process inventory tool | CPU/RAM sampling, read-only process list | No disk, network status, process detail, hang detection, terminate-with-approval |
| Agent tools | `agent/tools.py` | Registry with read/preview modes only, schema validation | No unified computer capability contract (OS, risk, timeout, verification) |
| Tasks | `agent/tasks.py` (769 lines) | Persistent tasks, leases, restart recovery, pause/stop, cancellation between read-only steps | No computer-task step ledger or checkpoints with revalidation |
| Voice | `voice/`, assistant UI | STT/TTS, interrupt, push-to-talk | Not connected to computer task execution |

## Duplicated or inconsistent pieces to consolidate (not rebuild)

- `computer/contracts.py` defines `ToolContract`/`ToolResult`, while `agent/tools.py` defines a different `ToolResult`/`ToolDefinition`. The new capability contract will extend `ToolContract` and be mapped into the agent registry.
- Permission names (`filesystem.read`, `process.launch`, ...) are strings in docs and code with no central catalogue.

## Implementation checklist

Status is updated as milestones are pushed. Nothing is ticked without a test or a documented manual check.

- [x] M1 Audit (this document)
- [x] M2 Unified capability contract, environment detection, permission levels, risk classes, health checks (Linux-tested; Windows detection simulated with injected platform data, not run on Windows)
- [ ] M3 Filesystem: folders, rename, copy, move, delete-to-recovery, compare, duplicates, metadata, project inspection, edit backups
- [ ] M4 Application discovery and window management; process detail and approved termination; disk/network status
- [ ] M5 Terminal execution: validation, risk classification, approval, timeout, stdout/stderr, redaction
- [ ] M6 Browser: tabs, forms, scroll, structured extraction, downloads/uploads to approved folders, verification
- [ ] M7 Desktop: observe/act/verify loop, UIA click/key/scroll, screenshot on demand
- [ ] M8 Multi-step computer task engine: step ledger, checkpoints, cancel, retry limits, revalidation on resume
- [ ] M9 AI Brain tools and Voice use the same executor and permission system
- [ ] M10 Kill switch, audit, privacy, docs, full regression

## Verification limits that apply to this feature

- This work is developed in a Linux container. **Windows desktop behavior (UI Automation, window management, PowerShell, UAC boundaries) cannot be verified here** and will be marked "unverified on Windows" until run on the owner's PC.
- Browser tests need Playwright's Chromium; availability in this container is checked at M6.
