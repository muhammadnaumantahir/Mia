"""MIA-GW-07: an empty/unusable model reply falls back instead of failing the turn.

Fake providers and mocked HTTP only; no live-provider claims. Request faults (HTTP 400),
credential errors and malformed payload types keep their previous no-fan-out semantics.
"""

import httpx
import pytest
import respx

from mia.gateway.contracts import (
    EmptyResponseError,
    ModelCandidate,
    ModelRequest,
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


class P:
    """Answers "" (empty) when empty=True, otherwise a fixed text; streams the same."""

    def __init__(self, pid, empty=False, buffered=False):
        self.provider_id, self.empty, self.calls = pid, empty, 0
        if buffered:
            self.stream = None  # force the buffered complete() path

    async def health(self):
        return True

    async def list_models(self):
        return []

    async def complete(self, candidate, request):
        self.calls += 1
        return "" if self.empty else f"answer from {self.provider_id}"

    async def stream(self, candidate, request):
        self.calls += 1
        if not self.empty:
            yield StreamChunk(delta=f"answer from {self.provider_id}")
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


def test_empty_response_error_is_permanent_and_not_transient():
    assert issubclass(EmptyResponseError, PermanentProviderError)
    assert not issubclass(EmptyResponseError, TransientProviderError)


@pytest.mark.asyncio
async def test_complete_falls_back_from_an_empty_reply():
    a, b = P("a", empty=True), P("b")
    g = gw(a, b)
    result = await g.complete(REQ, [cand("a", 0.9), cand("b", 0.1)])
    assert result.content == "answer from b"
    assert result.attempted_candidates == ("a/m", "b/m")
    assert g.breaker.state("a") == "closed" and not g.health.is_degraded(cand("a"))  # reachable
    assert [e.kind for e in g.events.recent() if e.subject == "a/m"] == ["invalid_output"]


@pytest.mark.asyncio
async def test_all_empty_raises_permanent_no_valid_response():
    g = gw(P("a", empty=True), P("b", empty=True))
    with pytest.raises(PermanentProviderError, match="No model produced a valid response") as info:
        await g.complete(REQ, [cand("a", 0.9), cand("b", 0.1)])
    assert not isinstance(info.value, TransientProviderError)
    assert "a/m" in str(info.value)


@pytest.mark.asyncio
async def test_stream_falls_back_from_an_empty_stream():
    a, b = P("a", empty=True), P("b")
    events = [e async for e in gw(a, b).stream(REQ, [cand("a", 0.9), cand("b", 0.1)])]
    assert "".join(e.delta for e in events) == "answer from b"
    assert events[-1].done and events[-1].attempted_candidates == ("a/m", "b/m")


@pytest.mark.asyncio
async def test_buffered_stream_path_falls_back_from_an_empty_reply():
    a, b = P("a", empty=True, buffered=True), P("b")
    events = [e async for e in gw(a, b).stream(REQ, [cand("a", 0.9), cand("b", 0.1)])]
    assert "".join(e.delta for e in events) == "answer from b"


@pytest.mark.asyncio
async def test_stream_all_empty_raises_permanent():
    g = gw(P("a", empty=True), P("b", empty=True))
    with pytest.raises(PermanentProviderError, match="No model produced a valid response"):
        [e async for e in g.stream(REQ, [cand("a", 0.9), cand("b", 0.1)])]


@pytest.mark.asyncio
async def test_paid_reservation_is_held_after_an_empty_reply(tmp_path, monkeypatch):
    # The provider processed the request (tokens may be billed), so the hold stays until expiry.
    monkeypatch.setenv("MIA_MAX_DAILY_SPEND_USD", "5")
    monkeypatch.setenv("MIA_MAX_MONTHLY_SPEND_USD", "5")
    ledger = UsageLedger(tmp_path / "usage.sqlite3")
    g = gw(P("a", empty=True))
    g.usage_ledger = ledger
    policy = RoutePolicy(allow_paid=True, max_request_cost_usd=1.0)
    with pytest.raises(PermanentProviderError):
        await g.complete(REQ, [cand("a", 0.9, free=False, price=1.0)], policy)
    assert ledger.reservation_summary()["active_count"] == 1  # actually attempted, then held


@pytest.mark.asyncio
@respx.mock
async def test_adapters_raise_empty_response_error_and_gateway_falls_back():
    respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": ""}}))
    respx.post("http://other.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "rescued"}}]}))
    respx.post("http://empty.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}))
    ollama = OllamaProvider()
    local = ModelCandidate("ollama", "tiny", frozenset({"chat"}), 8192, True, True, True,
                           reliability_score=0.9, data_local=True)
    with pytest.raises(EmptyResponseError):
        await ollama.complete(local, REQ)
    compat = OpenAICompatibleProvider("empty", "http://empty.test/v1", free_tier=True)
    with pytest.raises(EmptyResponseError):
        await compat.complete(cand("empty"), REQ)
    registry = ProviderRegistry()
    registry.register(ollama)
    registry.register(OpenAICompatibleProvider("other", "http://other.test/v1", free_tier=True))
    result = await ModelGateway(registry).complete(REQ, [local, cand("other", 0.1)])
    assert result.provider_id == "other" and result.content == "rescued"


@pytest.mark.asyncio
@respx.mock
async def test_malformed_payload_types_remain_plain_permanent():
    respx.post("http://x.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": 42}}]}))
    provider = OpenAICompatibleProvider("x", "http://x.test/v1", free_tier=True)
    with pytest.raises(PermanentProviderError) as info:
        await provider.complete(cand("x"), REQ)
    assert not isinstance(info.value, EmptyResponseError)


@pytest.mark.asyncio
async def test_a_bad_request_still_aborts_the_chain():
    class Bad(P):
        async def complete(self, candidate, request):
            self.calls += 1
            raise PermanentProviderError("HTTP 400")

    a, b = Bad("a"), P("b")
    with pytest.raises(PermanentProviderError):
        await gw(a, b).complete(REQ, [cand("a", 0.9), cand("b", 0.1)])
    assert b.calls == 0
