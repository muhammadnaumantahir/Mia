import asyncio

import pytest

from mia.gateway.contracts import (
    AuthenticationProviderError,
    ModelCandidate,
    ModelRequest,
    PermanentProviderError,
    StreamChunk,
    TransientProviderError,
)
from mia.gateway.registry import ProviderRegistry
from mia.gateway.resilience import CircuitBreaker, ResilienceLog, RetryPolicy
from mia.gateway.service import ModelGateway

REQ = ModelRequest(task_type="chat", prompt="hello")


class Fake:
    def __init__(self, provider_id, script=()):
        self.provider_id = provider_id
        self.script = list(script)
        self.calls = 0

    async def list_models(self):
        return []

    def _next(self):
        self.calls += 1
        step = self.script.pop(0) if self.script else "ok"
        if isinstance(step, BaseException):
            raise step
        return step

    async def complete(self, candidate, request):
        return self._next()

    async def stream(self, candidate, request):
        text = self._next()
        for part in text.split("|"):
            yield StreamChunk(delta=part)
        yield StreamChunk(finish_reason="stop")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def cand(pid, rel=0.5):
    return ModelCandidate(pid, "m", frozenset({"chat"}), 8192, True, True, True,
                          reliability_score=rel)


def make(*providers, retry=None, threshold=5, clock=None):
    registry = ProviderRegistry()
    for p in providers:
        registry.register(p)
    events = ResilienceLog()
    clock = clock or Clock()
    breaker = CircuitBreaker(failure_threshold=threshold, open_seconds=30, max_open_seconds=300,
                             auth_open_seconds=300, clock=clock, on_event=events.add)
    sleeps: list[float] = []

    async def sleep(seconds):
        sleeps.append(seconds)

    gw = ModelGateway(registry, retry_policy=retry, breaker=breaker, events=events, sleep=sleep)
    return gw, breaker, events, sleeps, clock


def boom():
    return TransientProviderError("connection failed")


def kinds(events):
    return [e.kind for e in reversed(events.recent(100))]


def test_retry_policy_validation_and_bounded_delay():
    for bad in (dict(max_attempts=0), dict(max_attempts=6), dict(max_attempts=True),
                dict(base_delay_seconds=-1), dict(base_delay_seconds=float("nan")),
                dict(jitter=2), dict(max_delay_seconds=61)):
        with pytest.raises(ValueError):
            RetryPolicy(**bad)
    policy = RetryPolicy(max_attempts=5, base_delay_seconds=1, max_delay_seconds=4, jitter=0)
    assert [policy.delay(i) for i in range(4)] == [1, 2, 4, 4]
    jittery = RetryPolicy(max_attempts=2, base_delay_seconds=2, max_delay_seconds=8, jitter=0.5)
    assert 1.0 <= jittery.delay(0, rng=lambda: 0.0) <= 3.0
    assert jittery.delay(0, rng=lambda: 1.0) <= 8.0


@pytest.mark.asyncio
async def test_default_policy_does_not_retry_same_candidate():
    a = Fake("a", [boom(), "late"])
    gw, *_ = make(a)
    with pytest.raises(TransientProviderError):
        await gw.complete(REQ, [cand("a")])
    assert a.calls == 1


@pytest.mark.asyncio
async def test_transient_failures_are_retried_with_backoff_then_succeed():
    a = Fake("a", [boom(), boom(), "finally"])
    gw, breaker, events, sleeps, _ = make(
        a, retry=RetryPolicy(max_attempts=3, base_delay_seconds=1, jitter=0))
    result = await gw.complete(REQ, [cand("a")])
    assert result.content == "finally" and a.calls == 3
    assert sleeps == [1.0, 2.0]
    assert kinds(events) == ["retry", "retry"]
    assert breaker.state("a") == "closed" and gw.health.snapshot() == []


@pytest.mark.asyncio
async def test_exhausted_retries_fall_back_to_next_candidate():
    a = Fake("a", [boom(), boom()])
    b = Fake("b", ["from b"])
    gw, *_ = make(a, b, retry=RetryPolicy(max_attempts=2, jitter=0))
    result = await gw.complete(REQ, [cand("a", 0.9), cand("b", 0.1)])
    assert result.content == "from b" and a.calls == 2 and b.calls == 1
    assert result.attempted_candidates == ("a/m", "b/m")


