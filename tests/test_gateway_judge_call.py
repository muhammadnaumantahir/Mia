"""MIA-EVAL-03a: gateway-backed JudgeCall. Fake providers only (no live-model claims)."""

import asyncio

import pytest

from mia.brain.judges import JudgePolicy, run_judges
from mia.gateway.contracts import (
    ModelCandidate,
    ModelRequest,
    ProviderResponse,
    RoutePolicy,
    TokenUsage,
    TransientProviderError,
)
from mia.gateway.judge_call import make_judge_call
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway


class Fake:
    def __init__(self, pid, model, *, reply='{"verdict":"supported","reason":"ok"}', local=True,
                 free=True, fail=False, tool_calls=(), price=(None, None)):
        self.provider_id, self.model, self.reply, self.local = pid, model, reply, local
        self.free, self.fail, self.tool_calls, self.price = free, fail, tool_calls, price
        self.calls, self.seen = 0, []

    def candidate(self):
        return ModelCandidate(self.provider_id, self.model, frozenset({"chat"}), 16384, self.free, True, True,
                              data_local=self.local, input_cost_per_million_usd=self.price[0],
                              output_cost_per_million_usd=self.price[1])

    async def health(self):
        return True

    async def list_models(self):
        return [self.candidate()]

    async def complete(self, candidate, request):
        self.calls += 1
        self.seen.append(request)
        if self.fail:
            raise TransientProviderError("down")
        return ProviderResponse(content=self.reply, tool_calls=tuple(self.tool_calls),
                                usage=TokenUsage(10, 5))


def setup(*providers):
    registry = ProviderRegistry()
    for p in providers:
        registry.register(p)
    return ModelGateway(registry)


REQ = ModelRequest(task_type="judge", prompt="p", estimated_input_tokens=50, max_output_tokens=100,
                   required_capabilities=frozenset({"chat"}),
                   tools=({"type": "function", "function": {"name": "x"}},),
                   json_schema={"type": "object"})


def run(coro):
    return asyncio.run(coro)


def test_call_is_pinned_text_only_and_returns_the_reply():
    a, b = Fake("a", "judge-a"), Fake("b", "judge-b")
    call = make_judge_call(setup(a, b))
    assert "supported" in run(call(a.candidate(), REQ))
    assert (a.calls, b.calls) == (1, 0)
    assert a.seen[0].tools == () and a.seen[0].json_schema is None


def test_pinned_failure_raises_and_never_falls_back_to_another_model():
    a, b = Fake("a", "judge-a", fail=True), Fake("b", "judge-b")
    call = make_judge_call(setup(a, b))
    with pytest.raises(Exception):
        run(call(a.candidate(), REQ))
    assert b.calls == 0


def test_tool_calls_and_empty_text_are_rejected():
    tooly = Fake("a", "judge-a", tool_calls=({"id": "1", "function": {"name": "x", "arguments": "{}"}},))
    with pytest.raises(RuntimeError):
        run(make_judge_call(setup(tooly))(tooly.candidate(), REQ))


def test_paid_judge_is_blocked_by_the_normal_route_policy():
    paid = Fake("a", "judge-a", free=False, price=(1.0, 1.0))
    with pytest.raises(Exception):
        run(make_judge_call(setup(paid), lambda: RoutePolicy())(paid.candidate(), REQ))
    assert paid.calls == 0


def test_on_result_hook_receives_usage_and_its_failures_are_contained():
    a = Fake("a", "judge-a")
    seen = []

    def hook(result, request):
        seen.append(result.provider_id)
        raise ValueError("accounting bug")

    assert run(make_judge_call(setup(a), on_result=hook)(a.candidate(), REQ))
    assert seen == ["a"]


def test_works_end_to_end_with_run_judges_and_keeps_independence():
    writer = Fake("w", "writer-7b")
    j1, j2 = Fake("j1", "judge-1"), Fake("j2", "judge-2", reply='{"verdict":"unsupported","reason":"no source"}')
    gateway = setup(writer, j1, j2)
    report = run(run_judges(
        JudgePolicy(enabled=True), [writer.candidate(), j1.candidate(), j2.candidate()],
        prompt="Is DNS a database?", answer="Yes, I have checked it.", call=make_judge_call(gateway),
        category="reasoning", complexity="complex", issue_codes=["low_confidence"],
        generator_key="w/writer-7b"))
    assert report.consensus == "split" and writer.calls == 0 and j1.calls == 1 and j2.calls == 1
