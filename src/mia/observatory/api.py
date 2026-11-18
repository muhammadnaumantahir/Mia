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

from mia.computer.approvals import ApprovalError, ApprovalStore
from mia.computer.filesystem import FilesystemAccessError, FilesystemAdapter
from mia.computer.applications import ApplicationLaunchError, ApplicationLauncher
from mia.computer.browser import BrowserAccessError, BrowserController
from mia.gateway.contracts import ModelRequest, PermanentProviderError, TransientProviderError
from mia.gateway.factory import build_default_registry, paid_route_policy
from mia.gateway.service import ModelGateway
from mia.voice.stt import FasterWhisperTranscriber, SpeechToTextProvider
from mia.memory.store import MemoryStore
from mia.knowledge.document_ingestion import DocumentIngestor, DocumentIngestionError

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


class BrowserNavigateInput(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class BrowserClickPreview(BaseModel):
    selector: str = Field(min_length=1, max_length=500)


class BrowserClickExecution(BrowserClickPreview):
    approval_id: str = Field(min_length=32, max_length=32)


class ApplicationLaunchPreview(BaseModel):
    app_id: str = Field(min_length=1, max_length=80)


class ApplicationLaunchExecution(ApplicationLaunchPreview):
    approval_id: str = Field(min_length=32, max_length=32)


class FileWritePreview(BaseModel):
    root_id: str = Field(min_length=1, max_length=40)
    relative_path: str = Field(min_length=1, max_length=1000)
    content: str = Field(max_length=2_000_000)


class FileWriteExecution(FileWritePreview):
    approval_id: str = Field(min_length=32, max_length=32)


class ChatInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=100_000)
    task_type: str = Field(default="chat", min_length=1, max_length=40)
    max_output_tokens: int = Field(default=1024, ge=1, le=8192)
    required_capabilities: set[str] = Field(default_factory=set)
    conversation_id: str | None = Field(default=None, min_length=8, max_length=80)


