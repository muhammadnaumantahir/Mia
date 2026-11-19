"""Rate-limit tracking, Retry-After parsing and health-aware fallback ordering."""

import math

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from mia.gateway.contracts import (
    ModelCandidate,
    ModelRequest,
    PermanentProviderError,
    RoutePolicy,
    StreamChunk,
    TransientProviderError,
)
from mia.gateway.health import CandidateHealthTracker
from mia.gateway.providers import OllamaProvider, OpenAICompatibleProvider
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def cand(provider: str, reliability: float = 0.5) -> ModelCandidate:
    return ModelCandidate(
        provider_id=provider,
        model_id="m",
        capabilities=frozenset({"chat"}),
        context_window=8192,
        free_tier=True,
        healthy=True,
        enabled=True,
        reliability_score=reliability,
    )


class ScriptedProvider:
    """Provider whose complete() raises or returns following a script."""

    def __init__(self, provider_id: str, script: list) -> None:
        self.provider_id = provider_id
        self.script = list(script)
        self.calls = 0

    async def health(self) -> bool:
        return True

    async def list_models(self):
        return [cand(self.provider_id)]

    async def complete(self, candidate, request):
        self.calls += 1
        step = self.script.pop(0) if self.script else "ok"
        if isinstance(step, Exception):
            raise step
        return step

    async def stream(self, candidate, request):
        self.calls += 1
        step = self.script.pop(0) if self.script else "ok"
        if isinstance(step, Exception):
            raise step
        yield StreamChunk(delta=step, finish_reason="stop")


def make_gateway(clock: Clock, *providers) -> ModelGateway:
    registry = ProviderRegistry()
    for provider in providers:
        registry.register(provider)
    return ModelGateway(registry, CandidateHealthTracker(clock=clock))


REQUEST = ModelRequest(task_type="chat", prompt="hi")


# --- error contract ---------------------------------------------------------


@pytest.mark.parametrize("bad", [-1.0, math.nan, math.inf, True, "5"])
def test_invalid_retry_after_is_discarded(bad):
    error = TransientProviderError("x", retry_after=bad, rate_limited=True)
    assert error.retry_after is None


def test_valid_retry_after_is_kept():
    assert TransientProviderError("x", retry_after=7, rate_limited=True).retry_after == 7.0


# --- tracker ----------------------------------------------------------------


def test_tracker_rejects_invalid_configuration():
    with pytest.raises(ValueError):
        CandidateHealthTracker(base_backoff_seconds=math.nan)
    with pytest.raises(ValueError):
        CandidateHealthTracker(max_backoff_seconds=-1)


def test_rate_limit_blocks_until_retry_after_then_recovers():
    clock = Clock()
    tracker = CandidateHealthTracker(clock=clock)
    c = cand("a")
    tracker.record_failure(c, TransientProviderError("429", retry_after=30, rate_limited=True))
    assert tracker.is_blocked(c)
    clock.now += 29
    assert tracker.is_blocked(c)
    clock.now += 2
    assert not tracker.is_blocked(c)


def test_retry_after_is_capped():
    clock = Clock()
    tracker = CandidateHealthTracker(max_retry_after_seconds=60, clock=clock)
    c = cand("a")
    tracker.record_failure(c, TransientProviderError("429", retry_after=86_400, rate_limited=True))
    clock.now += 61
    assert not tracker.is_blocked(c)


def test_plain_transient_failure_degrades_but_never_blocks():
    clock = Clock()
    tracker = CandidateHealthTracker(base_backoff_seconds=5, clock=clock)
    c = cand("a")
    tracker.record_failure(c, TransientProviderError("timeout"))
    assert tracker.is_degraded(c) and not tracker.is_blocked(c)
    clock.now += 6
    assert not tracker.is_degraded(c)


def test_backoff_grows_exponentially_and_is_capped():
    clock = Clock()
    tracker = CandidateHealthTracker(base_backoff_seconds=5, max_backoff_seconds=20, clock=clock)
    c = cand("a")
    for expected in (5, 10, 20, 20):
        tracker.record_failure(c, TransientProviderError("boom"))
        assert tracker.snapshot()[0]["retry_in_seconds"] == expected
        clock.now += 0  # same instant: deadline is measured from "now"


def test_success_clears_state():
    tracker = CandidateHealthTracker(clock=Clock())
    c = cand("a")
    tracker.record_failure(c, TransientProviderError("429", retry_after=60, rate_limited=True))
    tracker.record_success(c)
    assert not tracker.is_blocked(c) and tracker.snapshot() == []


