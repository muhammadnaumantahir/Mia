import json

import pytest
from fastapi.testclient import TestClient

from mia.gateway.contracts import (
    ModelCandidate,
    ModelRequest,
    RoutePolicy,
    StreamChunk,
    TokenUsage,
    TransientProviderError,
)
from mia.gateway.factory import apply_daily_cap
from mia.gateway.ledger import UsageLedger
from mia.gateway.registry import ProviderRegistry
from mia.gateway.routing import actual_cost_usd
from mia.gateway.service import ModelGateway
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


def candidate(provider="p", free=True, inp=None, out=None, reliability=0.5):
    return ModelCandidate(
        provider_id=provider,
        model_id="m",
        capabilities=frozenset({"chat"}),
        context_window=8192,
        free_tier=free,
        healthy=True,
        enabled=True,
        reliability_score=reliability,
        input_cost_per_million_usd=inp,
        output_cost_per_million_usd=out,
    )


class StreamingProvider:
    def __init__(self, provider_id, chunks, fail_after=None, fail_before=False):
        self.provider_id = provider_id
        self.chunks = chunks
        self.fail_after = fail_after
        self.fail_before = fail_before

    async def health(self):
        return True

    async def list_models(self):
        return [candidate(self.provider_id)]

    async def complete(self, candidate, request):
        return "buffered reply"

    async def stream(self, cand, request):
        if self.fail_before:
            raise TransientProviderError("down")
        for index, chunk in enumerate(self.chunks):
            if self.fail_after is not None and index == self.fail_after:
                raise TransientProviderError("dropped mid-stream")
            yield chunk


def gateway_with(*providers):
    registry = ProviderRegistry()
    for provider in providers:
        registry.register(provider)
    return ModelGateway(registry)


async def collect(gateway, candidates, request=None, policy=RoutePolicy()):
    request = request or ModelRequest(task_type="chat", prompt="hi")
    return [event async for event in gateway.stream(request, candidates, policy)]


@pytest.mark.asyncio
async def test_stream_yields_deltas_then_final_event_with_usage():
    provider = StreamingProvider(
        "p",
        [
            StreamChunk(delta="Hel"),
            StreamChunk(delta="lo"),
            StreamChunk(finish_reason="stop", usage=TokenUsage(7, 2)),
        ],
    )
    events = await collect(gateway_with(provider), [candidate()])
    assert [e.delta for e in events if not e.done] == ["Hel", "lo"]
    final = events[-1]
    assert final.done and final.usage == TokenUsage(7, 2)
    assert final.cost_usd == 0.0 and final.attempted_candidates == ("p/m",)


@pytest.mark.asyncio
async def test_stream_falls_back_only_before_first_token():
    bad = StreamingProvider("a", [], fail_before=True)
    good = StreamingProvider("b", [StreamChunk(delta="ok", finish_reason="stop")])
    events = await collect(
        gateway_with(bad, good),
        [candidate("a", reliability=0.9), candidate("b", reliability=0.1)],
    )
    assert events[-1].provider_id == "b"
    assert events[-1].attempted_candidates == ("a/m", "b/m")


@pytest.mark.asyncio
async def test_stream_failure_after_first_token_is_not_silently_retried():
    broken = StreamingProvider("a", [StreamChunk(delta="par"), StreamChunk(delta="x")], fail_after=1)
    spare = StreamingProvider("b", [StreamChunk(delta="other", finish_reason="stop")])
    seen = []
    with pytest.raises(TransientProviderError):
        async for event in gateway_with(broken, spare).stream(
            ModelRequest(task_type="chat", prompt="hi"),
            [candidate("a", reliability=0.9), candidate("b", reliability=0.1)],
            RoutePolicy(),
        ):
            seen.append(event.delta)
    assert seen == ["par"]


@pytest.mark.asyncio
async def test_stream_respects_paid_policy():
    paid = StreamingProvider("a", [StreamChunk(delta="x", finish_reason="stop")])
    with pytest.raises(RuntimeError, match="No eligible"):
        await collect(gateway_with(paid), [candidate("a", free=False, inp=1.0, out=1.0)])


@pytest.mark.asyncio
async def test_provider_without_stream_uses_buffered_completion():
    class Plain:
        provider_id = "plain"

        async def health(self):
            return True

        async def list_models(self):
            return []

        async def complete(self, cand, request):
            return "whole answer"

    events = await collect(gateway_with(Plain()), [candidate("plain")])
    assert events[0].delta == "whole answer" and events[-1].done


