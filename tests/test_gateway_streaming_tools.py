import asyncio
import json

import httpx
import pytest
import respx

from mia.gateway.contracts import (
    ModelCandidate,
    ModelRequest,
    PermanentProviderError,
    ProviderResponse,
    StreamChunk,
    StreamInterruptedError,
    TransientProviderError,
)
from mia.gateway.providers import OllamaProvider, OpenAICompatibleProvider
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway

TOOLS = ({"type": "function", "function": {"name": "get_time", "parameters": {"type": "object"}}},)


def sse(*events):
    body = "".join(f"data: {json.dumps(e) if e != '[DONE]' else e}\n\n" for e in events)
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


def delta(**fields):
    return {"choices": [{"delta": fields, "finish_reason": None}]}


def cand(pid="remote", tools=True):
    caps = {"chat", "tools"} if tools else {"chat"}
    return ModelCandidate(pid, "m", frozenset(caps), 8192, True, True, True)


async def collect(provider, request, candidate=None):
    return [c async for c in provider.stream(candidate or cand(), request)]


@pytest.mark.asyncio
@respx.mock
async def test_openai_stream_assembles_tool_call_fragments_into_one_whole_call():
    route = respx.post("https://r.example.com/v1/chat/completions").mock(
        return_value=sse(
            delta(content="Checking "),
            delta(tool_calls=[{"index": 0, "id": "call_1",
                               "function": {"name": "get_time", "arguments": '{"tz":'}}]),
            delta(tool_calls=[{"index": 0, "function": {"arguments": ' "UTC"}'}}]),
            delta(tool_calls=[{"index": 1, "id": "call_2",
                               "function": {"name": "get_time", "arguments": "{}"}}]),
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            "[DONE]",
        )
    )
    provider = OpenAICompatibleProvider("remote", "https://r.example.com/v1", supports_tools=True)
    chunks = await collect(provider, ModelRequest(task_type="agent", prompt="time?", tools=TOOLS))
    assert [c.delta for c in chunks if c.delta] == ["Checking "]
    final = chunks[-1]
    assert final.finish_reason == "tool_calls"
    assert [(c["id"], c["function"]["name"], c["function"]["arguments"]) for c in final.tool_calls] \
        == [("call_1", "get_time", '{"tz": "UTC"}'), ("call_2", "get_time", "{}")]
    assert all(not c.tool_calls for c in chunks[:-1])  # never partial fragments
    sent = json.loads(route.calls[0].request.content)
    assert sent["tools"] == list(TOOLS) and sent["tool_choice"] == "auto" and sent["stream"]


@pytest.mark.asyncio
@respx.mock
async def test_tools_are_not_sent_to_providers_not_configured_for_them():
    route = respx.post("https://r.example.com/v1/chat/completions").mock(
        return_value=sse(delta(content="hi"), "[DONE]"))
    provider = OpenAICompatibleProvider("remote", "https://r.example.com/v1")  # no tools
    await collect(provider, ModelRequest(task_type="chat", prompt="x", tools=TOOLS), cand(tools=False))
    assert "tools" not in json.loads(route.calls[0].request.content)


@pytest.mark.asyncio
@respx.mock
async def test_malformed_streamed_tool_calls_are_dropped_or_rejected():
    respx.post("https://r.example.com/v1/chat/completions").mock(
        return_value=sse(
            delta(tool_calls=[{"index": 0, "function": {"arguments": "{}"}}]),  # no name
            delta(content="ok"), "[DONE]"))
    provider = OpenAICompatibleProvider("remote", "https://r.example.com/v1", supports_tools=True)
    chunks = await collect(provider, ModelRequest(task_type="agent", prompt="x", tools=TOOLS))
    assert chunks[-1].tool_calls == ()

    respx.post("https://r.example.com/v1/chat/completions").mock(
        return_value=sse(delta(tool_calls=[{"index": 99, "function": {"name": "x"}}]), "[DONE]"))
    with pytest.raises(PermanentProviderError):
        await collect(provider, ModelRequest(task_type="agent", prompt="x", tools=TOOLS))

    respx.post("https://r.example.com/v1/chat/completions").mock(
        return_value=sse(delta(tool_calls=[{"index": 0, "function": {
            "name": "x", "arguments": "a" * 200_001}}]), "[DONE]"))
    with pytest.raises(PermanentProviderError):
        await collect(provider, ModelRequest(task_type="agent", prompt="x", tools=TOOLS))


@pytest.mark.asyncio
@respx.mock
async def test_ollama_stream_collects_tool_calls_and_sends_tools_when_enabled():
    lines = [
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "get_time", "arguments": {"tz": "UTC"}}}]}, "done": False},
        {"message": {"content": ""}, "done": True, "done_reason": "stop",
         "prompt_eval_count": 3, "eval_count": 5},
    ]
    route = respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode()))
    provider = OllamaProvider(supports_tools=True)
    chunks = await collect(provider, ModelRequest(task_type="agent", prompt="x", tools=TOOLS),
                           cand("ollama"))
    final = chunks[-1]
    assert final.tool_calls[0]["function"]["name"] == "get_time"
    assert json.loads(final.tool_calls[0]["function"]["arguments"]) == {"tz": "UTC"}
    assert final.usage is not None and json.loads(route.calls[0].request.content)["tools"]


