"""Local-only observatory and optional AI gateway API.

Authentication must be implemented before any remote network binding.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from mia.gateway.contracts import ModelRequest, PermanentProviderError, TransientProviderError
from mia.gateway.factory import build_default_registry, paid_route_policy
from mia.gateway.service import ModelGateway
from mia.voice.stt import FasterWhisperTranscriber, SpeechToTextProvider

from .store import ObservatoryStore


class EventInput(BaseModel):
    category: str = Field(min_length=1, max_length=80)
    event_type: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=500)
    outcome: str = Field(default="recorded", max_length=80)
    day_index: int | None = Field(default=None, ge=0)
    experiment_id: str | None = None
    layer_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class MetricInput(BaseModel):
    day_index: int = Field(ge=0)
    metric_name: str = Field(min_length=1, max_length=100)
    value: float
    unit: str = Field(default="score", max_length=40)
    benchmark_id: str | None = None
    experiment_id: str | None = None
    source_event_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class ExperimentPreparation(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    planned_days: int = Field(default=30, ge=1, le=365)
    baseline_manifest: dict[str, Any]


class DailySnapshotInput(BaseModel):
    day_index: int = Field(ge=1)
    source_commit: str = Field(min_length=7, max_length=64)
    snapshot: dict[str, Any]


class ChatInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=100_000)
    task_type: str = Field(default="chat", min_length=1, max_length=40)
    max_output_tokens: int = Field(default=1024, ge=1, le=8192)
    required_capabilities: set[str] = Field(default_factory=set)


def create_app(
    store: ObservatoryStore | None = None,
    gateway: ModelGateway | None = None,
    transcriber: SpeechToTextProvider | None = None,
) -> FastAPI:
    data_dir = Path(os.getenv("MIA_DATA_DIR", "./data"))
    observatory = store or ObservatoryStore(data_dir / "mia-observatory.sqlite3")
    model_gateway = gateway or ModelGateway(build_default_registry())
    app = FastAPI(
        title="Mia Evolution Observatory",
        version="0.1.0",
        description="Local-first, evidence-backed evolution telemetry and AI gateway.",
    )
    app.state.observatory = observatory
    app.state.gateway = model_gateway
    speech_to_text = transcriber or FasterWhisperTranscriber(data_dir=data_dir)
    app.state.transcriber = speech_to_text

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "mia"}

    @app.get("/assistant")
    def assistant() -> FileResponse:
        page = Path(__file__).resolve().parents[1] / "assistant.html"
        if not page.exists():
            raise HTTPException(status_code=404, detail="Assistant asset not found")
        return FileResponse(page, media_type="text/html")

    @app.get("/")
    def dashboard() -> FileResponse:
        page = Path(__file__).with_name("dashboard.html")
        if not page.exists():
            raise HTTPException(status_code=404, detail="Dashboard asset not found")
        return FileResponse(page, media_type="text/html")

    @app.get("/api/observatory/summary")
    def summary() -> dict[str, Any]:
        return observatory.summary()

    @app.get("/api/observatory/events")
    def events(limit: int = 100) -> dict[str, Any]:
        return {"items": observatory.list_events(limit)}

    @app.get("/api/observatory/metrics")
    def metrics(metric_name: str | None = None) -> dict[str, Any]:
        return {"items": observatory.list_metrics(metric_name)}

    @app.post("/api/observatory/events", status_code=201)
    def add_event(item: EventInput) -> dict[str, str]:
        return {"event_id": observatory.record_event(**item.model_dump())}

    @app.post("/api/observatory/metrics", status_code=201)
    def add_metric(item: MetricInput) -> dict[str, str]:
        return {"sample_id": observatory.record_metric(**item.model_dump())}

    @app.get("/api/experiments")
    def experiments() -> dict[str, Any]:
        return {"items": observatory.list_experiments()}

    @app.get("/api/experiments/{experiment_id}/snapshots")
    def snapshots(experiment_id: str) -> dict[str, Any]:
        known_ids = {
            item["experiment_id"] for item in observatory.list_experiments()
        }
        if experiment_id not in known_ids:
            raise HTTPException(status_code=404, detail="Experiment not found")
        return {"items": observatory.list_snapshots(experiment_id)}

    @app.post("/api/experiments/prepare", status_code=201)
    def prepare_experiment(item: ExperimentPreparation) -> dict[str, str]:
        try:
            return observatory.prepare_experiment(
                name=item.name,
                baseline_manifest=item.baseline_manifest,
                planned_days=item.planned_days,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/experiments/{experiment_id}/start")
    def start_experiment(experiment_id: str) -> dict[str, str]:
        try:
            observatory.start_experiment(experiment_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"experiment_id": experiment_id, "status": "running"}

    @app.post("/api/experiments/{experiment_id}/snapshots", status_code=201)
    def add_snapshot(
        experiment_id: str,
        item: DailySnapshotInput,
    ) -> dict[str, str]:
        try:
            snapshot_id = observatory.record_daily_snapshot(
                experiment_id=experiment_id,
                day_index=item.day_index,
                source_commit=item.source_commit,
                snapshot=item.snapshot,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"snapshot_id": snapshot_id}

    @app.get("/api/voice/status")
    def voice_status() -> dict[str, Any]:
        try:
            import faster_whisper  # noqa: F401
            installed = True
        except ImportError:
            installed = False
        return {
            "speech_to_text": "ready" if installed else "install_voice_extra",
            "model": getattr(speech_to_text, "model_size", "custom"),
            "text_to_speech": "browser_speech_synthesis",
            "always_listening": False,
        }

    @app.post("/api/voice/transcribe")
    async def transcribe_voice(request: Request) -> dict[str, Any]:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 15 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Audio clip exceeds 15 MB")
        content_type = request.headers.get("content-type", "")
        if not content_type.startswith("audio/"):
            raise HTTPException(status_code=415, detail="Expected an audio upload")

        chunks = []
        total_bytes = 0
        async for chunk in request.stream():
            total_bytes += len(chunk)
            if total_bytes > 15 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="Audio clip exceeds 15 MB")
            chunks.append(chunk)
        audio_bytes = b"".join(chunks)
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="Audio clip is empty")

        filename = request.query_params.get("filename", "recording.webm")
        language = request.query_params.get("language") or None
        try:
            result = await run_in_threadpool(
                speech_to_text.transcribe,
                audio_bytes,
                filename=filename,
                language=language,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        text = str(result.get("text") or "").strip()
        observatory.record_event(
            category="voice",
            event_type="transcription",
            summary="Voice clip transcribed locally",
            outcome="success" if text else "empty_transcription",
            payload={
                "language": result.get("language"),
                "language_probability": result.get("language_probability"),
                "audio_bytes": total_bytes,
                "audio_retained": False,
            },
        )
        return {
            "text": text,
            "language": result.get("language"),
            "language_probability": result.get("language_probability"),
            "audio_retained": False,
        }

    @app.get("/api/gateway/providers")
    async def providers() -> dict[str, Any]:
        results = []
        for provider in model_gateway.providers.values():
            try:
                is_healthy = await provider.health()
                state = "online" if is_healthy else "offline"
            except RuntimeError:
                state = "gateway_extra_not_installed"
            results.append({"provider_id": provider.provider_id, "status": state})
        return {"items": results}

    @app.get("/api/gateway/models")
    async def models() -> dict[str, Any]:
        try:
            items = await model_gateway.discover_models()
        except TransientProviderError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "items": [
                {
                    "provider_id": item.provider_id,
                    "model_id": item.model_id,
                    "capabilities": sorted(item.capabilities),
                    "context_window": item.context_window,
                    "free_tier": item.free_tier,
                    "healthy": item.healthy,
                }
                for item in items
            ]
        }

    @app.post("/api/gateway/chat")
    async def chat(item: ChatInput) -> dict[str, Any]:
        try:
            candidates = await model_gateway.discover_models()
            result = await model_gateway.complete(
                ModelRequest(
                    task_type=item.task_type,
                    prompt=item.prompt,
                    estimated_input_tokens=max(1, len(item.prompt) // 4),
                    max_output_tokens=item.max_output_tokens,
                    required_capabilities=frozenset(item.required_capabilities),
                ),
                candidates,
                paid_route_policy(),
            )
        except TransientProviderError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except PermanentProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        observatory.record_event(
            category="ai_gateway",
            event_type="completion",
            summary=f"Completed {item.task_type} request with {result.provider_id}/{result.model_id}",
            outcome="success",
            payload={
                "provider_id": result.provider_id,
                "model_id": result.model_id,
                "attempted_candidates": list(result.attempted_candidates),
            },
        )
        return {
            "provider_id": result.provider_id,
            "model_id": result.model_id,
            "content": result.content,
            "attempted_candidates": list(result.attempted_candidates),
        }

    return app


app = create_app()