class MemoryItemInput(BaseModel):
    kind: str = Field(default="note", min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=1_000_000)
    source_uri: str | None = Field(default=None, max_length=2000)
    source_title: str | None = Field(default=None, max_length=300)
    trust_level: str = Field(default="untrusted", max_length=20)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def create_app(
    store: ObservatoryStore | None = None,
    gateway: ModelGateway | None = None,
    transcriber: SpeechToTextProvider | None = None,
    filesystem: FilesystemAdapter | None = None,
    approvals: ApprovalStore | None = None,
    application_launcher: ApplicationLauncher | None = None,
    memory: MemoryStore | None = None,
    browser: BrowserController | None = None,
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
    approval_store = approvals or ApprovalStore(data_dir / "computer-approvals.sqlite3")
    computer_files = filesystem or FilesystemAdapter.from_environment(
        approval_store,
        workspace=Path.cwd() / "workspace",
    )
    app.state.computer_files = computer_files
    app.state.approvals = approval_store
    applications_file = Path(
        os.getenv("MIA_APPLICATIONS_FILE", "config/applications.json")
    )
    app_launcher = application_launcher or ApplicationLauncher.from_json(
        applications_file,
        approval_store,
    )
    app.state.application_launcher = app_launcher
    memory_store = memory or MemoryStore(data_dir / "mia-memory.sqlite3")
    app.state.memory = memory_store
    document_ingestor = DocumentIngestor(computer_files, memory_store)
    app.state.document_ingestor = document_ingestor
    browser_controller = browser or BrowserController(headless=True)
    app.state.browser = browser_controller

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

    @app.get("/api/browser/status")
    def browser_status() -> dict[str, Any]:
        return {
            "running": browser_controller.is_running,
            "current_url": browser_controller.current_url,
            "profile": "isolated-temporary",
            "downloads_enabled": False,
        }

    @app.post("/api/browser/navigate")
    async def browser_navigate(item: BrowserNavigateInput) -> dict[str, Any]:
        try:
            return await browser_controller.navigate(item.url)
        except BrowserAccessError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Browser navigation failed") from exc

    @app.post("/api/browser/click/preview", status_code=201)
    async def browser_click_preview(item: BrowserClickPreview) -> dict[str, str]:
        current_url = browser_controller.current_url
        if not current_url:
            raise HTTPException(status_code=409, detail="Browser has no active page")
        try:
            state_digest = await browser_controller.current_state_digest()
            return approval_store.request(
                action_id="browser.click",
                payload={
                    "url": current_url,
                    "selector": item.selector,
                    "state_sha256": state_digest,
                },
                summary=f"Click selector on {current_url}",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/browser/click/execute")
    async def browser_click_execute(item: BrowserClickExecution) -> dict[str, str]:
        current_url = browser_controller.current_url
        if not current_url:
            raise HTTPException(status_code=409, detail="Browser has no active page")
        try:
            state_digest = await browser_controller.current_state_digest()
            approval_store.consume(
                approval_id=item.approval_id,
                action_id="browser.click",
                payload={
                    "url": current_url,
                    "selector": item.selector,
                    "state_sha256": state_digest,
                },
            )
            result = await browser_controller.click(item.selector)
        except ApprovalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BrowserAccessError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Browser click failed") from exc
        observatory.record_event(
            category="computer",
            event_type="browser_click",
            summary=f"Approved browser interaction on {current_url}",
            outcome="success",
            layer_id="SPS-10",
            payload={"approval_id": item.approval_id, "selector": item.selector},
        )
        return result

    @app.post("/api/browser/close")
    async def browser_close() -> dict[str, str]:
        await browser_controller.close()
        return {"status": "closed"}

    @app.get("/api/computer/apps")
    def computer_apps() -> dict[str, Any]:
        return {"items": app_launcher.list_applications()}

    @app.post("/api/computer/apps/launch/preview", status_code=201)
    def computer_app_launch_preview(item: ApplicationLaunchPreview) -> dict[str, str]:
        try:
            return app_launcher.preview_launch(item.app_id)
        except ApplicationLaunchError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ApprovalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/computer/apps/launch/execute")
    def computer_app_launch_execute(
        item: ApplicationLaunchExecution,
    ) -> dict[str, Any]:
        try:
            result = app_launcher.execute_launch(item.app_id, item.approval_id)
        except ApplicationLaunchError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ApprovalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        observatory.record_event(
            category="computer",
            event_type="application_launch",
            summary=f"Launched approved application {result['display_name']}",
            outcome="success",
            layer_id="SPS-10",
            payload={
                "app_id": item.app_id,
                "process_id": result["process_id"],
                "approval_id": item.approval_id,
            },
        )
        return result

    @app.get("/api/computer/roots")
    def computer_roots() -> dict[str, Any]:
        return {"items": computer_files.list_roots()}

    @app.get("/api/computer/files")
    def computer_files_list(
        root_id: str,
        relative_path: str = ".",
    ) -> dict[str, Any]:
        try:
            return {
                "root_id": root_id,
                "relative_path": relative_path,
                "items": computer_files.list_directory(root_id, relative_path),
            }
        except FilesystemAccessError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/computer/file")
    def computer_file_read(root_id: str, relative_path: str) -> dict[str, str]:
        try:
            content = computer_files.read_text(root_id, relative_path)
        except FilesystemAccessError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (UnicodeDecodeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"root_id": root_id, "relative_path": relative_path, "content": content}

    @app.get("/api/computer/search")
    def computer_search(
        root_id: str,
        query: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        try:
            items = computer_files.search_filenames(root_id, query, limit=limit)
        except FilesystemAccessError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"root_id": root_id, "query": query, "items": items}

    @app.post("/api/computer/files/write/preview", status_code=201)
    def computer_write_preview(item: FileWritePreview) -> dict[str, Any]:
        try:
            return computer_files.preview_write(
                item.root_id,
                item.relative_path,
                item.content,
            )
        except FilesystemAccessError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/computer/approvals")
    def computer_pending_approvals() -> dict[str, Any]:
        return {"items": approval_store.list_pending()}

    @app.post("/api/computer/approvals/{approval_id}/approve")
    def computer_approve(approval_id: str) -> dict[str, str]:
        try:
            return approval_store.approve(approval_id)
        except ApprovalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/computer/files/write/execute")
    def computer_write_execute(item: FileWriteExecution) -> dict[str, Any]:
        try:
            result = computer_files.execute_write(
                approval_id=item.approval_id,
                root_id=item.root_id,
                relative_path=item.relative_path,
                content=item.content,
            )
        except ApprovalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except FilesystemAccessError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        observatory.record_event(
            category="computer",
            event_type="filesystem_write",
            summary=f"Approved write to {result['relative_path']}",
            outcome="success",
            layer_id="SPS-10",
            payload={
                "root_id": item.root_id,
                "sha256": result["sha256"],
                "size_bytes": result["size_bytes"],
                "approval_id": item.approval_id,
            },
        )
        return result

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

    @app.get("/api/conversations")
    def conversations(limit: int = 50) -> dict[str, Any]:
        return {"items": memory_store.list_conversations(limit)}

    @app.get("/api/conversations/{conversation_id}")
    def conversation(conversation_id: str) -> dict[str, Any]:
        session = memory_store.get_conversation(conversation_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return {
            **session,
            "messages": memory_store.get_messages(conversation_id, limit=50),
        }

    @app.delete("/api/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str) -> dict[str, bool]:
        return {"deleted": memory_store.delete_conversation(conversation_id)}

    @app.post("/api/knowledge/ingest", status_code=201)
    def ingest_document(
        root_id: str,
        relative_path: str,
    ) -> dict[str, Any]:
        try:
            result = document_ingestor.ingest(root_id, relative_path)
        except FilesystemAccessError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DocumentIngestionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        observatory.record_event(
            category="knowledge",
            event_type="document_ingested",
            summary=f"Imported {result['title']} into local knowledge",
            outcome="success",
            layer_id="SPS-04",
            payload={
                "item_id": result["item_id"],
                "file_sha256": result["file_sha256"],
                "trust_level": "untrusted",
            },
        )
        return result

    @app.get("/api/memory/items")
    def memory_items(limit: int = 100) -> dict[str, Any]:
        return {"items": memory_store.list_items(limit)}

    @app.get("/api/memory/search")
    def memory_search(q: str, limit: int = 20) -> dict[str, Any]:
        return {"items": memory_store.search(q, limit)}

    @app.post("/api/memory/items", status_code=201)
    def add_memory_item(item: MemoryItemInput) -> dict[str, str]:
        try:
            item_id = memory_store.add_item(**item.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"item_id": item_id}

    @app.delete("/api/memory/items/{item_id}")
    def delete_memory_item(item_id: str) -> dict[str, bool]:
        return {"deleted": memory_store.delete_item(item_id)}

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
            conversation_id = memory_store.ensure_conversation(item.conversation_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        memory_store.append_message(conversation_id, "user", item.prompt)
        conversation_messages = memory_store.get_messages(
            conversation_id,
            limit=12,
        )
        try:
            candidates = await model_gateway.discover_models()
            result = await model_gateway.complete(
                ModelRequest(
                    task_type=item.task_type,
                    prompt=item.prompt,
                    estimated_input_tokens=max(
                        1,
                        sum(len(message["content"]) for message in conversation_messages) // 4,
                    ),
                    max_output_tokens=item.max_output_tokens,
                    required_capabilities=frozenset(item.required_capabilities),
                    messages=tuple(conversation_messages),
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

        memory_store.append_message(conversation_id, "assistant", result.content)
        observatory.record_event(
            category="ai_gateway",
            event_type="completion",
            summary=f"Completed {item.task_type} request with {result.provider_id}/{result.model_id}",
            outcome="success",
            payload={
                "provider_id": result.provider_id,
                "model_id": result.model_id,
                "attempted_candidates": list(result.attempted_candidates),
                "conversation_id": conversation_id,
            },
        )
        return {
            "provider_id": result.provider_id,
            "model_id": result.model_id,
            "content": result.content,
            "attempted_candidates": list(result.attempted_candidates),
            "conversation_id": conversation_id,
        }

    return app


app = create_app()
