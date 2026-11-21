"""MIA-GW-06: a per-turn deadline bounds the total wait across fallback candidates.

Hung/slow fake providers with tiny real timeouts only; no live-provider claims.
"""

import asyncio
import time

import pytest

from mia.gateway.contracts import ModelCandidate, ModelRequest, RoutePolicy, StreamChunk
from mia.gateway.providers import TransientProviderError
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import DEFAULT_TURN_DEADLINE_S, ModelGateway, ProviderTimeout

REQ = ModelRequest(task_type="chat", prompt="hi", estimated_input_tokens=50, max_output_tokens=50,
                   required_capabilities=frozenset({"chat"}))


class P:
    """Configurable fake: mode is hang | ok | silent_stream | slow_stream."""

    def __init__(self, pid, mode="ok"):
        self.provider_id, self.mode, self.calls = pid, mode, 0

    def candidate(self, rel=0.5):
        return ModelCandidate(self.provider_id, "m", frozenset({"chat"}), 4096, True, True, True,
                              reliability_score=rel)

    async def health(self):
        return True

    async def list_models(self):
        return [self.candidate()]

    async def complete(self, candidate, request):
        self.calls += 1
        if self.mode == "hang":
            await asyncio.sleep(3600)
        return "fine"

    async def stream(self, candidate, request):
        self.calls += 1
        if self.mode in ("hang", "silent_stream"):
            await asyncio.sleep(3600)
        if self.mode == "slow_stream":
            for i in range(6):  # about 0.3s of steady output
                await asyncio.sleep(0.05)
                yield StreamChunk(delta=f"t{i} ")
        else:
            yield StreamChunk(delta="fine")
        yield StreamChunk(finish_reason="stop")


def gateway(*providers, **kw):
    registry = ProviderRegistry()
    for p in providers:
        registry.register(p)
    return ModelGateway(registry, **kw)


def ranked(*providers):
    return [p.candidate(0.9 - 0.1 * i) for i, p in enumerate(providers)]


def test_default_is_on_validated_and_env_overridable(monkeypatch):
    monkeypatch.delenv("MIA_GATEWAY_TURN_DEADLINE_S", raising=False)
    assert gateway().turn_deadline == DEFAULT_TURN_DEADLINE_S
    assert gateway(turn_deadline=None).turn_deadline is None
    for bad in (0, -1, float("nan"), float("inf"), 99999):
        with pytest.raises(ValueError):
            gateway(turn_deadline=bad)
    monkeypatch.setenv("MIA_GATEWAY_TURN_DEADLINE_S", "12")
    assert gateway().turn_deadline == 12.0
    monkeypatch.setenv("MIA_GATEWAY_TURN_DEADLINE_S", "off")
    assert gateway().turn_deadline is None
    monkeypatch.setenv("MIA_GATEWAY_TURN_DEADLINE_S", "junk")
    assert gateway().turn_deadline == DEFAULT_TURN_DEADLINE_S


def test_without_a_deadline_every_hung_candidate_is_waited_on():
    a, b, c = P("a", "hang"), P("b", "hang"), P("c", "hang")
    g = gateway(a, b, c, attempt_timeout=0.1, turn_deadline=None)
    with pytest.raises(TransientProviderError):
        asyncio.run(g.complete(REQ, ranked(a, b, c), RoutePolicy()))
    assert (a.calls, b.calls, c.calls) == (1, 1, 1)  # control: the cost the deadline removes


def test_deadline_stops_trying_more_candidates_once_spent():
    a, b, c = P("a", "hang"), P("b", "hang"), P("c", "ok")
    g = gateway(a, b, c, attempt_timeout=0.15, turn_deadline=0.2)
    started = time.perf_counter()
    with pytest.raises(TransientProviderError, match="All eligible candidates failed"):
        asyncio.run(g.complete(REQ, ranked(a, b, c), RoutePolicy()))
    assert time.perf_counter() - started < 0.6  # not 3 x attempt timeout
    assert c.calls == 0  # budget spent before the healthy third candidate was reached
    assert [e.kind for e in g.events.recent() if e.kind == "deadline"] == ["deadline"]


def test_deadline_clamps_a_single_long_attempt():
    a = P("a", "hang")
    g = gateway(a, attempt_timeout=30, turn_deadline=0.1)
    started = time.perf_counter()
    with pytest.raises(TransientProviderError) as info:
        asyncio.run(g.complete(REQ, ranked(a), RoutePolicy()))
    assert time.perf_counter() - started < 1.0
    assert isinstance(info.value.__cause__, ProviderTimeout)


def test_fast_fallback_inside_the_budget_is_unaffected():
    a, b = P("a", "hang"), P("b", "ok")
    g = gateway(a, b, attempt_timeout=0.05, turn_deadline=5)
    result = asyncio.run(g.complete(REQ, ranked(a, b), RoutePolicy()))
    assert result.provider_id == "b" and result.content == "fine"


def test_each_turn_gets_a_fresh_budget():
    a = P("a", "ok")
    g = gateway(a, attempt_timeout=1, turn_deadline=0.3)

    async def two_turns():
        first = await g.complete(REQ, ranked(a), RoutePolicy())
        await asyncio.sleep(0.4)  # longer than one budget
        return first, await g.complete(REQ, ranked(a), RoutePolicy())

    first, second = asyncio.run(two_turns())
    assert first.content == second.content == "fine"


def test_stream_deadline_bounds_the_wait_for_a_first_chunk_and_skips_later_candidates():
    a, b, c = P("a", "silent_stream"), P("b", "silent_stream"), P("c", "ok")
    g = gateway(a, b, c, stream_idle_timeout=0.15, turn_deadline=0.2)

    async def run():
        return [e async for e in g.stream(REQ, ranked(a, b, c), RoutePolicy())]

    started = time.perf_counter()
    with pytest.raises(TransientProviderError):
        asyncio.run(run())
    assert time.perf_counter() - started < 0.6
    assert c.calls == 0


def test_stream_already_producing_text_is_never_cut_off_by_the_deadline():
    a = P("a", "slow_stream")
    g = gateway(a, stream_idle_timeout=5, turn_deadline=0.1)  # output lasts about 3x the budget

    async def run():
        return [e async for e in g.stream(REQ, ranked(a), RoutePolicy())]

    events = asyncio.run(run())
    assert "".join(e.delta for e in events) == "t0 t1 t2 t3 t4 t5 "
    assert events[-1].done


def test_buffered_stream_path_is_bounded_by_the_deadline():
    class NoStream(P):
        stream = None  # forces the buffered complete() path

    a, b = NoStream("a", "hang"), P("b", "ok")
    g = gateway(a, b, attempt_timeout=30, turn_deadline=0.1)

    async def run():
        return [e async for e in g.stream(REQ, ranked(a, b), RoutePolicy())]

    started = time.perf_counter()
    with pytest.raises(TransientProviderError):
        asyncio.run(run())
    assert time.perf_counter() - started < 1.0
    assert b.calls == 0  # budget already spent when b's turn came


def test_consumer_cancellation_still_propagates_with_a_deadline():
    a = P("a", "hang")
    g = gateway(a, attempt_timeout=30, turn_deadline=30)

    async def scenario():
        task = asyncio.ensure_future(g.complete(REQ, ranked(a), RoutePolicy()))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