@pytest.mark.asyncio
async def test_rate_limits_are_not_retried_and_do_not_trip_the_breaker():
    a = Fake("a", [TransientProviderError("429", retry_after=30, rate_limited=True)])
    b = Fake("b", ["ok b"])
    gw, breaker, _, sleeps, _ = make(
        a, b, retry=RetryPolicy(max_attempts=3, jitter=0), threshold=1)
    result = await gw.complete(REQ, [cand("a", 0.9), cand("b", 0.1)])
    assert result.content == "ok b" and a.calls == 1 and sleeps == []
    assert breaker.state("a") == "closed"  # throttled, not down
    assert gw.health.is_blocked(cand("a"))


@pytest.mark.asyncio
async def test_permanent_errors_are_not_retried_and_abort_the_chain():
    a = Fake("a", [PermanentProviderError("bad request")])
    b = Fake("b")
    gw, _, _, sleeps, _ = make(a, b, retry=RetryPolicy(max_attempts=3, jitter=0))
    with pytest.raises(PermanentProviderError):
        await gw.complete(REQ, [cand("a", 0.9), cand("b", 0.1)])
    assert a.calls == 1 and b.calls == 0 and sleeps == []


@pytest.mark.asyncio
async def test_auth_failure_falls_back_pauses_provider_and_is_not_retried():
    a = Fake("a", [AuthenticationProviderError("rejected")])
    b = Fake("b")
    gw, breaker, events, sleeps, clock = make(a, b, retry=RetryPolicy(max_attempts=3, jitter=0))
    pool = [cand("a", 0.9), cand("b", 0.1)]
    first = await gw.complete(REQ, pool)
    assert first.provider_id == "b" and a.calls == 1 and sleeps == []
    assert breaker.state("a") == "open" and "auth_failure" in kinds(events)
    second = await gw.complete(REQ, pool)  # a is skipped without being called
    assert second.provider_id == "b" and a.calls == 1
    assert "skipped" in kinds(events)
    clock.now += 301  # cooldown over: one probe is allowed again
    await gw.complete(REQ, pool)
    assert a.calls == 2 and breaker.state("a") == "closed"


@pytest.mark.asyncio
async def test_only_auth_failures_surface_as_a_provider_unavailable_error():
    a = Fake("a", [AuthenticationProviderError("rejected")])
    gw, *_ = make(a)
    with pytest.raises(TransientProviderError, match="a/m"):
        await gw.complete(REQ, [cand("a")])


@pytest.mark.asyncio
async def test_breaker_opens_after_threshold_and_half_open_probe_cycle():
    a = Fake("a", [boom(), boom(), boom(), boom(), "recovered"])
    gw, breaker, events, _, clock = make(a, threshold=3)
    for _ in range(3):
        with pytest.raises(TransientProviderError):
            await gw.complete(REQ, [cand("a")])
    assert breaker.state("a") == "open" and a.calls == 3
    with pytest.raises(TransientProviderError, match="temporarily paused"):
        await gw.complete(REQ, [cand("a")])
    assert a.calls == 3  # not called while open
    clock.now += 31
    assert breaker.state("a") == "half_open"
    with pytest.raises(TransientProviderError):  # probe fails -> reopens for twice as long
        await gw.complete(REQ, [cand("a")])
    assert a.calls == 4 and breaker.state("a") == "open"
    clock.now += 31
    assert breaker.state("a") == "open"  # 60s cooldown this time
    clock.now += 30
    assert (await gw.complete(REQ, [cand("a")])).content == "recovered"
    assert breaker.state("a") == "closed"
    assert {"circuit_open", "circuit_half_open", "circuit_closed"} <= set(kinds(events))


def test_half_open_allows_exactly_one_probe_and_release_frees_it():
    clock = Clock()
    breaker = CircuitBreaker(failure_threshold=1, open_seconds=10, clock=clock)
    breaker.record_failure("p")
    assert not breaker.allow("p")
    clock.now += 11
    assert breaker.allow("p") and not breaker.allow("p")
    breaker.release("p")
    assert breaker.allow("p")


