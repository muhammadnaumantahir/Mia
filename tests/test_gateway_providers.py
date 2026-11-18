import httpx
import pytest
import respx

from mia.gateway.contracts import ModelCandidate, ModelRequest
from mia.gateway.providers import OllamaProvider, OpenAICompatibleProvider
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway


@pytest.mark.asyncio
@respx.mock
async def test_ollama_lists_models_and_completes_locally():
    respx.get("http://127.0.0.1:11434/api/tags").mock(
        return_value=httpx.Response(
            200,
            json={"models": [{"name": "qwen-small:latest"}]},
        )
    )
    respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"content": "Hello from local Mia model"}},
        )
    )
    provider = OllamaProvider()
    models = await provider.list_models()
    response = await provider.complete(
        models[0],
        ModelRequest(task_type="chat", prompt="Say hello"),
    )
    assert models[0].model_id == "qwen-small:latest"
    assert models[0].free_tier is True
    assert response == "Hello from local Mia model"


@pytest.mark.asyncio
@respx.mock
async def test_openai_compatible_adapter_supports_local_freellmapi_endpoint():
    respx.get("http://127.0.0.1:3001/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "auto"}]})
    )
    request_route = respx.post(
        "http://127.0.0.1:3001/v1/chat/completions"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Gateway response"}}]},
        )
    )
    provider = OpenAICompatibleProvider(
        "freellmapi",
        "http://127.0.0.1:3001/v1",
        api_key="test-only-key",
        free_tier=False,
    )
    models = await provider.list_models()
    answer = await provider.complete(
        models[0],
        ModelRequest(task_type="chat", prompt="Hello"),
    )
    assert models[0].free_tier is False
    assert answer == "Gateway response"
    assert request_route.calls[0].request.headers["Authorization"] == "Bearer test-only-key"


@pytest.mark.asyncio
@respx.mock
async def test_gateway_falls_back_after_transient_provider_failure():
    respx.post("http://provider-a.test/v1/chat/completions").mock(
        return_value=httpx.Response(503, json={"error": "busy"})
    )
    respx.post("http://provider-b.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Fallback worked"}}]},
        )
    )
    registry = ProviderRegistry()
    registry.register(
        OpenAICompatibleProvider("provider-a", "http://provider-a.test/v1", free_tier=True)
    )
    registry.register(
        OpenAICompatibleProvider("provider-b", "http://provider-b.test/v1", free_tier=True)
    )
    candidates = [
        ModelCandidate(
            provider_id=provider_id,
            model_id="model",
            capabilities=frozenset({"chat"}),
            context_window=8192,
            free_tier=True,
            healthy=True,
            enabled=True,
            reliability_score=0.9 if provider_id == "provider-a" else 0.8,
        )
        for provider_id in ("provider-a", "provider-b")
    ]
    result = await ModelGateway(registry).complete(
        ModelRequest(task_type="chat", prompt="hello"),
        candidates,
    )
    assert result.provider_id == "provider-b"
    assert result.content == "Fallback worked"
    assert result.attempted_candidates == (
        "provider-a/model",
        "provider-b/model",
    )
