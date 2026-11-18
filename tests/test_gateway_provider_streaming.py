import json

import httpx
import pytest
import respx

from mia.gateway.contracts import (
    ModelCandidate,
    ModelRequest,
    PermanentProviderError,
    TokenUsage,
    TransientProviderError,
)
from mia.gateway.providers import OllamaProvider, OpenAICompatibleProvider

CAND = ModelCandidate(
    provider_id="x", model_id="m", capabilities=frozenset({"chat"}), context_window=8192,
    free_tier=True, healthy=True, enabled=True,
)
REQ = ModelRequest(task_type="chat", prompt="hi")


async def drain(provider):
    return [chunk async for chunk in provider.stream(CAND, REQ)]


@respx.mock
async def test_ollama_stream_parses_ndjson_and_usage():
    lines = [
        {"message": {"content": "Hel"}, "done": False},
        {"message": {"content": "lo"}, "done": False},
        {"message": {"content": ""}, "done": True, "done_reason": "stop",
         "prompt_eval_count": 9, "eval_count": 4},
    ]
    respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(200, text="\n".join(json.dumps(x) for x in lines))
    )
    chunks = await drain(OllamaProvider())
    assert "".join(c.delta for c in chunks) == "Hello"
    assert chunks[-1].usage == TokenUsage(9, 4) and chunks[-1].finish_reason == "stop"


@respx.mock
async def test_ollama_truncated_stream_is_transient():
    respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(200, text=json.dumps({"message": {"content": "a"}}))
    )
    with pytest.raises(TransientProviderError):
        await drain(OllamaProvider())


@respx.mock
async def test_ollama_complete_reports_usage():
    respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(200, json={
            "message": {"content": "ok"}, "prompt_eval_count": 3, "eval_count": 2})
    )
    response = await OllamaProvider().complete(CAND, REQ)
    assert response.usage == TokenUsage(3, 2)


@respx.mock
async def test_openai_stream_parses_sse_and_usage():
    body = "\n\n".join([
        'data: ' + json.dumps({"choices": [{"delta": {"content": "A"}}]}),
        'data: ' + json.dumps({"choices": [{"delta": {"content": "B"}, "finish_reason": "stop"}]}),
        'data: ' + json.dumps({"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}),
        "data: [DONE]",
    ])
    respx.post("http://gw.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=body)
    )
    provider = OpenAICompatibleProvider("gw", "http://gw.test/v1")
    chunks = await drain(provider)
    assert "".join(c.delta for c in chunks) == "AB"
    assert chunks[-1].usage == TokenUsage(5, 2) and chunks[-1].finish_reason == "stop"


@respx.mock
async def test_openai_stream_http_errors_are_classified():
    provider = OpenAICompatibleProvider("gw", "http://gw.test/v1")
    route = respx.post("http://gw.test/v1/chat/completions")
    route.mock(return_value=httpx.Response(429))
    with pytest.raises(TransientProviderError):
        await drain(provider)
    route.mock(return_value=httpx.Response(400))
    with pytest.raises(PermanentProviderError):
        await drain(provider)


@respx.mock
async def test_openai_stream_rejects_garbage_event():
    respx.post("http://gw.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, text="data: {not json")
    )
    with pytest.raises(PermanentProviderError):
        await drain(OpenAICompatibleProvider("gw", "http://gw.test/v1"))