class Scripted:
    """Provider whose stream() yields prepared chunks; no streams_tools attribute by default."""

    def __init__(self, provider_id, chunks=(), *, streams_tools=False, hang_after=None):
        self.provider_id = provider_id
        self.chunks = list(chunks)
        self.streams_tools = streams_tools
        self.hang_after = hang_after
        self.closed = False
        self.complete_calls = 0

    async def list_models(self):
        return []

    async def complete(self, candidate, request):
        self.complete_calls += 1
        return ProviderResponse(content="", tool_calls=(
            {"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}},))

    async def stream(self, candidate, request):
        try:
            for i, chunk in enumerate(self.chunks):
                yield chunk
                if self.hang_after == i:
                    await asyncio.sleep(3600)
        finally:
            self.closed = True


def gateway(*providers, **kwargs):
    registry = ProviderRegistry()
    for p in providers:
        registry.register(p)
    return ModelGateway(registry, **kwargs)


CALL = {"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}


@pytest.mark.asyncio
async def test_service_streams_text_then_delivers_tool_calls_on_done_event():
    p = Scripted("a", [StreamChunk(delta="one "), StreamChunk(delta="two"),
                       StreamChunk(finish_reason="tool_calls", tool_calls=(CALL,))],
                 streams_tools=True)
    req = ModelRequest(task_type="agent", prompt="x", tools=TOOLS, required_capabilities=frozenset({"tools"}))
    events = [e async for e in gateway(p).stream(req, [cand("a")])]
    assert "".join(e.delta for e in events) == "one two"
    assert events[-1].done and events[-1].tool_calls == (CALL,)
    assert events[-1].finish_reason == "tool_calls" and p.complete_calls == 0


@pytest.mark.asyncio
async def test_tool_only_streamed_response_is_valid_not_empty():
    p = Scripted("a", [StreamChunk(finish_reason="tool_calls", tool_calls=(CALL,))], streams_tools=True)
    req = ModelRequest(task_type="agent", prompt="x", tools=TOOLS)
    events = [e async for e in gateway(p).stream(req, [cand("a")])]
    assert [e.done for e in events] == [True] and events[0].tool_calls == (CALL,)


@pytest.mark.asyncio
async def test_providers_without_streaming_tools_use_one_buffered_call_and_keep_tool_calls():
    p = Scripted("a", [StreamChunk(delta="never used")], streams_tools=False)
    req = ModelRequest(task_type="agent", prompt="x", tools=TOOLS)
    events = [e async for e in gateway(p).stream(req, [cand("a")])]
    assert p.complete_calls == 1 and events[-1].tool_calls[0]["id"] == "c1"
    assert not any(e.delta for e in events)


@pytest.mark.asyncio
async def test_idle_timeout_before_first_token_falls_back_to_next_candidate():
    slow = Scripted("slow", [StreamChunk(delta="x")], hang_after=-1)  # hangs before yielding
    slow.stream = _hang_forever(slow)
    fast = Scripted("fast", [StreamChunk(delta="ok"), StreamChunk(finish_reason="stop")])
    gw = gateway(slow, fast, stream_idle_timeout=0.05)
    c = [cand("slow"), cand("fast")]
    c[0] = ModelCandidate("slow", "m", frozenset({"chat"}), 8192, True, True, True,
                          reliability_score=0.9)
    events = [e async for e in gw.stream(ModelRequest(task_type="chat", prompt="x"), c)]
    assert "".join(e.delta for e in events) == "ok" and events[-1].provider_id == "fast"
    assert slow.closed  # connection released


def _hang_forever(provider):
    async def stream(candidate, request):
        try:
            await asyncio.sleep(3600)
            yield StreamChunk(delta="never")
        finally:
            provider.closed = True
    return stream


@pytest.mark.asyncio
async def test_idle_timeout_after_text_raises_interrupted_error_with_partial_text():
    p = Scripted("a", [StreamChunk(delta="part"), StreamChunk(delta="never")], hang_after=0)
    gw = gateway(p, stream_idle_timeout=0.05)
    seen = []
    with pytest.raises(StreamInterruptedError) as info:
        async for e in gw.stream(ModelRequest(task_type="chat", prompt="x"), [cand("a")]):
            seen.append(e.delta)
    assert seen == ["part"] and info.value.partial_text == "part"
    assert isinstance(info.value, TransientProviderError) and p.closed


@pytest.mark.asyncio
async def test_interrupted_error_preserves_rate_limit_hints():
    class Throttled(Scripted):
        async def stream(self, candidate, request):
            yield StreamChunk(delta="a")
            raise TransientProviderError("429", retry_after=12, rate_limited=True)

    gw = gateway(Throttled("a"))
    with pytest.raises(StreamInterruptedError) as info:
        async for _ in gw.stream(ModelRequest(task_type="chat", prompt="x"), [cand("a")]):
            pass
    assert info.value.rate_limited and info.value.retry_after == 12


@pytest.mark.asyncio
async def test_early_close_and_task_cancellation_close_the_provider_stream():
    p = Scripted("a", [StreamChunk(delta="1"), StreamChunk(delta="2"), StreamChunk(delta="3")])
    stream = gateway(p).stream(ModelRequest(task_type="chat", prompt="x"), [cand("a")])
    assert (await stream.__anext__()).delta == "1"
    await stream.aclose()
    assert p.closed

    q = Scripted("b", [StreamChunk(delta="1"), StreamChunk(delta="2")], hang_after=0)

    async def consume():
        async for _ in gateway(q).stream(ModelRequest(task_type="chat", prompt="x"), [cand("b")]):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert q.closed


def test_idle_timeout_validation():
    for bad in (0, -1, float("nan"), float("inf"), 4000):
        with pytest.raises(ValueError):
            gateway(stream_idle_timeout=bad)
    assert gateway(stream_idle_timeout=30).stream_idle_timeout == 30