def test_breaker_validation():
    for kwargs in (dict(failure_threshold=0), dict(failure_threshold=True),
                   dict(open_seconds=0), dict(max_open_seconds=float("inf"))):
        with pytest.raises(ValueError):
            CircuitBreaker(**kwargs)


@pytest.mark.asyncio
async def test_probe_slot_is_released_when_request_ends_without_a_countable_outcome():
    a = Fake("a", [boom(), PermanentProviderError("bad request")])
    gw, breaker, _, _, clock = make(a, threshold=1)
    with pytest.raises(TransientProviderError):
        await gw.complete(REQ, [cand("a")])
    clock.now += 31
    with pytest.raises(PermanentProviderError):  # the probe dies on a request-specific error
        await gw.complete(REQ, [cand("a")])
    assert breaker.allow("a")  # not stuck half-open with a phantom probe


@pytest.mark.asyncio
async def test_cancellation_releases_the_probe_slot():
    gate = asyncio.Event()

    class Hanging(Fake):
        async def complete(self, candidate, request):
            self.calls += 1
            if self.calls == 1:
                raise boom()
            await gate.wait()

    a = Hanging("a")
    gw, breaker, _, _, clock = make(a, threshold=1)
    with pytest.raises(TransientProviderError):
        await gw.complete(REQ, [cand("a")])
    clock.now += 31
    task = asyncio.create_task(gw.complete(REQ, [cand("a")]))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert breaker.allow("a")


@pytest.mark.asyncio
async def test_stream_falls_back_on_auth_failure_before_first_token_and_skips_paused():
    a = Fake("a", [AuthenticationProviderError("rejected")])
    b = Fake("b", ["hel|lo", "again"])
    gw, breaker, *_ = make(a, b)
    pool = [cand("a", 0.9), cand("b", 0.1)]
    events = [e async for e in gw.stream(REQ, pool)]
    text = "".join(e.delta for e in events if e.delta)
    assert text == "hello" and events[-1].done and events[-1].provider_id == "b"
    assert breaker.state("a") == "open"
    again = [e async for e in gw.stream(REQ, pool)]
    assert a.calls == 1 and again[-1].provider_id == "b"


@pytest.mark.asyncio
async def test_stream_failure_after_first_token_is_raised_and_counted():
    class Dropping(Fake):
        async def stream(self, candidate, request):
            self.calls += 1
            yield StreamChunk(delta="par")
            raise boom()

    a = Dropping("a")
    b = Fake("b")
    gw, breaker, events, *_ = make(a, b, threshold=1)
    got = []
    with pytest.raises(TransientProviderError):
        async for ev in gw.stream(REQ, [cand("a", 0.9), cand("b", 0.1)]):
            got.append(ev.delta)
    assert got == ["par"] and b.calls == 0  # partial output never mixed across models
    assert breaker.state("a") == "open" and "failure" in kinds(events)


@pytest.mark.asyncio
async def test_closing_a_stream_early_releases_the_probe_slot():
    a = Fake("a", [boom(), "one|two|three"])
    gw, breaker, _, _, clock = make(a, threshold=1)
    with pytest.raises(TransientProviderError):
        async for _ in gw.stream(REQ, [cand("a")]):
            pass
    clock.now += 31
    stream = gw.stream(REQ, [cand("a")])
    first = await stream.__anext__()
    assert first.delta == "one"
    await stream.aclose()
    assert breaker.allow("a")


@pytest.mark.asyncio
async def test_event_log_is_bounded_and_free_of_request_content():
    a = Fake("a", [TransientProviderError("connection failed")])
    gw, _, events, *_ = make(a)
    secret = ModelRequest(task_type="chat", prompt="TOP SECRET PROMPT")
    with pytest.raises(TransientProviderError):
        await gw.complete(secret, [cand("a")])
    assert "TOP SECRET" not in repr([e.to_dict() for e in events.recent()])
    log = ResilienceLog(capacity=2)
    for i in range(5):
        log.add("failure", f"p{i}")
    assert [e.subject for e in log.recent()] == ["p4", "p3"]
