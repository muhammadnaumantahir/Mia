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
    respx.post("http://127.0.0.1:11434/api/show").mock(return_value=httpx.Response(404))
    respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"content": "Hello from local Mia model"}},
        )
    )
    provider = OllamaProvider()
    models = await provider.list_models()
    assert models[0].context_window == 8192  # /api/show unavailable: conservative default
    response = await provider.complete(
        models[0],
        ModelRequest(task_type="chat", prompt="Say hello"),
    )
    assert models[0].model_id == "qwen-small:latest"
    assert models[0].free_tier is True
    assert response.content == "Hello from local Mia model"


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
        data_local=True,
    )
    models = await provider.list_models()
    answer = await provider.complete(
        models[0],
        ModelRequest(task_type="chat", prompt="Hello"),
    )
    assert models[0].free_tier is False
    assert models[0].data_local is True
    assert answer.content == "Gateway response"
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



@pytest.mark.asyncio
@respx.mock
async def test_openai_compatible_adapter_returns_structured_tool_calls():
    route = respx.post("http://tools.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": None,
                        "tool_calls": [{
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "filesystem.list_directory",
                                "arguments": '{"root_id":"root_1"}',
                            },
                        }],
                    },
                }]
            },
        )
    )
    provider = OpenAICompatibleProvider(
        "tools-test",
        "http://tools.test/v1",
        free_tier=True,
        supports_tools=True,
    )
    candidate = ModelCandidate(
        provider_id="tools-test",
        model_id="tools-model",
        capabilities=frozenset({"chat", "tools"}),
        context_window=8192,
        free_tier=True,
        healthy=True,
        enabled=True,
    )
    response = await provider.complete(
        candidate,
        ModelRequest(
            task_type="chat",
            prompt="List files",
            tools=({
                "type": "function",
                "function": {
                    "name": "filesystem.list_directory",
                    "description": "List an allowed folder",
                    "parameters": {
                        "type": "object",
                        "properties": {"root_id": {"type": "string"}},
                        "required": ["root_id"],
                        "additionalProperties": False,
                    },
                },
            },),
        ),
    )
    assert response.tool_calls[0]["id"] == "call-1"
    assert response.tool_calls[0]["function"]["name"] == "filesystem.list_directory"
    request_json = httpx.Response(200, content=route.calls[0].request.content).json()
    assert request_json["tool_choice"] == "auto"
    assert request_json["tools"][0]["function"]["name"] == "filesystem.list_directory"


def _show(models):
    def handler(request):
        import json as _json

        name = _json.loads(request.content)["model"]
        return httpx.Response(200, json=models[name]) if name in models else httpx.Response(500)

    return handler


@pytest.mark.asyncio
@respx.mock
async def test_ollama_discovers_context_length_and_capabilities():
    respx.get("http://127.0.0.1:11434/api/tags").mock(return_value=httpx.Response(
        200, json={"models": [{"name": "big"}, {"name": "vis"}, {"name": "broken"}]}))
    respx.post("http://127.0.0.1:11434/api/show").mock(side_effect=_show({
        "big": {"model_info": {"llama.context_length": 131072},
                "capabilities": ["completion", "tools"]},
        "vis": {"model_info": {"general.architecture": "x", "x.context_length": 4096},
                "capabilities": ["completion", "vision"]},
    }))
    models = {m.model_id: m for m in await OllamaProvider(supports_tools=True).list_models()}
    assert models["big"].context_window == 131072 and "tools" in models["big"].capabilities
    assert models["vis"].context_window == 4096
    assert "vision" in models["vis"].capabilities and "tools" not in models["vis"].capabilities
    assert models["broken"].context_window == 8192  # one failing /api/show does not hide the model
    assert "tools" in models["broken"].capabilities  # no report: owner opt-in decides


@pytest.mark.asyncio
@respx.mock
async def test_ollama_tool_capability_stays_owner_gated_and_rejects_bad_context():
    respx.get("http://127.0.0.1:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "m"}]}))
    respx.post("http://127.0.0.1:11434/api/show").mock(return_value=httpx.Response(
        200, json={"model_info": {"llama.context_length": -5}, "capabilities": ["tools"]}))
    (model,) = await OllamaProvider(supports_tools=False).list_models()
    assert "tools" not in model.capabilities  # model supports it, owner has not enabled it
    assert model.context_window == 8192  # negative value ignored
