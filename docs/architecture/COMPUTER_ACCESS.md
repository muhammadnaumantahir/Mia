# Mia computer-access architecture

## Goals

Give Mia useful access to the user's Windows computer through a catalog of typed, permissioned actions. A model must not receive unrestricted shell access simply because it can formulate a command.

## Current foundation

The first adapter is scoped filesystem access:
- Configured roots are read/write boundaries.
- If `MIA_COMPUTER_ROOTS` is unset, the only root is Mia's `./workspace`.
- Paths are resolved and checked against the selected root to prevent traversal and symlink escapes.
- Directory listings are bounded to 200 entries.
- UTF-8 reads and writes are bounded to 2 MB.
- Writes to credential/key/certificate files, environment files and Git metadata are blocked.
- Reads are immediate; writes use preview → approval → execute.
- Approval is one-time and bound to action, root, relative path, previous file hash and new content hash.
- The audit ledger records successful writes, but not file contents.

## API

- `GET /api/computer/roots`
- `GET /api/computer/files?root_id=root_1&relative_path=.`
- `GET /api/computer/file?root_id=root_1&relative_path=notes.txt`
- `GET /api/computer/search?root_id=root_1&query=readme`
- `POST /api/computer/files/write/preview`
- `GET /api/computer/approvals`
- `POST /api/computer/approvals/{approval_id}/approve`
- `POST /api/computer/files/write/execute`

The service remains bound to localhost. Remote access must not be enabled until authentication, CSRF protection, audit controls and user identity are implemented.

## Permission catalogue (planned)

| Permission | Scope | Default |
|---|---|---|
| filesystem.read | Read/list/search within an allowed root | Allowed for configured roots |
| filesystem.write | Create/replace file within an allowed root | Approval required |
| filesystem.delete | Delete a file/folder | Not implemented; approval required |
| process.inspect | Read process metadata | Not implemented |
| process.launch | Launch one allowlisted application | Not implemented; approval required |
| process.terminate | Stop a process | Not implemented; approval required |
| browser.navigate | Navigate an isolated browser context | Not implemented |
| browser.interact | Click/type/upload/download in a browser context | Not implemented; risk-based approval |
| system.settings.read | Read system configuration | Not implemented |
| system.settings.write | Change system configuration | Not implemented; approval required |
| shell.execute | Run a command in a constrained worker | Disabled; not available to the main assistant |

## Next integrations

1. Windows application catalog and launch adapter with explicit allowlist.
2. Browser adapter using Playwright with a dedicated browser context and download policy.
3. Document adapters for PDF, DOCX, XLSX and PPTX, with read/write previews.
4. Expand Windows UI Automation to carefully scoped browser/app interactions with per-action approvals.
5. Task orchestrator to combine actions and resume safely after failures.
6. Restricted worker process for approved scripts; no arbitrary code in the privileged process.

## Non-negotiable safety boundaries

- A tool declares its permission and side effects.
- The policy engine decides whether an action is allowed or requires approval.
- Model instructions and external documents cannot grant permissions.
- Secrets, cookies and provider keys are not passed to tools by default.
- File writes and other destructive actions are auditable and reversible where possible.
- Broad roots are not inferred from the machine; the owner must configure them.

## Application launching

- Mia loads an owner-managed allowlist from `MIA_APPLICATIONS_FILE`.
- Each entry defines one application ID, executable and fixed argument list.
- Models cannot supply or modify executable arguments.
- Shells and script hosts are blocked by the launcher.
- Launch is previewed, approved and executed with `shell=False`.
- Process termination and unrestricted terminal commands are not implemented.

Copy `config/applications.example.json` to `config/applications.json` and edit it for the applications actually installed on the machine. The personal file is ignored by Git.

Install the optional browser adapter with `python -m pip install -e ".[computer]"` and `playwright install chromium`.

## Browser automation

- Uses Playwright with a temporary isolated browser context, not the user's default browser profile.
- Runs headless by default and does not import saved passwords, cookies or extensions.
- Downloads are disabled.
- Navigation is limited to HTTP/HTTPS public hosts; loopback, private, link-local, reserved and local hostnames are blocked.
- Every browser request is checked, including requests following redirects.
- Navigation and page-text extraction are read-oriented.
- Clicking a selector requires a preview and one-time approval bound to the current page URL and exact selector.
- The current browser context has no authenticated user session unless a later explicit login flow is added.

The URL filter is a defense-in-depth control, not a complete network sandbox. DNS rebinding and browser-engine vulnerabilities require OS-level/network isolation before exposing the browser to untrusted automated workloads.


## Windows desktop UI Automation

The optional Windows adapter uses pywinauto's UI Automation backend:
- It can enumerate visible windows and expose only title, handle, process ID and window class.
- It can inspect a selected window's visible, enabled, non-password text-control metadata (AutomationId, control type, label and class), never the control's current text value.
- Text entry is limited to visible, enabled controls with a stable AutomationId and one of the supported text-control types.
- The preview is bound to the exact window handle/title, control ID/type and text SHA-256.
- The approval card displays the proposed text and its hash; no typing occurs before approval.
- Execution re-resolves the same window/control and consumes the one-time approval before replacing text.
- Arbitrary keystrokes, mouse clicks, menu selection, button activation, password controls, shell access and process termination are not implemented.

