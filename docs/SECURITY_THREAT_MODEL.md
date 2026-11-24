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
4. **Network attacker.** Mia binds to loopback only (`mia.__main__` host check, declared). The
   guard below re-checks the Host header, so a changed bind does not by itself expose it to named hosts.

## Trust boundaries
Browser page → loopback HTTP/WebSocket API → stores and device adapters. The first boundary had no
defence before this task.

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
and recovery path. **tested**: `tests/test_route_capabilities.py` enumerates the real routes and fails if
a mutating route is unclassified, a rule matches nothing, device control has no approval class, or a
stop control is approval-gated. Classifications are **declared** (assigned from route names), not
proven by tracing each handler; the enforcement that a handler really demands the approval is COMP-04.

## Approval classes
`none` (direct owner action), `preview_then_execute` (execute requires prior preview/approval record), `grant_gated` (409 unless an active owner grant covers it; e.g. browser fill),
`owner_confirm` (destructive or wide; UI confirms), `always_allowed` (stop controls: never blocked).

## Emergency stop and revocation
Stop-all, emergency-stop, wake-word stop, autonomy pause and permission revoke-all are classified
`always_allowed`. Whether each actually halts in-flight work is covered by their own feature tests;
the end-to-end "stop everything" drill is OWNER_VERIFICATION_REQUIRED.

## Open items
Per-caller auth / device identity (LIFE-13), enforcement broker (COMP-04), optional extra allowed
hosts for remote access (intentionally not supported), handler-level proof of each classification.
