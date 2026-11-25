# Companion sync and remote transport (MIA-LIFE-13)

## Security defaults

- Remote access is **disabled by default**.
- Pairing-code creation and first-time pairing remain loopback-only.
- Remote requests require direct HTTPS/TLS, an exact Host allowlist entry and a device bearer token.
- Remote browser requests are rejected. The companion is a non-browser client; `Origin` and `Sec-Fetch-Site` headers are not accepted.
- Remote access is limited to the exact companion routes listed below. Owner APIs and pairing administration are never remotely exposed.
- A device token is not an owner identity. `DeviceConfinementGuard` continues to confine device credentials to `/api/companion/*`.
- Session renewal requires an existing live bearer token plus a fresh Ed25519 signature over a one-time challenge.
- Every sync request checks the current device status and requested scope. Revocation invalidates active sessions immediately.

## Enabling remote transport

Set the following only when a public or private DNS name is protected by a valid TLS certificate:

```dotenv
MIA_REMOTE_ACCESS_ENABLED=true
MIA_REMOTE_ALLOWED_HOSTS=mia.example.com
```

`MIA_REMOTE_ALLOWED_HOSTS` is a comma-separated list of exact Host header values. Wildcards, URLs, paths and user-info are rejected. Include a non-default port when it appears in the Host header.

Run the ASGI server with direct TLS, for example:

```bash
uvicorn mia.observatory.api:app --host 0.0.0.0 --port 8443 \
  --ssl-certfile /path/to/fullchain.pem \
  --ssl-keyfile /path/to/privkey.pem
```

The app checks the ASGI request scheme and does not trust `X-Forwarded-Proto`. TLS termination at an untrusted reverse proxy is not supported. `local_guard=True` is mandatory when remote access is enabled. Without these settings, Mia remains loopback-only.

## Remotely reachable routes

Only these exact method/path pairs are permitted off-loopback, and all require an existing `Authorization: Bearer mia-dev_...` token:

| Method | Path | Additional authorization |
|---|---|---|
| POST | `/api/companion/challenge` | Existing live device session; used for renewal |
| POST | `/api/companion/session` | Existing live device session and Ed25519 challenge signature |
| GET | `/api/companion/whoami` | Valid device session |
| POST | `/api/companion/heartbeat` | `companion.heartbeat` scope |
| GET | `/api/companion/sync/pull` | `companion.sync.read` scope |
| POST | `/api/companion/sync/push` | `companion.sync.write` scope |

The remote guard denies all other routes. Pairing-code creation, first-time pairing, device revocation and owner administration remain local-only.

## Sync contract

The sync service is an owner-shared, versioned state log for two small namespaces only:

- `companion_state`
- `preferences`

It does **not** sync conversations, personal memory, files, credentials, governance records or arbitrary paths. Payloads are JSON objects; credential-like keys and values are rejected.

- Push: `POST /api/companion/sync/push` with a client-generated idempotency `batch_id` and 1–50 changes.
- Pull: `GET /api/companion/sync/pull?since=<cursor>&limit=<1..100>`.
- Each item uses optimistic concurrency: the client supplies `base_version`; a mismatch returns HTTP 409 with current version metadata. No partial batch is applied.
- Successful writes increment the item version and append a globally ordered change event. Deletes produce tombstones.
- Replaying a batch ID returns the original result and does not apply it twice. Idempotency records are retained for 30 days; stale base versions still prevent reapplying an expired batch.
- Payloads are limited to 8 KiB; item IDs to 80 characters; batches to 50 changes; current items to 2,000; event history to 20,000; pull pages to 100 events.
- When the bounded history/item limit is reached, new writes fail closed and require owner maintenance rather than silently deleting unacknowledged changes.

The SQLite sync store is local to the Mia instance. Remote transport is encrypted by HTTPS/TLS; this initial sync store is not a general encrypted-at-rest vault and must contain only non-secret companion state/preferences.

## Owner verification still required

- Configure and validate a real TLS certificate and firewall/DNS.
- Pair a real device locally with only the scopes it needs.
- Verify HTTPS sync and signed session renewal from a separate network.
- Confirm HTTP, unlisted hosts, browser-origin requests, pairing and owner-admin routes are rejected remotely.
- Revoke the device and verify all sync/session requests fail immediately.
- Exercise conflict, batch replay, offline reconnect and cursor recovery on a real companion.
