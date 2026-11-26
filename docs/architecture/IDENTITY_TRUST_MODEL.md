# Mia identity and remote trust model

**Status:** source-backed current-state contract, 2026-11-26  
**Related work:** COMP-02 device pairing, LIFE-13 companion sync, COMP-04 owner approval enforcement.

## 1. Two distinct identities

### Owner authority

Mia currently runs as a single-owner local workspace. The owner boundary is the local loopback API plus the machine account that runs Mia. There is **not yet a cryptographically authenticated owner-user session** for the browser/API.

Consequences:

- A local process that can reach the loopback API may call owner routes.
- Pairing-code creation, device revocation and owner approvals must not be considered protected against another local process until COMP-04-OWNER is complete.
- A companion device token is never an owner credential and must never be accepted for owner administration or approval.

This is a known limitation, not a claim that loopback is equivalent to owner authentication.

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
  └─ local loopback owner routes
       ├─ pairing-code creation / device revocation
       └─ owner approvals (COMP-04-OWNER: not yet authenticated)

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

The device identity and transport boundary are implemented, but the **owner identity boundary is still local-process trust**. COMP-04-OWNER must authenticate the owner at the final approval decision boundary, reject forged/local-process approval, and remain safe under revocation races. Until then, do not claim owner approvals are protected from another local process.

Real TLS certificate, firewall/DNS, Windows CurrentUser DPAPI, Task Scheduler/logon behavior, physical companion pairing and browser accessibility remain owner verification. Regression tests for companion pairing, Windows client/lifecycle and sync/remote behavior have been authored but not executed in this development pass.
