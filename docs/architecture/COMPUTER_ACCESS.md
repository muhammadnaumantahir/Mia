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

## Terminal execution (Feature 4, M5)

`computer/terminal.py`, API under `/api/computer/terminal/*`. This replaces the earlier "shell disabled" stance with a
controlled executor; there is still no unrestricted command path.

1. **Validate and classify** (`classify_command`): every command gets a risk (low/medium/high) or is **blocked**.
   Blocked even with approval: administrative/system tools (sudo, runas, reg, schtasks, netsh, shutdown, format, diskpart,
   bcdedit, ...), security-setting changes (Set-MpPreference, Set-ExecutionPolicy), encoded/obfuscated commands, piping into
   a shell, download-and-run, credential/key files and locations, environment dumps, and recursive deletion of drive roots
   or system folders. High: delete/kill, network tools (curl, ssh, git push/pull), package managers, nested shells.
   Medium: anything that runs a program or writes files. Low: read-only inspection (git status/log/diff, ls, cat, version checks).
   The classifier is defence in depth, not a sandbox; approval is the control that matters.
2. **Confine**: the working directory must be inside an owner-configured filesystem root (no traversal, symlinks, junctions).
3. **Authorize**: one-time approval bound to command, shell, directory, timeout and risk, or an active owner grant from
   `PermissionPolicy` (capability `terminal.run`, level execute, directory-scoped). Grants never cover high-risk commands,
   expire, and can be revoked. This is the first adapter that consults `PermissionPolicy`.
4. **Run**: no model text reaches a shell except through the validated command; child gets a scrubbed environment
   (PATH, temp, home and similar only; no API keys/tokens); stdin closed; max 3 concurrent; timeout default 60 s, max 600 s;
   timeout and cancel kill the whole process tree.
5. **Report**: separate stdout/stderr (256 KB cap each, `truncated` flag), exit code, status
   (`completed|failed|timed_out|cancelled`), secret-looking output redacted. A zero exit code means the process ended
   normally, not that the user's goal was achieved; callers must verify outcomes themselves.
6. **Stop**: `POST /api/computer/terminal/executions/{id}/cancel` and `POST /api/computer/stop-all`.
- Audit: `terminal_started`, `terminal_finished`, `terminal_refused`, `terminal_cancel_requested`, `stop_all` events.
- Not implemented: administrator elevation, interactive/TTY sessions, persistent shell sessions, execution history that
  survives a restart (execution output is in memory; the audit events persist).
- Verified on Linux with real `bash`. PowerShell/cmd argument handling and quoting rules are untested on Windows.

## Browser automation (Feature 4, M6)

`computer/browser.py` (controller) and `computer/browser_actions.py` (approval-bound actions). The browser is Mia's own
isolated Playwright context, never the owner's default profile. Every request of every tab, including redirects and
sub-requests, goes through the URL policy (public web only; loopback/private/reserved blocked).

- **Observe (no approval):** `GET /api/browser/observe` returns page text and visible controls (links, buttons, inputs,
  selects) with short-lived element references (`e1`...), flags for consequential controls, and `content_trust:
  untrusted_external_web_content`. Password values are never returned. `find` filters by label; `extract` returns headings,
  tables and lists.
- **Prepare (no approval, verified by read-back):** `fill`, `select`, `check`, `scroll`, tab list/new/switch/close, and
  on-request `screenshot` to an owner-configured folder. `fill` refuses password, payment (`cc-*`), one-time-code, file and
  button controls, and refuses text that looks like a secret. Element references go stale when the page is re-observed or
  navigates; stale references are rejected rather than guessed.
