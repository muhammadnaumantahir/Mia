"""MIA-GW-04: gateway-level provider timeouts. Hung fake providers only; no live-provider claims."""

import asyncio
import time

import pytest

from mia.gateway.contracts import ModelCandidate, ModelRequest, RoutePolicy
from mia.gateway.ledger import UsageLedger
from mia.gateway.providers import TransientProviderError
from mia.gateway.registry import ProviderRegistry
from mia.gateway.resilience import RetryPolicy
from mia.gateway.service import (
    DEFAULT_ATTEMPT_TIMEOUT_S,
    DEFAULT_STREAM_IDLE_TIMEOUT_S,
    ModelGateway,
    ProviderTimeout,
)

REQ = ModelRequest(task_type="chat", prompt="hi", estimated_input_tokens=50, max_output_tokens=50,
                   required_capabilities=frozenset({"chat"}))


class Hung:
    """complete() never returns (until cancelled)."""

    def __init__(self, pid="hung", *, free=True, price=1.0):
        self.provider_id, self.free, self.price, self.calls, self.cancelled = pid, free, price, 0, 0

    def candidate(self):
        return ModelCandidate(self.provider_id, "m", frozenset({"chat"}), 4096, self.free, True, True,
                              input_cost_per_million_usd=None if self.free else self.price,
                              output_cost_per_million_usd=None if self.free else self.price)

    async def health(self):
        return True

    async def list_models(self):
        return [self.candidate()]

    async def complete(self, candidate, request):
        self.calls += 1
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


class Ok(Hung):
    async def complete(self, candidate, request):
        self.calls += 1
        return "fine"


def gateway(*providers, **kw):
    registry = ProviderRegistry()
    for p in providers:
        registry.register(p)
    return ModelGateway(registry, **kw)


def test_defaults_are_on_with_safe_values_and_validated(monkeypatch):
    for name in ("MIA_GATEWAY_ATTEMPT_TIMEOUT_S", "MIA_GATEWAY_STREAM_IDLE_TIMEOUT_S"):
        monkeypatch.delenv(name, raising=False)
    g = gateway()
    assert g.attempt_timeout == DEFAULT_ATTEMPT_TIMEOUT_S and g.stream_idle_timeout == DEFAULT_STREAM_IDLE_TIMEOUT_S
    assert gateway(attempt_timeout=None).attempt_timeout is None  # explicit opt-out in code
    for bad in (0, -1, float("nan"), float("inf"), 99999):
        with pytest.raises(ValueError):
            gateway(attempt_timeout=bad)


def test_env_overrides_fail_safe(monkeypatch):
    monkeypatch.setenv("MIA_GATEWAY_ATTEMPT_TIMEOUT_S", "7")
    monkeypatch.setenv("MIA_GATEWAY_STREAM_IDLE_TIMEOUT_S", "garbage")
    g = gateway()
    assert g.attempt_timeout == 7.0 and g.stream_idle_timeout == DEFAULT_STREAM_IDLE_TIMEOUT_S
    monkeypatch.setenv("MIA_GATEWAY_ATTEMPT_TIMEOUT_S", "off")
    assert gateway().attempt_timeout is None
    monkeypatch.setenv("MIA_GATEWAY_ATTEMPT_TIMEOUT_S", "-5")
    assert gateway().attempt_timeout == DEFAULT_ATTEMPT_TIMEOUT_S


def test_hung_provider_times_out_and_falls_back_to_the_next_candidate():
    hung, ok = Hung("a"), Ok("b")
    g = gateway(hung, ok, attempt_timeout=0.05)
    started = time.perf_counter()
    result = asyncio.run(g.complete(REQ, [hung.candidate(), ok.candidate()], RoutePolicy()))
    assert time.perf_counter() - started < 2.0
    assert result.provider_id == "b" and result.content == "fine"
    assert hung.cancelled == 1  # the hung call was actually cancelled, not leaked


def test_timeout_is_not_retried_on_the_same_provider():
    hung = Hung("a")
    g = gateway(hung, attempt_timeout=0.05, retry_policy=RetryPolicy(max_attempts=3))
    with pytest.raises(TransientProviderError):
        asyncio.run(g.complete(REQ, [hung.candidate()], RoutePolicy()))
    assert hung.calls == 1


def test_all_hung_raises_a_timeout_error_instead_of_waiting_forever():
    a, b = Hung("a"), Hung("b")
    g = gateway(a, b, attempt_timeout=0.05)
    started = time.perf_counter()
    with pytest.raises(TransientProviderError, match="All eligible candidates failed") as info:
        asyncio.run(g.complete(REQ, [a.candidate(), b.candidate()], RoutePolicy()))
    assert isinstance(info.value.__cause__, ProviderTimeout)
    assert time.perf_counter() - started < 2.0 and (a.calls, b.calls) == (1, 1)


def test_repeated_timeouts_open_the_breaker_so_the_hung_provider_is_skipped_fast():
    hung, ok = Hung("a"), Ok("b")
    g = gateway(hung, ok, attempt_timeout=0.02)
    cands = [hung.candidate(), ok.candidate()]
    for _ in range(8):
        assert asyncio.run(g.complete(REQ, cands, RoutePolicy())).provider_id == "b"
    assert hung.calls < 8  # breaker/health stopped sending traffic to the hung provider


def test_consumer_cancellation_is_not_mistaken_for_a_timeout():
    hung = Hung("a")
    g = gateway(hung, attempt_timeout=30)

    async def scenario():
        task = asyncio.ensure_future(g.complete(REQ, [hung.candidate()], RoutePolicy()))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert hung.cancelled == 1


def test_buffered_stream_path_is_also_bounded_and_falls_back():
    hung, ok = Hung("a"), Ok("b")
    g = gateway(hung, ok, attempt_timeout=0.05)

    async def scenario():
        events = []
        async for event in g.stream(REQ, [hung.candidate(), ok.candidate()], RoutePolicy()):
            events.append(event)
        return events

    events = asyncio.run(scenario())
    assert events[-1].done and events[-1].provider_id == "b"
    assert "".join(e.delta for e in events if e.delta) == "fine"


def test_paid_attempt_that_times_out_keeps_its_reservation(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_MAX_DAILY_SPEND_USD", "1")
    monkeypatch.setenv("MIA_MAX_MONTHLY_SPEND_USD", "5")
    # Free-first ranking would try a free model before any paid one, so the fallback is a
    # *more expensive* paid model: the cheaper hung one is attempted (and reserved) first.
    paid, fallback = Hung("cloud", free=False, price=1.0), Ok("cloud2", free=False, price=5.0)
    ledger = UsageLedger(tmp_path / "t.sqlite3")
    g = gateway(paid, fallback, attempt_timeout=0.05)
    g.usage_ledger, g.require_budget_reservations = ledger, True
    policy = RoutePolicy(allow_paid=True, max_request_cost_usd=0.01)
    result = asyncio.run(g.complete(REQ, [paid.candidate(), fallback.candidate()], policy))
    assert paid.calls == 1 and result.provider_id == "cloud2" and result.reservation_id
    # 2 held: the timed-out attempt (possibly billed before we gave up) and the successful one
    # (consumed when its usage is recorded). The timed-out one is not refunded.
    assert ledger.reservation_summary()["active_count"] == 2