def test_actual_cost_uses_reported_tokens_and_never_undercounts():
    request = ModelRequest(task_type="chat", prompt="x" * 400, max_output_tokens=1000)
    paid = candidate(free=False, inp=2.0, out=10.0)
    assert actual_cost_usd(paid, TokenUsage(1_000_000, 100_000), request) == pytest.approx(3.0)
    # no reported usage: worst-case estimate, not zero
    assert actual_cost_usd(paid, None, request) > 0
    assert actual_cost_usd(candidate(), TokenUsage(5, 5), request) == 0.0
    assert actual_cost_usd(candidate(free=False, inp=None, out=1.0), None, request) is None
    assert actual_cost_usd(candidate(free=False, inp=float("nan"), out=1.0),
                           TokenUsage(1, 1), request) is None
    assert actual_cost_usd(paid, TokenUsage(-1, 1), request) is None


def test_ledger_records_and_summarises(tmp_path):
    ledger = UsageLedger(tmp_path / "u.sqlite3")
    ledger.record(provider_id="ollama", model_id="a", task_type="chat", input_tokens=10,
                  output_tokens=5, estimated_input_tokens=1, estimated_output_tokens=1,
                  cost_usd=0.0, free_tier=True, data_local=True)
    ledger.record(provider_id="cloud", model_id="b", task_type="chat", input_tokens=None,
                  output_tokens=None, estimated_input_tokens=100, estimated_output_tokens=50,
                  cost_usd=0.25, free_tier=False, data_local=False, streamed=True)
    ledger.record(provider_id="cloud", model_id="b", task_type="chat", input_tokens=1,
                  output_tokens=1, estimated_input_tokens=1, estimated_output_tokens=1,
                  cost_usd=float("nan"), free_tier=False, data_local=False)
    summary = ledger.summary(7)
    assert summary["total_requests"] == 3
    assert summary["total_cost_usd"] == pytest.approx(0.25)  # NaN cost is dropped, not summed
    cloud = next(i for i in summary["items"] if i["provider_id"] == "cloud")
    assert cloud["estimated_token_rows"] == 1 and cloud["input_tokens"] == 101
    assert ledger.spend_today_utc() == pytest.approx(0.25)


def test_daily_cap_tightens_and_fails_closed(monkeypatch):
    policy = RoutePolicy(allow_paid=True, max_request_cost_usd=1.0)
    monkeypatch.delenv("MIA_MAX_DAILY_SPEND_USD", raising=False)
    assert apply_daily_cap(policy, 99.0) == policy  # no daily cap configured
    monkeypatch.setenv("MIA_MAX_DAILY_SPEND_USD", "2.0")
    assert apply_daily_cap(policy, 1.5).max_request_cost_usd == pytest.approx(0.5)
    assert apply_daily_cap(policy, 0.0).max_request_cost_usd == 1.0
    assert not apply_daily_cap(policy, 2.0).allow_paid
    assert not apply_daily_cap(policy, 3.0).allow_paid
    assert not apply_daily_cap(policy, float("nan")).allow_paid
    for bad in ("abc", "nan", "inf", "-1"):
        monkeypatch.setenv("MIA_MAX_DAILY_SPEND_USD", bad)
        assert not apply_daily_cap(policy, 0.0).allow_paid, bad
    # disabled paid routing stays disabled
    assert not apply_daily_cap(RoutePolicy(), 0.0).allow_paid


def make_client(tmp_path, *providers):
    return TestClient(
        create_app(
            ObservatoryStore(tmp_path / "o.sqlite3"),
            gateway_with(*providers),
            usage_ledger=UsageLedger(tmp_path / "u.sqlite3"),
            memory=None,
        )
    )


def parse_sse(text):
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_stream_endpoint_emits_sse_persists_reply_and_records_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path))
    provider = StreamingProvider(
        "stub-local",
        [
            StreamChunk(delta="Hi "),
            StreamChunk(delta="there"),
            StreamChunk(finish_reason="stop", usage=TokenUsage(11, 3)),
        ],
    )
    client = make_client(tmp_path, provider)
    response = client.post("/api/gateway/chat/stream", json={"prompt": "Hello"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    assert [e for e, _ in events] == ["delta", "delta", "done"]
    done = events[-1][1]
    assert done["input_tokens"] == 11 and done["output_tokens"] == 3 and done["cost_usd"] == 0.0

    usage = client.get("/api/gateway/usage").json()
    assert usage["total_requests"] == 1 and usage["total_input_tokens"] == 11

    messages = client.get(f"/api/conversations/{done['conversation_id']}").json()
    assert "Hi there" in json.dumps(messages)


def test_stream_endpoint_reports_error_event_when_no_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path))
    client = make_client(tmp_path, StreamingProvider("a", [], fail_before=True))
    response = client.post("/api/gateway/chat/stream", json={"prompt": "Hello"})
    events = parse_sse(response.text)
    assert events[-1][0] == "error"
    assert events[-1][1]["partial"] is False