def test_tracker_memory_is_bounded():
    tracker = CandidateHealthTracker(clock=Clock())
    for i in range(700):
        tracker.record_failure(cand(f"p{i}"), TransientProviderError("x"))
    assert len(tracker.snapshot()) <= 512


# --- gateway behavior -------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limited_candidate_is_skipped_on_next_request():
    clock = Clock()
    limited = ScriptedProvider(
        "a", [TransientProviderError("429", retry_after=60, rate_limited=True)]
    )
    backup = ScriptedProvider("b", ["from b", "from b again"])
    gateway = make_gateway(clock, limited, backup)
    candidates = [cand("a", 0.9), cand("b", 0.1)]

    first = await gateway.complete(REQUEST, candidates)
    assert first.attempted_candidates == ("a/m", "b/m")

    second = await gateway.complete(REQUEST, candidates)
    assert second.attempted_candidates == ("b/m",)  # 'a' not hammered during its wait
    assert limited.calls == 1

    clock.now += 61
    third = await gateway.complete(REQUEST, candidates)
    assert third.attempted_candidates[0] == "a/m"  # eligible again after Retry-After


@pytest.mark.asyncio
async def test_degraded_candidate_is_tried_last_not_excluded():
    clock = Clock()
    flaky = ScriptedProvider("a", [TransientProviderError("timeout")])
    steady = ScriptedProvider("b", ["one", "two"])
    gateway = make_gateway(clock, flaky, steady)
    candidates = [cand("a", 0.9), cand("b", 0.1)]
    await gateway.complete(REQUEST, candidates)
    second = await gateway.complete(REQUEST, candidates)
    assert second.attempted_candidates == ("b/m",)


@pytest.mark.asyncio
async def test_sole_degraded_candidate_remains_usable():
    clock = Clock()
    only = ScriptedProvider("a", [TransientProviderError("blip"), "recovered"])
    gateway = make_gateway(clock, only)
    with pytest.raises(TransientProviderError):
        await gateway.complete(REQUEST, [cand("a")])
    result = await gateway.complete(REQUEST, [cand("a")])  # no cooldown lockout
    assert result.content == "recovered"
    assert gateway.health.snapshot() == []


@pytest.mark.asyncio
async def test_all_rate_limited_raises_with_retry_hint_and_makes_no_calls():
    clock = Clock()
    only = ScriptedProvider(
        "a", [TransientProviderError("429", retry_after=45, rate_limited=True)]
    )
    gateway = make_gateway(clock, only)
    with pytest.raises(TransientProviderError) as first:
        await gateway.complete(REQUEST, [cand("a")])
    assert first.value.rate_limited and first.value.retry_after == 45
    with pytest.raises(TransientProviderError) as second:
        await gateway.complete(REQUEST, [cand("a")])
    assert second.value.rate_limited and 0 < second.value.retry_after <= 45
    assert only.calls == 1


@pytest.mark.asyncio
async def test_permanent_error_does_not_affect_health():
    clock = Clock()
    bad = ScriptedProvider("a", [PermanentProviderError("HTTP 400")])
    gateway = make_gateway(clock, bad)
    with pytest.raises(PermanentProviderError):
        await gateway.complete(REQUEST, [cand("a")])
    assert gateway.health.snapshot() == []


@pytest.mark.asyncio
async def test_stream_failures_feed_health_tracker():
    clock = Clock()
    limited = ScriptedProvider(
        "a", [TransientProviderError("429", retry_after=60, rate_limited=True)]
    )
    backup = ScriptedProvider("b", ["x", "y"])
    gateway = make_gateway(clock, limited, backup)
    candidates = [cand("a", 0.9), cand("b", 0.1)]
    events = [e async for e in gateway.stream(REQUEST, candidates, RoutePolicy())]
    assert events[-1].attempted_candidates == ("a/m", "b/m")
    events = [e async for e in gateway.stream(REQUEST, candidates, RoutePolicy())]
    assert events[-1].attempted_candidates == ("b/m",)


# --- provider Retry-After parsing (real httpx responses via respx) ---------


@pytest.mark.asyncio
@respx.mock
async def test_openai_compatible_429_carries_retry_after_seconds():
    respx.post("http://gw.local/v1/chat/completions").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "12"})
    )
    provider = OpenAICompatibleProvider("gw", "http://gw.local/v1")
    with pytest.raises(TransientProviderError) as info:
        await provider.complete(cand("gw"), REQUEST)
    assert info.value.rate_limited and info.value.retry_after == 12.0
    assert "429" in str(info.value)  # message format unchanged


