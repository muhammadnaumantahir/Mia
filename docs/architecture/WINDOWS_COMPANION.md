# Windows companion lifecycle (MIA-COMP-03)

**Status:** implementation integrated on `main`; mocked protocol/lifecycle tests authored. Real Windows installation and physical-device pairing remain owner verification.

## Security and scope

The Windows companion is a separate, outbound-only process. It initiates HTTPS or loopback requests to Mia; it does not open an inbound listener, expose a desktop-control port, execute shell commands, or run generated code.

The companion has a distinct Ed25519 device identity and only the scopes chosen by the owner. Device tokens are accepted only under `/api/companion/*`; they are never owner credentials and do not authorize pairing administration, permission grants or approvals. The current first implementation actively uses the heartbeat scope; observe/act/sync scopes remain bounded protocol capabilities and do not imply that arbitrary actions are currently available.

The optional client is installed with:

```powershell
python -m pip install -e ".[companion]"
```

This adds the Ed25519 `cryptography` package and the `httpx` client. The core Mia install does not require this extra.

## Owner pairing

1. Open Mia locally at `http://127.0.0.1:8765/companion`.
2. Enter a device label, select only the required scopes, and generate a one-time code. The code expires after the selected 1–5 minute period and is shown once.
3. On the Windows machine hosting Mia, execute:

   ```powershell
   mia-companion pair --server http://127.0.0.1:8765 --name "My Windows PC"
   ```

4. Enter the one-time code when prompted. The CLI does not echo the code. Pairing and initial key enrollment are loopback-only by design; this release does not support first-time pairing directly to a remote HTTPS host.
5. Confirm the new device appears in Mia's Companion Devices page. Use `mia-companion status` to inspect the local client state.

After local enrollment, the owner may point the already-paired identity at a remote HTTPS hostname only when remote companion access is enabled on the Mia host:

```powershell
mia-companion set-server --url https://mia.example.com --confirm
```

The hostname must be in the server's exact `MIA_REMOTE_ALLOWED_HOSTS` allowlist, the connection must use valid direct TLS, and the current device token must still be live. This command only changes the outbound destination; it does not pair a new device or widen its scopes.

The client generates an Ed25519 private key locally. The private key and session bearer token are protected with Windows CurrentUser DPAPI before they are written to `%LOCALAPPDATA%\Mia\Companion\device.json`. No plaintext fallback is permitted on other platforms. The public key, device label, scopes and server URL are not secrets and are stored as ordinary metadata. Runtime status and logs are stored under `%LOCALAPPDATA%\Mia\Companion\runtime\`; they do not contain the private key or bearer token.

## Lifecycle commands

```powershell
mia-companion status
mia-companion start
mia-companion stop
mia-companion set-server --url https://mia.example.com --confirm
mia-companion install --confirm
mia-companion update --confirm
mia-companion uninstall --confirm
mia-companion forget --confirm
```

- `start` runs the companion in the current user's session. If it is already running, Mia reports the existing status.
- `stop` writes a stop request and waits for the companion to exit at its next control-loop checkpoint. It does not force-kill the process. A timeout is reported rather than silently terminating the process.
- `install --confirm` creates a visible Windows Task Scheduler logon task named **Mia Companion** for the current interactive user. The ScheduledTasks principal is `Interactive` at `Limited` run level. No password is stored, no SYSTEM principal is used, and no elevation is requested. It runs when that user logs in.
- `update --confirm` gracefully stops and restarts the currently installed companion after the Mia package has been updated. It does not download or self-update Mia.
- `uninstall --confirm` stops the process and removes only the named Mia Companion task. If the process does not stop gracefully, the task is retained.

Installation, restart/update and removal all require the explicit `--confirm` flag. There is no silent persistence or privilege escalation.

## Session renewal and reconnect

The companion sends a heartbeat every 60 seconds by default. It renews the 15-minute device session when fewer than three minutes remain, using a fresh 30-second challenge and an Ed25519 signature. Challenges are single-use; captured signatures cannot be replayed. Network failures use bounded exponential backoff (up to five minutes) with jitter. The current process status includes last successful heartbeat, last error, consecutive failures, device ID and scopes, but never a bearer token or private key.

If the device is revoked, Mia refuses the session and heartbeat. The client reports that it was refused/revoked and keeps retrying at the bounded interval; it does not silently re-pair or obtain new scopes. The owner must inspect/revoke the old device in Mia before replacing it.

For a safe replacement:

1. Revoke the old device on the Companion Devices page.
2. Stop the local agent.
3. Run `mia-companion forget --confirm` to remove the local DPAPI-protected identity. This does not revoke the server-side device.
4. Generate a new one-time code in Mia and pair again.

Remote companion requests require direct HTTPS, exact owner-configured allowlisted hosts and an existing live device token. First-time pairing and owner administration remain local-only. If a remote session expires while disconnected, switch the configured URL back to the loopback Mia endpoint, let the client prove its key and renew locally, then set the HTTPS URL again. The client never bypasses token expiry, TLS verification or the host allowlist. The remote companion allowlist and TLS boundary are configured by the server owner as described in [Companion Sync](COMPANION_SYNC.md).

## Owner trust limitation

The Companion Devices page is protected by the current loopback Host/Origin guard, but it is not a cryptographically authenticated owner session. Another process running locally as the same user may still call owner administration endpoints. This is a tracked limitation under `MIA-COMP-04-OWNER`; do not interpret pairing administration as protected against local malware until that work is complete.

## Verification

`tests/test_windows_companion.py` contains mocked tests for URL/transport policy, Ed25519 challenge and pairing signatures, DPAPI-protected identity storage, interrupted first-pair recovery, session renewal, bounded stop/reconnect, Limited Scheduled Task registration, confirmation requirements, no-force-stop behavior and secret-free status. `tests/test_companion_pairing.py` also covers the Companion Devices page and existing pairing/revocation API behavior.

Tests were authored but not executed in this development pass, per the owner's instruction to defer test execution until development is complete. Physical Windows Task Scheduler, DPAPI, logon/restart and real-device checks remain `OWNER_VERIFICATION_REQUIRED`.
