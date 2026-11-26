# Mia identity and remote trust model

**Status:** source-backed current-state contract, 2026-11-26  
**Related work:** COMP-02 device pairing, LIFE-13 companion sync, COMP-04 owner approval enforcement.

## 1. Two distinct identities

### Owner authority

Mia still runs as a single-owner local workspace, but high-risk owner actions in the production
`local_guard=True` deployment now require a WebAuthn passkey session (MIA-COMP-04-OWNER).

- The first passkey is enrolled only after the owner runs `mia-owner-auth bootstrap` on the Mia
  host and enters the short-lived, one-use code in `/owner-auth`.
- The relying-party ID is `localhost`; the browser must use the exact `http://localhost:<port>`
  or `https://localhost:<port>` origin. `127.0.0.1` and other hostnames are refused for owner
  passkey ceremonies and protected owner requests.
- Registration and authentication require WebAuthn user verification. Private passkey keys stay
  with the authenticator; Mia stores credential public keys, challenge state and sign counters.
- Successful authentication issues an eight-hour HttpOnly, SameSite=Strict cookie. Mia stores
  only a hash of the session token, and logout revokes the corresponding session.
- Approval decisions, high-risk previews/execution, owner permission administration, pending
  approval review and companion administration reads are guarded by that owner session.
- Brain write-mode tools also require the live owner-session context, even when a long-lived
  permission grant exists. Read-only and preview tools remain available; previewed actions still
  need owner approval before execution.

Safety reductions deliberately remain reachable without a passkey: stop-all/emergency stop,
permission revocation, companion-device revocation and approval rejection. This allows the owner
to reduce authority when authentication is unavailable.

This is local single-owner authentication, not a multi-user account system. A compromised browser
session or malware running as the same Windows user remains a serious threat; keep the host and
browser profile protected. Retain more than one passkey where practical. Mia currently has no
self-service reset after every registered passkey is lost; do not delete the owner-auth database
as a casual recovery step.
 
### Paired device identity

A companion device has a separate Ed25519 keypair:

- The private key stays on the companion.
- The owner creates a short-lived, one-time pairing code locally.
- Pairing binds the public key to a device ID and owner-selected scopes.
- Authentication uses a one-time challenge and Ed25519 signature.
- Mia issues a short-lived bearer session; only a hash of the token is stored.
- Each request checks the device status and required scope.
- Revocation marks the device revoked and deletes its active sessions/challenges.

Source: `src/mia/security/pairing.py`; protocol tests: `tests/test_companion_pairing.py`.

The first Windows companion client is now implemented in `src/mia/companion/` (COMP-03). It generates and retains the Ed25519 private key on the device, stores that key and its bearer token using Windows CurrentUser DPAPI, renews short-lived sessions with a fresh signed challenge, sends a heartbeat, and supports bounded reconnect. It initiates outbound HTTP(S) requests only and exposes no inbound listener. First-time enrollment is loopback-only. The owner can configure an already-paired device to use an allowlisted HTTPS hostname; if that remote session expires while offline, renewal must be performed from the loopback Mia host. The Windows logon task runs as the current interactive user with Limited privilege and no stored password.

The owner Companion Devices page is served at `/companion`. It creates one-time codes, displays paired-device metadata and revokes devices. It does not create an authenticated owner session; the owner-authority limitation below still applies.

## 2. Device scopes

The device may hold only these explicitly granted scopes:

- `companion.heartbeat`
- `companion.observe`
- `companion.act`
- `companion.sync.read`
- `companion.sync.write`

A device cannot grant itself scopes, create pairing codes, approve owner actions, revoke devices or access owner administration. Read and write sync scopes are separate. The sync service exposes only `companion_state` and `preferences`, not conversations, personal memory, files, credentials or governance records. The current Windows companion client actively uses `companion.heartbeat`; observe/act/sync scope labels do not imply that remote action execution or automatic sync is enabled in that client release.

Source: `src/mia/security/pairing.py`, `src/mia/security/companion_api.py`, `src/mia/security/sync.py`, `src/mia/companion/agent.py`.

## 3. Transport and remote boundary

Default deployment is loopback-only. Remote access is an explicit opt-in and requires all of:

1. `MIA_REMOTE_ACCESS_ENABLED=true`.
2. Exact `MIA_REMOTE_ALLOWED_HOSTS` entries (no wildcard or URL).
3. `local_guard=True`.
4. Direct HTTPS/TLS as seen by the ASGI scope; forwarded-proto headers are not trusted.
5. A live `mia-dev_...` device bearer token.
6. One of the exact companion route/method pairs in `LoopbackGuard.REMOTE_COMPANION_ROUTES`.

Remote pairing-code creation, first-time pairing, owner administration and all non-companion APIs remain denied. Remote session renewal requires an existing live token and a fresh Ed25519 challenge signature. Browser-originated requests are rejected.

Source: `src/mia/security/local_guard.py`, `src/mia/security/companion_api.py`, `src/mia/observatory/api.py`; protocol and setup: `docs/architecture/COMPANION_SYNC.md`.

## 4. Revocation, replay and conflicts

- Pairing codes are one-time and expire.
- Challenges are single-use and short-lived.
- Session tokens expire and are rechecked against device status on every request.
- Revocation deletes sessions and challenges immediately.
- Sync pushes require a per-device idempotency batch ID; replay returns the original result.
- Sync writes use base versions. A conflict returns HTTP 409 and applies none of the batch.
- Deletes create tombstones so other paired devices can observe them.
- Payloads are size-bounded and credential-like fields/values are rejected.

Sources: `src/mia/security/pairing.py`, `src/mia/security/sync.py`; tests: `tests/test_companion_pairing.py`, `tests/test_companion_sync.py`.

## 5. Trust-boundary summary

```text
Owner on Mia host
  └─ localhost owner routes + WebAuthn passkey session
       ├─ pairing-code creation / high-risk administration (passkey required)
       ├─ high-risk approvals and Brain write-mode tools (passkey required)
       └─ safety reductions (stop/revoke/reject remain reachable)

Paired companion
  └─ Ed25519 device identity
       └─ scoped short-lived device session
            ├─ local companion routes
            └─ optional remote HTTPS allowlisted routes
                 └─ heartbeat / whoami / sync / signed session renewal

Device token ─X─ owner routes, approval, pairing administration
Remote request ─X─ pairing, owner APIs, browser routes, arbitrary Mia APIs
```

## 6. Remaining security gap

The final approval HTTP boundary and Brain write-mode broker now require a live WebAuthn owner
session. Remaining limits are: the whole local API is not a multi-user authenticated service;
read-only and preview routes intentionally remain usable without a passkey; safety reductions
remain unauthenticated by design; an active same-user browser session or compromised host can
still be abused; and recovery after all owner passkeys are lost is currently an offline/manual
host-recovery concern. Do not expose Mia's owner API to a network or another user account.

The WebAuthn ceremony and owner-session tests have been authored but not executed in this
development pass. Real Windows Hello/security-key verification, browser-origin behavior, cookie
lifecycle and local-host recovery remain owner verification. Existing TLS certificate,
firewall/DNS, Windows CurrentUser DPAPI, Task Scheduler/logon behavior, physical companion pairing
and browser accessibility checks also remain owner verification.
