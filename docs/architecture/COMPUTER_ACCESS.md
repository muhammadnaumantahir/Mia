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
4. Task orchestrator to combine actions and resume safely after failures.
5. Restricted worker process for approved scripts; no arbitrary code in the privileged process.

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