Install on Windows with `python -m pip install -e ".[computer]"`. This adapter is Windows-only; the other core tests remain cross-platform. UI Automation support varies by application, and elevated/admin windows may not be accessible to a non-elevated Mia process.

## Unified capability contract, environment and permission policy (Feature 4, M2)

- `computer/capabilities.py`: every capability declares name, description, supported OS, input/output schema,
  permission level (observe/read/write/execute/interact/administrative), risk (low..critical), timeout,
  constraints and a verification method. Only capabilities with a working adapter are registered.
- `computer/environment.py`: read-only detection of OS family/version, CPU/RAM, user, display, shells, disks.
  `GET /api/computer/environment`, `/api/computer/capabilities`, `/api/computer/health` report availability and
  the reason a capability is unavailable (wrong OS, missing optional dependency, no display).
- `computer/permissions.py`: owner-created grants (session or expiring persistent), directory scoping,
  revocation, history. Low-risk observe/read is allowed; write/execute/interact need a grant or per-action approval;
  high/critical risk always need per-action approval; administrative actions need a separate admin grant that
  still needs per-action approval. `granted_by` must be `owner`; no model-callable tool grants permissions.
- API: `GET/POST /api/computer/permissions`, `DELETE /api/computer/permissions/{id}`,
  `POST /api/computer/permissions/revoke-all`, `GET /api/computer/permissions/history`. The API is localhost-only;
  authenticated owner identity is still required before any remote exposure.
- Not yet done: existing adapters (filesystem write, app launch, browser click, UI text) still use their own
  approval flow and do not yet call `PermissionPolicy.authorize`; this is wired in the milestones that touch them.

## File operations (Feature 4, M3)

`computer/fileops.py` adds, on top of the scoped adapter:
- Read-only: metadata (with SHA-256), content search (secret-looking lines redacted, credential files skipped),
  text compare (diff), duplicate finder (size then SHA-256, reclaimable bytes), project inspection
  (type, extension counts, top-level tree, README excerpt with secrets redacted, docs, tests/git presence).
- Mutations with preview -> approval -> execute, approval bound to the exact source fingerprint: `mkdir`, `rename`,
  `copy`, `move`, `delete`, `restore`. Existing destinations are never overwritten. `delete` moves the item to
  `<root>/.mia-recovery/deleted/<id>/` with a manifest and can be restored; the recovery area is hidden from listing/search
  and cannot be targeted. Every mutation is verified by re-hashing and returns `verified`.
- Approved text writes now copy the previous file to `.mia-recovery/backups/` first (newest 50 kept) and return `backup`.
- Encoding detection (`read_text_auto`): UTF-8/UTF-8-BOM, UTF-16 with BOM, explicit cp1252 fallback flag, binary rejected.
- Windows junctions are treated like symlinks (`Path.is_junction`); this is untested on a real Windows host.
- API: `/api/computer/files/{metadata,search-content,compare,duplicates,project,recovery}` (GET) and
  `/api/computer/files/ops/{preview,execute}` (POST).
- Limits: copies up to 100 MB / 5000 files; compare 512 KB per file; duplicate scan 5000 files. Office/PDF document
  reading remains via the existing knowledge ingestion path; no new document writers were added.
- Like M2, these paths still use the one-time approval store directly; `PermissionPolicy` is not yet consulted.

## Application, window and process control (Feature 4, M4)

- **Discovery** (`computer/app_discovery.py`, `GET /api/computer/apps/discover`): installed application names from
  Start Menu shortcuts and the uninstall registry (Windows), `.desktop` entries (Linux), `/Applications` (macOS).
  Names only: no command lines are returned and nothing is launched. Launching still requires an owner allowlist entry.
- **Launch failure detection**: after an approved launch the process is polled; a non-zero early exit raises, a clean
  early exit is reported as `handed_off_or_exited_cleanly` with `verified: false`, a running process as `verified: true`.
- **Window management** (`WindowsUIAdapter`, Windows only): focus, minimize, maximize, restore, close via
  `/api/desktop/windows/action/{preview,execute}`. Approval is bound to handle, title, PID, class and action. Results are
  verified by re-reading window state (close = window gone); if nothing changed, `verified` is false. Close is refused when
  the title suggests unsaved work (`*`, `●`, "(modified)") unless the owner explicitly acknowledges the risk. This is a
  title heuristic only and cannot see unsaved state inside an application. Close uses the window's normal close request,
  not a process kill, so applications can still prompt to save.
- **Hung windows**: `GET /api/desktop/windows-hung` uses the OS `IsHungAppWindow` check (Windows only).
- **Processes** (`computer/processes.py`): `system/status` (CPU, RAM, disks, uptime, interface state only: no outbound
  connectivity probe), `processes/top` (by memory or CPU), `processes/{pid}`. No command lines or environment variables.
- **Termination**: preview -> approval -> execute, bound to PID + name + start time (PID reuse invalidates the approval),
  graceful by default with `force` as a separate approval. Refused: PID <= 4, Mia and its parent processes, critical OS and
  security process names, and any process whose owner is not the current user. Result is `verified` only if the process is
  actually gone. Elevated/administrator operations are not implemented.
- **Verification status**: process and system code ran against real processes on Linux. Window, registry and hung-window
  code ran only against test doubles. **No real Windows run has happened.**
- Policy engine wiring (`PermissionPolicy.authorize`) is still pending for these adapters.
