"""HTTP surface for companion pairing (MIA-COMP-02).

Owner routes live under ``/api/companion-admin`` and device routes under ``/api/companion``. The
split is deliberate: ``DeviceConfinementGuard`` lets a device token reach only the second prefix,
so a paired (or compromised) companion can never create codes or revoke devices.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from mia.security.pairing import ALLOWED_SCOPES, PairingError, PairingService, ScopeError
from mia.security.sync import CompanionSyncError, CompanionSyncStore


class CodeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(min_length=1, max_length=len(ALLOWED_SCOPES))
    ttl_seconds: int = Field(default=300, ge=30, le=300)


class PairInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=32)
    public_key: str = Field(min_length=1, max_length=64)
    device_name: str = Field(min_length=1, max_length=80)
    signature: str = Field(min_length=1, max_length=128)


class ChallengeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1, max_length=64)


class SessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1, max_length=64)
    challenge_id: str = Field(min_length=1, max_length=64)
    signature: str = Field(min_length=1, max_length=128)


class SyncChangeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
    namespace: Literal["companion_state", "preferences"]
    base_version: int = Field(ge=0)
    payload: dict[str, Any] | None = None
    deleted: bool = False


class SyncPushInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]{16,64}$")
    changes: list[SyncChangeInput] = Field(min_length=1, max_length=50)


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    return authorization.split(" ", 1)[1].strip()


def register_companion_routes(
    app: FastAPI,
    service: PairingService,
    record: Callable[..., Any],
    sync: CompanionSyncStore | None = None,
) -> None:
    def refuse(exc: PairingError) -> HTTPException:
        if isinstance(exc, ScopeError):
            return HTTPException(status_code=403, detail=str(exc))
        if exc.locked:
            return HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "300"})
        return HTTPException(status_code=401, detail=str(exc))

    # ---- owner ------------------------------------------------------------------------------
    @app.post("/api/companion-admin/pairing-codes", status_code=201)
    def create_code(item: CodeInput) -> dict[str, Any]:
        try:
            created = service.create_code(item.label, item.scopes, item.ttl_seconds)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        record(event_type="companion_code_created", summary=f"Pairing code created for {item.label[:60]}",
               outcome="success", payload={"scopes": created["scopes"]})
        return created

    @app.get("/api/companion-admin/devices")
    def devices() -> dict[str, Any]:
        return {"items": service.list_devices(), "allowed_scopes": sorted(ALLOWED_SCOPES)}

    @app.post("/api/companion-admin/devices/{device_id}/revoke")
    def revoke(device_id: str) -> dict[str, bool]:
        changed = service.revoke(device_id)
        if changed:
            record(event_type="companion_device_revoked", summary="A companion device was revoked",
                   outcome="success", payload={"device_id": device_id})
        return {"revoked": changed}

    @app.post("/api/companion-admin/revoke-all")
    def revoke_all() -> dict[str, int]:
        count = service.revoke_all()
        record(event_type="companion_revoke_all", summary=f"All companion devices revoked ({count})",
               outcome="success", payload={"count": count})
        return {"revoked": count}

    # ---- companion (initiates every exchange) ------------------------------------------------
    @app.post("/api/companion/pair", status_code=201)
    def pair(item: PairInput) -> dict[str, Any]:
        try:
            device = service.redeem(item.code, item.public_key, item.device_name, item.signature)
        except PairingError as exc:
            record(event_type="companion_pair_failed", summary="A companion pairing attempt failed",
                   outcome="failure", payload={"locked": exc.locked})
            raise refuse(exc) from exc
        record(event_type="companion_paired", summary=f"Companion paired: {device['name'][:60]}",
               outcome="success", payload={"device_id": device["device_id"], "scopes": device["scopes"]})
        return device

    @app.post("/api/companion/challenge")
    def challenge(
        item: ChallengeInput, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        try:
            # Remote session renewal must prove possession of a still-live token before
            # requesting a fresh Ed25519 challenge. Local first-time pairing may omit it.
            if authorization:
                service.verify_token(_bearer(authorization), touch=True)
            return service.challenge(item.device_id)
        except PairingError as exc:
            raise refuse(exc) from exc

    @app.post("/api/companion/session")
    def session(
        item: SessionInput, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        try:
            if authorization:
                service.verify_token(_bearer(authorization), touch=True)
            return service.open_session(item.device_id, item.challenge_id, item.signature)
        except PairingError as exc:
            record(event_type="companion_auth_failed", summary="A companion authentication failed",
                   outcome="failure", payload={"locked": exc.locked})
            raise refuse(exc) from exc

    @app.get("/api/companion/whoami")
    def whoami(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        try:
            caller = service.verify_token(_bearer(authorization))
        except PairingError as exc:
            raise refuse(exc) from exc
        return {"device_id": caller.device_id, "label": caller.label, "scopes": sorted(caller.scopes)}

    @app.post("/api/companion/heartbeat")
    def heartbeat(authorization: str | None = Header(default=None)) -> dict[str, str]:
        try:
            service.verify_token(_bearer(authorization), scope="companion.heartbeat", touch=True)
        except PairingError as exc:
            raise refuse(exc) from exc
        return {"status": "ok"}


    @app.get("/api/companion/sync/pull")
    def sync_pull(
        since: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=100),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if sync is None:
            raise HTTPException(status_code=503, detail="Companion sync is not configured")
        try:
            caller = service.verify_token(
                _bearer(authorization), scope="companion.sync.read", touch=True
            )
            result = sync.pull(caller.device_id, since=since, limit=limit)
        except PairingError as exc:
            raise refuse(exc) from exc
        except CompanionSyncError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        record(
            event_type="companion_sync_pulled",
            summary=f"Companion sync pull returned {len(result['changes'])} changes",
            outcome="success",
            payload={"device_id": caller.device_id, "since": since, "next_cursor": result["next_cursor"]},
        )
        return result

    @app.post("/api/companion/sync/push", response_model=None)
    def sync_push(
        item: SyncPushInput, authorization: str | None = Header(default=None)
    ) -> Any:
        if sync is None:
            raise HTTPException(status_code=503, detail="Companion sync is not configured")
        try:
            caller = service.verify_token(
                _bearer(authorization), scope="companion.sync.write", touch=True
            )
            result = sync.push(
                caller.device_id, item.batch_id, [change.model_dump() for change in item.changes]
            )
        except PairingError as exc:
            raise refuse(exc) from exc
        except CompanionSyncError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if result["status"] == "conflict":
            record(
                event_type="companion_sync_conflict",
                summary="Companion sync batch rejected due to version conflicts",
                outcome="failure",
                payload={"device_id": caller.device_id, "batch_id": item.batch_id,
                         "conflict_count": len(result["conflicts"])},
            )
            return JSONResponse(status_code=409, content=result)
        record(
            event_type="companion_sync_pushed",
            summary=f"Companion sync batch applied ({len(result['items'])} items)",
            outcome="success",
            payload={"device_id": caller.device_id, "batch_id": item.batch_id,
                     "item_count": len(result["items"])},
        )
        return result