- **Approval required:** `click`, `download`, `upload` via `/api/browser/actions/{preview,execute}`. The approval is bound to
  the page URL and content digest and to the real element (tag, type, label, link target, form action). A page change
  after approval invalidates it. Previews explain the action ("CONSEQUENTIAL click 'Place order' (submits a form) on
  host"). Form submission, sending and uploading are flagged consequential. Uploads are also bound to the file's SHA-256 and
  come only from configured filesystem roots (credential files blocked, 25 MB cap).
- **Verification:** a click reports URL/title/content/new-tab effects; if nothing observable changed, `verified` is
  false with a note instead of claiming success. Downloads report the saved path and SHA-256. Uploads read back the
  input's file list.
- **Downloads:** refused unless an approved download action is in progress (unexpected/automatic downloads are cancelled);
  saved only to the configured download folder (default `<data>/browser-downloads`), never overwriting, executable and script
  types (.exe, .msi, .bat, .ps1, .js, ...) refused, 100 MB cap.
- **Authenticated sessions:** optional persistent Mia-only profile (`MIA_BROWSER_PROFILE`) so the owner can log in once; no
  API reads cookies, storage or passwords. CAPTCHA and access controls are not bypassed. Mia does not type passwords.
- Configuration: `MIA_BROWSER_EXECUTABLE` selects a Chromium/Chrome binary; downloads/screenshots under the data folder.
- **Verification status:** all of the above ran against a real headless Chromium 131 (bundled-binary build) on Linux with a
  local HTTP test server. Real form submission and upload were confirmed by what the server received. Not verified: Windows
  browsers, persistence of a logged-in profile across restarts (that Chromium build never writes the cookie store, so the
  restart test skips itself; run it on a full Chrome/Chromium), and real third-party websites.
- Tests: `tests/test_browser_automation.py` skips when no Chromium is available; set `MIA_TEST_BROWSER=/path/to/chromium`.

## Desktop observation and interaction, activity indicator, emergency stop (Feature 4, M7)

**Desktop (`computer/desktop_control.py`, `/api/desktop/*`, Windows UI Automation via pywinauto).**
- `GET /api/desktop/windows/{handle}/observe`: controls (type, name, automation id, enabled, toggle/selected state) with
  the actions each supports. Password controls are skipped and edit-control values are never returned.
- Actions through `POST /api/desktop/actions/{preview,execute}`: `click` (invoke, `click_input` fallback), `toggle`,
  `select`, `keys` (literal text plus a whitelist of keys; Alt+F4, Win key, Ctrl+Alt+Del, Ctrl+V are not allowed;
  secret-looking text refused), `scroll`, `click_at` (coordinates, explicit last-resort fallback) and `screenshot`
  (one window, per-approval, never continuous).
- Each action is bound by approval to the window (handle, title, process, class) and the control's identity and state; an
  ambiguous or disabled target is refused. Consequential labels (Delete, Send, Install, Close, ...) are flagged in the
  approval summary.
- **Cycle:** observe -> understand (unique control resolution) -> act -> observe -> verify. Verification compares the
  re-read UI: toggle must flip, a selected item must report selected, keys must change the control's value hash, clicks
  must produce a window change (content, title, new window, or closed window). If nothing observable changed the result
  is `verified: false` with an explanation; a returned call is never reported as success. There are no automatic retries
  of non-idempotent actions.
- **Protected windows:** UAC consent, credential/logon UI, Windows Security, regedit, mmc and Task Manager are refused for
  observe, window management, typing and every desktop action (`make_window_guard`). The process name comes from psutil;
  if it cannot be resolved the window is allowed through (documented fail-open on unknown names).
- `desktop.type_text` now reads the control back and reports `verified`; previously it reported success on no exception.
- OCR is not implemented (UI Automation labels cover the supported cases). Screenshots need Pillow via pywinauto.

**Activity indicator and stop switch (`computer/control.py`).**
- Every action registers an activity (`observing`, `interacting`, `executing`) visible at `GET /api/computer/activity`.
- `POST /api/computer/stop-all` immediately cancels running terminal commands and blocks all new automation (HTTP 423)
  until `POST /api/computer/resume`. The stop check runs before approvals are consumed in every executor, so a stopped
  system does not burn one-time approvals. The control can only restrict; it never grants permission.
- Wired into terminal, file operations, process termination, browser actions and desktop actions. Listeners are best-effort:
  a failing listener cannot disable the stop switch.

**Verification status:** all desktop logic is tested with a fake UI Automation tree (17 tests). **Nothing has been run
against a real Windows desktop**, real pywinauto element behavior (invoke patterns, toggle/selection state, scroll, `type_keys`
quoting), or a real UAC prompt. Manual Windows verification is required before relying on this.

## Multi-step computer tasks (Feature 4, M8)

`computer/tasks.py` (engine), `computer/task_handlers.py` (step handlers), API under `/api/computer/tasks*`. This is a
dedicated durable ledger for computer work (tables `computer_tasks`, `computer_task_steps`, `computer_task_events`); the
general `agent/tasks.py` store is unchanged.

- **Plan:** an ordered list of up to 20 steps; each names a registered capability (`filesystem.*`, `terminal.run`,
  `process.*`, `apps.*`, `browser.*`, and on Windows `desktop.*`) and arguments. Unknown capabilities, missing arguments,
  unknown fields and forward references are rejected up front. A step may reference an earlier step's recorded result with
  `${steps.N.path}` (for example a recovery id or a found element reference). `GET /api/computer/tasks/capabilities` lists what
  can be planned on this machine. A plan cannot grant permission and cannot carry approvals.
- **Execution:** `POST /api/computer/tasks/{id}/run` runs steps until the task finishes or needs the owner. Before each step the
  handler re-inspects the *current* state (revalidation). A consequential step creates its own approval through the same
  preview/approve/execute code as the direct API (or is covered by an active owner grant), then stops with status
  `awaiting_approval`; approving one step never approves another. If the world changed after approval the approval no longer
  matches, the step does nothing and asks for a fresh approval.
- **Checkpoints and restart:** every transition is written to SQLite. After a restart, tasks that were running become
  `interrupted`, the step that was in flight becomes *outcome unknown* and is not re-run. `run` will not continue past an
  unknown consequential step; the owner must `retry-step`, which clears its approval so it is re-validated and re-approved.
  An interrupted read-only step is simply re-run. Completed steps are never repeated.
- **Control:** `pause` (before the next step), `cancel` (also cancels the running terminal command of that task), `retry-step`,
  and the global `stop-all` (tasks pause instead of running). Retries apply only to idempotent read-only steps, at most
  3 attempts, only for transient failures. Consequential steps are never retried automatically.
- **Verification and reporting:** each step stores `verified` true/false/unknown from the adapter (file hashes, process gone,
  UI re-observed, page effect, expected terminal output). Terminal exit code 0 alone is recorded as *not independently
  verifiable* unless an `expect_output` pattern matched. The task outcome is `verified`, `partially_verified`, `unverified`,
  `failed` or `cancelled`, and `GET .../report` renders only recorded data.
- **Dev server:** a `terminal.run` step with `ready_pattern` waits for that text in the live output and reports ready while the
  process keeps running; if it never appears, the process is cancelled and the step fails.
- A defect found and fixed while testing this milestone: the terminal reader used a blocking `read(4096)`, so output only appeared
  when the command exited. It now uses `read1`, and a regression test covers live output.
- **Verification status:** tested with fake handlers (ordering, approvals, retries, pause/cancel, restart recovery) and with
  real adapters on Linux: file workflows, delete/restore via templating, terminal failure halts, dev-server readiness, process
  termination, cancel of a running command, and a real browser find/approved-click/extract workflow. Desktop steps are
  registered only on Windows and are unverified there.
