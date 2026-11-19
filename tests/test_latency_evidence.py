import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from test_gateway_streaming_ledger import (
    StreamingProvider,
    candidate,
    collect,
    gateway_with,
    make_client,
    parse_sse,
)

from mia.gateway.contracts import ModelRequest, RoutePolicy, StreamChunk, TokenUsage
from mia.gateway.ledger import UsageLedger
from mia.observatory.store import ObservatoryStore


class SlowProvider(StreamingProvider):
    def __init__(self, provider_id, first_delay, gap):
        super().__init__(provider_id, [])
        self.first_delay, self.gap = first_delay, gap

    async def complete(self, cand, request):
        await asyncio.sleep(self.first_delay)
        return "whole"

    async def stream(self, cand, request):
        await asyncio.sleep(self.first_delay)
        yield StreamChunk(delta="a")
        await asyncio.sleep(self.gap)
        yield StreamChunk(delta="b")
        yield StreamChunk(finish_reason="stop", usage=TokenUsage(1, 2))


@pytest.mark.asyncio
async def test_stream_reports_first_token_and_total_time():
    events = await collect(gateway_with(SlowProvider("s", 0.05, 0.05)), [candidate("s")])
    final = events[-1]
    assert final.first_token_ms >= 40
    assert final.total_ms >= final.first_token_ms + 40


@pytest.mark.asyncio
async def test_slow_consumer_does_not_inflate_latency():
    gateway = gateway_with(SlowProvider("s", 0.0, 0.0))
    request = ModelRequest(task_type="chat", prompt="hi")
    final = None
    async for event in gateway.stream(request, [candidate("s")], RoutePolicy()):
        await asyncio.sleep(0.15)  # consumer is slow, provider is not
        final = event
    assert final.done and final.total_ms < 100


@pytest.mark.asyncio
async def test_complete_reports_provider_call_time():
    result = await gateway_with(SlowProvider("s", 0.05, 0)).complete(
        ModelRequest(task_type="chat", prompt="hi"), [candidate("s")], RoutePolicy()
    )
    assert result.latency_ms >= 40


def rec(ledger, **kw):
    ledger.record(provider_id="p", model_id="m", task_type="chat", input_tokens=1,
                  output_tokens=1, estimated_input_tokens=1, estimated_output_tokens=1,
                  cost_usd=0.0, free_tier=True, data_local=True, **kw)


def test_ledger_stores_valid_latency_only_and_computes_stats(tmp_path):
    ledger = UsageLedger(tmp_path / "u.sqlite3")
    for ms in (10, 20, 30, 40, 100):
        rec(ledger, total_ms=ms, first_token_ms=ms / 2)
    for bad in (float("nan"), float("inf"), -1.0, True):
        rec(ledger, total_ms=bad, first_token_ms=bad)
    rec(ledger)  # no measurement at all
    since = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    summary = ledger.window_summary(since, until)
    total = summary["latency_ms"]["total"]
    assert summary["requests"] == 10
    assert total == {"count": 5, "mean": 40.0, "median": 30.0, "p95": 100.0}
    assert summary["latency_ms"]["first_token"]["median"] == 15.0
    empty = ledger.window_summary(until, until)["latency_ms"]["total"]
    assert empty == {"count": 0, "mean": None, "median": None, "p95": None}
    assert ledger.summary(1)["items"][0]["avg_total_ms"] == 40.0


def test_old_ledger_without_latency_columns_is_migrated(tmp_path):
    path = tmp_path / "u.sqlite3"
    UsageLedger(path)
    db = sqlite3.connect(path)
    db.execute("ALTER TABLE usage DROP COLUMN first_token_ms")
    db.execute("ALTER TABLE usage DROP COLUMN total_ms")
    db.commit()
    db.close()
    migrated = UsageLedger(path)
    rec(migrated, total_ms=12.5)
    assert migrated.summary(1)["items"][0]["avg_total_ms"] == 12.5


def test_report_keeps_latency_per_day_instead_of_summing():
    def snap(day, median):
        usage = {"available": True, "requests": 2, "input_tokens": 1, "output_tokens": 1,
                 "cost_usd": 0.0, "estimated_token_rows": 0, "paid_requests": 0,
                 "non_local_requests": 0,
                 "latency_ms": {"total": {"count": 2, "mean": median, "median": median,
                                          "p95": median}, "first_token": {"count": 0}}}
        return {"day_index": day, "evidence": {"activity": {
            "window": {}, "inference_usage": usage,
            "capabilities": {"available": False}, "tasks": {"available": False}}}}

    totals = ObservatoryStore._aggregate_activity([snap(1, 100.0), snap(2, 300.0)])
    usage = totals["inference_usage"]
    assert usage["requests"] == 4  # additive counts are summed
    assert [d["total"]["median"] for d in usage["daily_latency_ms"]] == [100.0, 300.0]
    assert "latency_ms" not in usage
    assert totals["unavailable_sources"] == {"capabilities": [1, 2], "tasks": [1, 2]}


def test_stream_endpoint_returns_and_records_latency(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path))
    client = make_client(tmp_path, SlowProvider("stub-local", 0.02, 0.02))
    events = parse_sse(client.post("/api/gateway/chat/stream", json={"prompt": "Hi"}).text)
    done = events[-1][1]
    assert done["first_token_ms"] >= 15 and done["total_ms"] >= done["first_token_ms"]
    usage = client.get("/api/gateway/usage").json()
    assert usage["items"][0]["avg_total_ms"] >= 30


@pytest.mark.asyncio
async def test_buffered_path_total_ignores_consumer_delay():
    class Plain:
        provider_id = "plain"

        async def health(self):
            return True

        async def list_models(self):
            return []

        async def complete(self, cand, request):
            await asyncio.sleep(0.05)
            return "whole"

    final = None
    async for event in gateway_with(Plain()).stream(
        ModelRequest(task_type="chat", prompt="hi"), [candidate("plain")], RoutePolicy()
    ):
        await asyncio.sleep(0.2)
        final = event
    assert 40 <= final.total_ms < 150 and final.first_token_ms == pytest.approx(final.total_ms)

