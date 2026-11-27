# Mia computer-security handoff

**Updated:** 2026-11-27  
**Scope:** MIA-COMP-05 through MIA-COMP-11, MIA-COMP-04-OWNER, companion trust and the local computer boundary.

This is an owner handoff and verification plan, not a security certification. Source review and regression tests exist, but this development pass did not execute the tests or exercise real Windows hardware.

## Current security boundaries

| Boundary | Implemented control | Evidence location | Remaining limitation |
|---|---|---|---|
| Local HTTP/WebSocket | Loopback Host/Origin and cross-site guards | src/mia/security/local_guard.py, tests/test_local_guard.py | Not per-process OS identity; local tools without Origin are not blocked by browser-origin checks |
| Owner authority | WebAuthn passkey, one-use CLI bootstrap, hashed eight-hour sessions, exact localhost RP/origin | src/mia/security/owner_auth.py, tests/test_owner_auth.py | Same-user malware or a stolen active browser session remains dangerous; losing every passkey needs manual host recovery |
| Brain write tools | Request-scoped owner-session context checked before standing grants | src/mia/brain/broker.py, tests/test_owner_auth.py | Read and preview tools remain available without passkey; no multi-user identity model |
| Computer approvals | Exact payload/state-bound one-time approvals, owner grants, revalidation, serialized consumption | src/mia/computer/approvals.py, src/mia/computer/permissions.py, tests/test_broker_enforcement.py | Adapter-specific behavior must still be verified on the target OS |
| Filesystem | Configured roots, traversal/symlink/credential refusal, backup and recovery | src/mia/computer/filesystem.py, src/mia/computer/fileops.py, tests/test_computer_filesystem.py, tests/test_computer_fileops.py | Real Windows junction and long-path behavior needs owner verification |
| Browser | Isolated Playwright profile, URL policy, exact target/page approval, effect verification | src/mia/computer/browser.py, src/mia/computer/browser_actions.py, tests/test_browser_automation.py | Do not import default-browser cookies or bypass CAPTCHA/access controls |
| Desktop/process | Protected PID/window checks, target-bound approvals, read-back verification, stop control | src/mia/computer/desktop_control.py, src/mia/computer/windows_ui.py, src/mia/computer/processes.py, related tests | Real Windows UIA, UAC and security-window behavior is not proven by fakes |
| Disposable code execution | Fresh non-root Docker worker, no network, read-only candidate mount/root, dropped capabilities, bounded resources and JSON contract | src/mia/evolution/sandbox.py, src/mia/evolution/capability_worker.py, tests/test_sandbox.py | One-shot pure-data capability execution, not a general shell or host-computer sandbox; Docker/kernel isolation remains a dependency |
| Companion identity | Ed25519 device identity, short-lived scoped sessions, revocation and narrow opt-in remote sync | src/mia/security/pairing.py, src/mia/security/sync.py, tests/test_companion_pairing.py, tests/test_companion_sync.py | Windows client currently uses heartbeat only; it is not a computer-action relay |
| Stop and recovery | Stop-all, revocation and rejection remain available without passkey; interrupted actions are not auto-retried | Computer, workflow, companion and approval tests | One end-to-end owner drill across all adapters remains outstanding |

## Regression suite inventory

Run from the repository root after installing the project development dependencies:

```text
python -m pytest tests/test_local_guard.py tests/test_route_capabilities.py
python -m pytest tests/test_owner_auth.py tests/test_companion_pairing.py tests/test_companion_sync.py
python -m pytest tests/test_broker_enforcement.py tests/test_broker_model_tools.py tests/test_brain_owner_permissions.py
python -m pytest tests/test_computer_filesystem.py tests/test_computer_fileops.py tests/test_computer_tasks.py
python -m pytest tests/test_browser_automation.py tests/test_browser_policy.py tests/test_desktop_control.py
python -m pytest tests/test_windows_ui.py tests/test_computer_process_apps.py tests/test_computer_terminal.py
python -m pytest tests/test_sandbox.py tests/test_workflows.py tests/test_workflows_page.py
```

