"""Local-only observatory API. Authentication is required before remote binding."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

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


def create_app(store: ObservatoryStore | None = None) -> FastAPI:
    data_dir = Path(os.getenv("MIA_DATA_DIR", "./data"))
    observatory = store or ObservatoryStore(data_dir / "mia-observatory.sqlite3")
    app = FastAPI(
        title="Mia Evolution Observatory",
        version="0.1.0",
        description="Local-first, evidence-backed evolution telemetry.",
    )
    app.state.observatory = observatory

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "mia-observatory"}

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

    return app


app = create_app()
