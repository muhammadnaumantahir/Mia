"""MIA-GW-05: a model/endpoint that is gone (HTTP 404/410) falls back instead of failing the turn.

Fake providers and mocked HTTP only; no live-provider claims. Other permanent errors
(400 bad request, credentials, empty output) keep their existing no-fan-out semantics.
"""

import httpx
import pytest
import respx

from mia.gateway.contracts import (
    ModelCandidate,
    ModelRequest,
    ModelUnavailableError,
    PermanentProviderError,
    RoutePolicy,
    StreamChunk,
    TransientProviderError,
)
from mia.gateway.ledger import UsageLedger
from mia.gateway.providers import OllamaProvider, OpenAICompatibleProvider
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway

REQ = ModelRequest(task_type="chat", prompt="hello", estimated_input_tokens=20,
                   max_output_tokens=20, required_capabilities=frozenset({"chat"}))


class Scripted:
    def __init__(self, pid, step="ok"):
        self.provider_id, self.step, self.calls = pid, step, 0

    async def health(self):
        return True

    async def list_models(self):
        return []

    def _go(self):
        self.calls += 1
        if isinstance(self.step, BaseException):
            raise self.step
        return self.step

    async def complete(self, candidate, request):
        return self._go()

    async def stream(self, candidate, request):
        text = self._go()
        yield StreamChunk(delta=text)
        yield StreamChunk(finish_reason="stop")


def cand(pid, rel=0.5, free=True, price=None):
    return ModelCandidate(pid, "m", frozenset({"chat"}), 8192, free, True, True,
                          reliability_score=rel,
                          input_cost_per_million_usd=price, output_cost_per_million_usd=price)


def gw(*providers):
    registry = ProviderRegistry()
    for p in providers:
        registry.register(p)
    return ModelGateway(registry)


def gone(msg="model not found"):
    return ModelUnavailableError(msg)


def test_model_unavailable_is_a_permanent_error_for_existing_handlers():
    assert issubclass(ModelUnavailableError, PermanentProviderError)
    assert not issubclass(ModelUnavailableError, TransientProviderError)


@pytest.mark.asyncio
async def test_complete_falls_back_when_first_model_is_gone():
    a, b = Scripted("a", gone()), Scripted("b", "from b")
    g = gw(a, b)
    result = await g.complete(REQ, [cand("a", 0.9), cand("b", 0.1)])
    assert result.content == "from b"
    assert result.attempted_candidates == ("a/m", "b/m")
    assert a.calls == 1 and b.calls == 1  # never retried on the missing model
    assert g.breaker.state("a") == "closed"  # reachable provider, not an outage
    assert g.health.is_degraded(cand("a"))  # but ranked after healthy candidates next time
    assert [e.kind for e in g.events.recent() if e.subject == "a/m"] == ["model_unavailable"]


@pytest.mark.asyncio
async def test_next_turn_prefers_the_healthy_candidate_first():
    a, b = Scripted("a", gone()), Scripted("b", "from b")
    g = gw(a, b)
    await g.complete(REQ, [cand("a", 0.9), cand("b", 0.1)])
    again = await g.complete(REQ, [cand("a", 0.9), cand("b", 0.1)])
    assert again.attempted_candidates == ("b/m",)
    assert a.calls == 1  # not hammered on every turn


@pytest.mark.asyncio
async def test_all_models_gone_raises_permanent_not_a_retryable_error():
    g = gw(Scripted("a", gone()), Scripted("b", gone()))
    with pytest.raises(ModelUnavailableError) as info:
        await g.complete(REQ, [cand("a", 0.9), cand("b", 0.1)])
    assert not isinstance(info.value, TransientProviderError)
    assert "a/m" in str(info.value) and "b/m" in str(info.value)


@pytest.mark.asyncio
async def test_transient_failure_still_takes_precedence_over_gone_models():
    a = Scripted("a", TransientProviderError("busy"))
    b = Scripted("b", gone())
    with pytest.raises(TransientProviderError):  # retry may help, so say so
        await gw(a, b).complete(REQ, [cand("a", 0.9), cand("b", 0.1)])


@pytest.mark.asyncio
async def test_bad_request_still_aborts_the_chain():
    a, b = Scripted("a", PermanentProviderError("HTTP 400")), Scripted("b")
    with pytest.raises(PermanentProviderError) as info:
        await gw(a, b).complete(REQ, [cand("a", 0.9), cand("b", 0.1)])
    assert not isinstance(info.value, ModelUnavailableError)
    assert b.calls == 0


@pytest.mark.asyncio
async def test_paid_reservation_is_released_when_model_is_gone(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_MAX_DAILY_SPEND_USD", "5")
    monkeypatch.setenv("MIA_MAX_MONTHLY_SPEND_USD", "5")
    ledger = UsageLedger(tmp_path / "usage.sqlite3")
    a, b = Scripted("a", gone()), Scripted("b", "free answer")
    g = gw(a, b)
    g.usage_ledger = ledger
    policy = RoutePolicy(allow_paid=True, max_request_cost_usd=1.0)
    result = await g.complete(REQ, [cand("a", 0.9, free=False, price=1.0), cand("b", 0.1)], policy)
    assert result.content == "free answer"
    assert ledger.reservation_summary()["active_count"] == 0  # rejected before processing: no hold


@pytest.mark.asyncio
async def test_stream_falls_back_before_first_token():
    a, b = Scripted("a", gone()), Scripted("b", "streamed")
    events = [e async for e in gw(a, b).stream(REQ, [cand("a", 0.9), cand("b", 0.1)])]
    assert "".join(e.delta for e in events) == "streamed"
    final = events[-1]
    assert final.done and final.provider_id == "b"
    assert final.attempted_candidates == ("a/m", "b/m")


@pytest.mark.asyncio
async def test_stream_all_gone_raises_permanent():
    g = gw(Scripted("a", gone()), Scripted("b", gone()))
    with pytest.raises(ModelUnavailableError):
        [e async for e in g.stream(REQ, [cand("a", 0.9), cand("b", 0.1)])]


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("status", [404, 410])
async def test_http_not_found_maps_to_model_unavailable_and_falls_back(status):
    respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(status, json={"error": "model 'x' not found"}))
    respx.post("http://other.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "rescued"}}]}))
    registry = ProviderRegistry()
    registry.register(OllamaProvider())
    registry.register(OpenAICompatibleProvider("other", "http://other.test/v1", free_tier=True))
    local = ModelCandidate("ollama", "gone:latest", frozenset({"chat"}), 8192, True, True, True,
                           reliability_score=0.9, data_local=True)
    result = await ModelGateway(registry).complete(REQ, [local, cand("other", 0.1)])
    assert result.provider_id == "other" and result.content == "rescued"


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("status,expected", [(400, PermanentProviderError), (422, PermanentProviderError)])
async def test_other_4xx_remain_plain_permanent(status, expected):
    respx.post("http://x.test/v1/chat/completions").mock(return_value=httpx.Response(status))
    provider = OpenAICompatibleProvider("x", "http://x.test/v1", free_tier=True)
    with pytest.raises(expected) as info:
        await provider.complete(cand("x"), REQ)
    assert not isinstance(info.value, ModelUnavailableError)
