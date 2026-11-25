# Mia Security Threat Model (MIA-COMP-01, A7)

Scope: Mia's local FastAPI service and the pages it serves. Status of every claim is marked
**verified** (reproduced against the running code), **tested** (covered by an automated test in
this repo) or **declared** (stated design intent, not proven).

## Assets
Owner memory, tasks and identity; device control (browser, terminal, desktop, files, processes);
microphone access; the self-development pipeline (capabilities, experiments); paid-inference budget.

## Attacker model
1. **A web page the owner visits** (malicious or compromised site) that tries to reach Mia through
   the owner's browser: cross-site POST, WebSocket, or DNS rebinding.
2. **Another local process or user** on the same machine. *Out of scope for now:* Mia has no
   per-caller authentication, so any local process can call the API. Mitigation is the
   permission broker and device identity (MIA-COMP-04, MIA-LIFE-13). Declared gap.
3. **Generated or self-developed code** acting beyond its grant. Covered by the sandbox and
   governance layers, not by this document.
4. **Network attacker.** Mia binds to loopback by default. The only opt-in remote surface is
   the exact companion route/method allowlist, protected by direct HTTPS, exact Host allowlisting,
   a live device bearer token and device scopes. Pairing and owner routes remain loopback-only.

## Trust boundaries
Browser page → loopback HTTP/WebSocket API → stores and device adapters. A paired non-browser
companion may reach only the narrow HTTPS sync/session routes when the owner explicitly enables
remote access; it cannot reach owner administration or general APIs.

## Findings (before this task)
- **verified**: the API answered requests carrying `Host: evil.example` (DNS-rebinding precondition).
- **verified**: mutating requests with a foreign `Origin` were accepted, including body-less POSTs
  that need no CORS preflight: `/api/computer/stop-all`, `/api/computer/resume`,
  `/api/computer/permissions/revoke-all`, `/api/autonomy/pause`. JSON POSTs without a content-type
  match FastAPI's strict content-type check and are rejected, which is a partial, incidental defence.
- **verified**: `WS /api/voice/wake/stream` accepted any Origin.

## Mitigation added
`mia.security.local_guard.LoopbackGuard` (ASGI middleware, on for the production `app`, off by
default in `create_app` so existing in-process tests are unchanged):
- Host must be `127.0.0.1`, `localhost` or `::1` (HTTP and WebSocket) → otherwise 400 / close 1008.
- Mutating methods and WebSockets: an `Origin` header, if present, must equal the request's own
  loopback origin (same host and port); `null` and foreign origins → 403. `Sec-Fetch-Site: cross-site` → 403.
- Requests with no `Origin` (curl, scripts, other local tools) pass. A browser always sends `Origin`
  on cross-site POSTs, so this does not weaken the browser defence.
- **tested**: `tests/test_local_guard.py`. Limits: it is transport hygiene, not authorization;
  cross-site *reads* with a loopback Host are not blocked (reads have no side effects, but they can be
  read only by same-origin pages because Mia sends no CORS allow headers - declared, not tested here).

## Capability inventory
`mia.security.capabilities` classifies every mutating route by side effect, approval class, scope
and recovery path. At app construction, `bind_route_capabilities` binds each mutating route to its
actual callable Mia handler identity and declared capability. App construction fails closed if a
mutating route has no matching rule, a non-Mia/missing handler, or cannot store its binding.
`tests/test_route_capabilities.py` covers route classification, live handler identity bindings,
dead rules, device-control approval classes and always-available stop controls. **Test execution for
the new handler-binding coverage remains OWNER_VERIFICATION_REQUIRED.** The classification is still
a declaration derived from route/method patterns; binding proves which handler is registered, not
that the handler enforces the approval class. That enforcement remains COMP-04.
## Approval classes
`none` (direct owner action), `preview_then_execute` (execute requires prior preview/approval record), `grant_gated` (409 unless an active owner grant covers it; e.g. browser fill),
`owner_confirm` (destructive or wide; UI confirms), `always_allowed` (stop controls: never blocked).

## Emergency stop and revocation
Stop-all, emergency-stop, wake-word stop, autonomy pause and permission revoke-all are classified
`always_allowed`. Whether each actually halts in-flight work is covered by their own feature tests;
the end-to-end "stop everything" drill is OWNER_VERIFICATION_REQUIRED.

## Open items
Per-caller auth / device identity (LIFE-13), enforcement of declared approval classes inside handlers
(COMP-04), and optional extra allowed hosts for remote access (intentionally not supported).
Route-to-handler binding is now fail-closed at app construction; it does not replace COMP-04 enforcement.

## Update 2026-11-25: companion identity (MIA-COMP-02)
- **Added (tested):** `mia.security.pairing` gives a paired companion its own revocable identity: Ed25519 keys (private key stays on the device), one-time hashed pairing codes with lockout, single-use challenges, scoped 15-minute sessions, instant revocation. `DeviceConfinementGuard` confines any device token to `/api/companion/*`, so a compromised companion cannot approve its own requests, grant permissions, pair or revoke devices.
- **Scopes a device can ever hold:** `companion.heartbeat`, `companion.observe`, `companion.act`, `companion.sync.read` and `companion.sync.write`. Approval, permission grants and pairing are not grantable by design.
- **Still not covered (declared):** the owner's own browser is not authenticated, so a local process that presents no device token can still call owner routes; no real companion has been exercised (COMP-03, owner verification).


## Update 2026-11-25: authenticated companion sync (MIA-LIFE-13)

- **Implemented (tests authored; execution OWNER_VERIFICATION_REQUIRED):** device-scoped `companion.sync.read` and `companion.sync.write` scopes, versioned push/pull, atomic conflict rejection, tombstones, idempotent batches, payload/namespace limits and immediate revocation checks.
- **Remote transport is opt-in and narrow:** disabled by default; exact Host allowlist, direct HTTPS scheme, bearer device token, no browser Origin/Sec-Fetch metadata, and exact companion route/method allowlist. First-time pairing and owner administration remain loopback-only. Remote session renewal requires an existing live token plus an Ed25519 signature.
- **Data boundary:** only `companion_state` and `preferences` JSON namespaces sync. Conversations, files, personal memory, credentials and governance data are excluded. Payloads are credential-screened and bounded.
- **Owner verification remains:** real TLS certificate, firewall/DNS, separate-network device connection, reconnect/cursor recovery and real-device revocation checks. Direct TLS is required; forwarded-proto headers are not trusted.
- Protocol and operational instructions: `docs/architecture/COMPANION_SYNC.md`.