@pytest.mark.asyncio
@respx.mock
async def test_429_without_header_is_rate_limited_without_hint():
    respx.post("http://127.0.0.1:11434/api/chat").mock(return_value=httpx.Response(429))
    with pytest.raises(TransientProviderError) as info:
        await OllamaProvider().complete(cand("ollama"), REQUEST)
    assert info.value.rate_limited and info.value.retry_after is None


@pytest.mark.asyncio
@respx.mock
async def test_plain_500_is_not_rate_limited_and_garbage_header_ignored():
    respx.post("http://gw.local/v1/chat/completions").mock(
        return_value=httpx.Response(500, headers={"Retry-After": "soon"})
    )
    provider = OpenAICompatibleProvider("gw", "http://gw.local/v1")
    with pytest.raises(TransientProviderError) as info:
        await provider.complete(cand("gw"), REQUEST)
    assert not info.value.rate_limited and info.value.retry_after is None


@pytest.mark.asyncio
@respx.mock
async def test_503_with_retry_after_is_treated_as_throttle():
    respx.post("http://gw.local/v1/chat/completions").mock(
        return_value=httpx.Response(503, headers={"Retry-After": "3"})
    )
    provider = OpenAICompatibleProvider("gw", "http://gw.local/v1")
    with pytest.raises(TransientProviderError) as info:
        await provider.complete(cand("gw"), REQUEST)
    assert info.value.rate_limited and info.value.retry_after == 3.0


@pytest.mark.asyncio
@respx.mock
async def test_4xx_remains_permanent():
    respx.post("http://gw.local/v1/chat/completions").mock(return_value=httpx.Response(400))
    provider = OpenAICompatibleProvider("gw", "http://gw.local/v1")
    with pytest.raises(PermanentProviderError):
        await provider.complete(cand("gw"), REQUEST)


@pytest.mark.asyncio
@respx.mock
async def test_stream_429_carries_retry_after():
    respx.post("http://gw.local/v1/chat/completions").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "20"})
    )
    provider = OpenAICompatibleProvider("gw", "http://gw.local/v1")
    with pytest.raises(TransientProviderError) as info:
        async for _ in provider.stream(cand("gw"), REQUEST):
            pass
    assert info.value.rate_limited and info.value.retry_after == 20.0


# --- API --------------------------------------------------------------------


def api_client(tmp_path, gateway):
    store = ObservatoryStore(tmp_path / "obs.db")
    return TestClient(create_app(store=store, gateway=gateway))


def test_health_endpoint_reports_rate_limited_candidate(tmp_path):
    clock = Clock()
    gateway = make_gateway(clock, ScriptedProvider("a", []))
    gateway.health.record_failure(
        cand("a"), TransientProviderError("429", retry_after=30, rate_limited=True)
    )
    with api_client(tmp_path, gateway) as client:
        body = client.get("/api/gateway/health").json()
    assert body["items"][0]["candidate"] == "a/m"
    assert body["items"][0]["state"] == "rate_limited"
    assert body["items"][0]["retry_in_seconds"] == 30.0


def test_health_endpoint_empty_when_all_healthy(tmp_path):
    gateway = make_gateway(Clock(), ScriptedProvider("a", []))
    with api_client(tmp_path, gateway) as client:
        assert client.get("/api/gateway/health").json() == {"items": []}


def test_chat_endpoint_returns_retry_after_header_when_rate_limited(tmp_path):
    clock = Clock()
    provider = ScriptedProvider(
        "a", [TransientProviderError("429", retry_after=42.2, rate_limited=True)]
    )
    gateway = make_gateway(clock, provider)
    with api_client(tmp_path, gateway) as client:
        first = client.post("/api/gateway/chat", json={"prompt": "hello"})
        assert first.status_code == 503
        assert first.headers["retry-after"] == "43"
        second = client.post("/api/gateway/chat", json={"prompt": "hello"})
        assert second.status_code == 503 and "rate limited" in second.json()["detail"]
        assert provider.calls == 1  # second request never reached the provider


def test_chat_endpoint_has_no_retry_after_for_plain_transient_failure(tmp_path):
    gateway = make_gateway(Clock(), ScriptedProvider("a", [TransientProviderError("timeout")]))
    with api_client(tmp_path, gateway) as client:
        response = client.post("/api/gateway/chat", json={"prompt": "hello"})
    assert response.status_code == 503 and "retry-after" not in response.headers