These commands are a suggested focused security pass. They have **not** been run as part of this handoff. A full suite and static checks are still required before a release claim.

## Owner verification on the real Windows machine

Perform these checks with non-production test data and a disposable test folder. Do not use real credentials, sensitive files, payment pages or important processes.

1. **Passkey enrollment**
   - Start Mia locally and create the initial short-lived bootstrap code using the host CLI command `mia-owner-auth bootstrap`.
   - Open `http://localhost:<port>/owner-auth` (not `127.0.0.1`) and register a Windows Hello or security-key passkey.
   - Register a second recovery passkey where possible. Store recovery instructions securely; never paste private keys or bootstrap codes into chat.
   - Log out, authenticate again, close/reopen the browser and verify the session behavior. Confirm logout revokes the session.
2. **Owner boundary**
   - With no session, confirm approval decisions, high-risk permission administration, workflow run/retry/reconcile and companion administration reads fail closed with an owner-auth response.
   - Confirm the chat/approval UI offers the passkey route without executing the blocked action.
   - Confirm stop-all, permission revocation, device revocation, approval rejection and workflow pause/cancel remain usable as safety reductions.
3. **Approval integrity**
   - Preview a harmless write into the disposable test folder, change the target/content after preview and confirm the old approval cannot execute.
   - Confirm a single approval cannot be consumed twice or reused for a different action.
   - Confirm rejection and cancellation revoke pending or approved-but-unconsumed approvals.
4. **Filesystem**
   - Confirm `..`, symlink/junction escapes, credential-like files, private keys and Mia recovery metadata are refused.
   - Confirm overwrite creates a recovery backup and delete moves data to the recovery area rather than permanently deleting it.
5. **Browser**
   - Use a local test page only. Confirm navigation to loopback/private/reserved targets is blocked by the configured policy.
   - Change the page or target after preview and confirm the approval becomes stale. Confirm the browser profile does not expose the owner's default-browser cookies.
6. **Desktop and processes**
   - Use a disposable Notepad window and a harmless child process. Confirm target changes invalidate approvals and protected/security/UAC windows are refused.
   - Confirm a terminated process is verified by PID plus process start identity, and Mia's own/parent/protected processes cannot be terminated.
7. **Sandbox**
   - Confirm Docker Desktop is using its Linux container engine and the expected local image is built.
   - Run only a harmless pure-data test capability. Confirm the worker has no network, no writable host mount, non-root identity and bounded resources.
   - Confirm unavailable Docker, timeout, hash mismatch, malformed result and forbidden source are failures—not success or activation.
8. **Companion**
   - Pair a test device on the local host, confirm heartbeat and visible status, then revoke it and confirm its token stops working.
   - If testing remote sync, use a separate trusted network and direct HTTPS with the exact configured hostname. Do not expose the general computer API.
   - Do not treat heartbeat or sync as remote desktop/computer control.
9. **Emergency stop**
   - Start a harmless long-running test action, invoke stop-all, and verify no new side effect begins.
   - Verify stop/revoke/reject controls work while logged out. Resume only after confirming the test action is stopped or safely reconciled.

## Explicitly out of scope / not claimed

- No guarantee against a compromised Windows account, malicious local administrator, kernel/Docker escape or stolen active browser session.
- No arbitrary unrestricted shell for Brain.
- No general remote computer-control relay through the Windows companion.
- No automatic capability activation, privilege escalation or self-approval.
- No claim that heuristic AST checks prove generated code safe.
- No claim that the Windows-specific paths are verified until the owner runs the checks above.

## Handoff status

- Source-backed task-board reconciliation for COMP-05 through COMP-08 is complete.
- Owner passkey implementation and workflow approval implementation are committed on `main`.
- This handoff plan is documented; focused automated regression execution, full-suite/static checks, actual Windows checks and the cross-adapter stop/revocation drill remain open under MIA-COMP-11.
