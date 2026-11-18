"""Local Ollama and OpenAI-compatible provider adapters."""

from __future__ import annotations

import json
import uuid
from typing import Any

from .contracts import (
    ModelCandidate,
    ModelRequest,
    PermanentProviderError,
    ProviderResponse,
    TransientProviderError,
)


class OllamaProvider:
    """Local Ollama adapter using Ollama's native HTTP API."""

    provider_id = "ollama"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 90.0,
        supports_tools: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.supports_tools = supports_tools

    @staticmethod
    def _httpx():
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError(
                'Install Mia with the "gateway" extra to connect to model providers.'
            ) from exc
        return httpx

    async def health(self) -> bool:
        httpx = self._httpx()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                return response.is_success
        except httpx.HTTPError:
            return False

    async def list_models(self) -> list[ModelCandidate]:
        httpx = self._httpx()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            raise TransientProviderError("Ollama model listing timed out") from exc
        except httpx.HTTPError as exc:
            raise TransientProviderError("Ollama is unavailable") from exc
        except ValueError as exc:
            raise PermanentProviderError("Ollama returned invalid model metadata") from exc

        candidates = []
        for item in payload.get("models", []):
            model_name = item.get("name") or item.get("model")
            if not model_name:
                continue
            candidates.append(
                ModelCandidate(
                    provider_id=self.provider_id,
                    model_id=str(model_name),
                    capabilities=frozenset(
                        {"chat", "tools"} if self.supports_tools else {"chat"}
                    ),
                    context_window=8192,
                    free_tier=True,
                    data_local=True,
                    healthy=True,
                    enabled=True,
                    reliability_score=0.5,
                    latency_ms=10_000.0,
                )
            )
        return candidates

    async def complete(
        self,
        candidate: ModelCandidate,
        request: ModelRequest,
    ) -> ProviderResponse:
        httpx = self._httpx()
        body = {
            "model": candidate.model_id,
            "messages": list(request.messages) or [{"role": "user", "content": request.prompt}],
            "stream": False,
            "options": {"num_predict": request.max_output_tokens},
        }
        if request.tools and self.supports_tools:
            body["tools"] = list(request.tools)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=body)
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            raise TransientProviderError("Ollama request timed out") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 or exc.response.status_code == 429:
                raise TransientProviderError(
                    f"Ollama returned transient HTTP {exc.response.status_code}"
                ) from exc
            raise PermanentProviderError(
                f"Ollama rejected the request with HTTP {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise TransientProviderError("Ollama request failed") from exc
        except ValueError as exc:
            raise PermanentProviderError("Ollama returned invalid JSON") from exc

        message = payload.get("message", {})
        content = message.get("content")
        raw_calls = message.get("tool_calls") or []
        tool_calls = _normalise_tool_calls(raw_calls)
        if not isinstance(content, str):
            content = ""
        if not content and not tool_calls:
            raise PermanentProviderError("Ollama response did not contain text or tool calls")
        return ProviderResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=payload.get("done_reason"),
        )


class OpenAICompatibleProvider:
    """Adapter for OpenAI-compatible endpoints, including a local FreeLLMAPI sidecar.

    Do not set free_tier=True for a gateway unless its model/quota policy
    proves that the selected route is currently free. Local hosting alone does
    not make upstream inference free.
    """

    def __init__(
        self,
        provider_id: str,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = 90.0,
        free_tier: bool = False,
        model_context_window: int = 8192,
        supports_tools: bool = False,
        data_local: bool = False,
    ):
        if not provider_id.strip():
            raise ValueError("provider_id is required")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be an HTTP(S) URL")
        self.provider_id = provider_id.strip().lower()
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.free_tier = free_tier
        self.model_context_window = model_context_window
        self.supports_tools = supports_tools
        self.data_local = data_local

    @staticmethod
    def _httpx():
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError(
                'Install Mia with the "gateway" extra to connect to model providers.'
            ) from exc
        return httpx

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def health(self) -> bool:
        httpx = self._httpx()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{self.base_url}/models",
                    headers=self._headers(),
                )
                return response.is_success
        except httpx.HTTPError:
            return False

    async def list_models(self) -> list[ModelCandidate]:
        httpx = self._httpx()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.base_url}/models",
                    headers=self._headers(),
                )
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
        except httpx.TimeoutException as exc:
            raise TransientProviderError(
                f"{self.provider_id} model listing timed out"
            ) from exc
        except httpx.HTTPError as exc:
            raise TransientProviderError(
                f"{self.provider_id} model listing failed"
            ) from exc
        except ValueError as exc:
            raise PermanentProviderError(
                f"{self.provider_id} returned invalid model metadata"
            ) from exc

        candidates = []
        for item in payload.get("data", []):
            model_name = item.get("id")
            if not model_name:
                continue
            candidates.append(
                ModelCandidate(
                    provider_id=self.provider_id,
                    model_id=str(model_name),
                    capabilities=frozenset(
                        {"chat", "tools"} if self.supports_tools else {"chat"}
                    ),
                    context_window=self.model_context_window,
                    free_tier=self.free_tier,
                    data_local=self.data_local,
                    healthy=True,
                    enabled=True,
                )
            )
        return candidates

    async def complete(
        self,
        candidate: ModelCandidate,
        request: ModelRequest,
    ) -> ProviderResponse:
        httpx = self._httpx()
        body = {
            "model": candidate.model_id,
            "messages": list(request.messages) or [{"role": "user", "content": request.prompt}],
            "max_tokens": request.max_output_tokens,
            "stream": False,
        }
        if request.tools and self.supports_tools:
            body["tools"] = list(request.tools)
            body["tool_choice"] = "auto"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=body,
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            raise TransientProviderError(
                f"{self.provider_id} request timed out"
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429 or status >= 500:
                raise TransientProviderError(
                    f"{self.provider_id} returned transient HTTP {status}"
                ) from exc
            raise PermanentProviderError(
                f"{self.provider_id} rejected the request with HTTP {status}"
            ) from exc
        except httpx.HTTPError as exc:
            raise TransientProviderError(f"{self.provider_id} request failed") from exc
        except ValueError as exc:
            raise PermanentProviderError(
                f"{self.provider_id} returned invalid JSON"
            ) from exc

        try:
            choice = payload["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise PermanentProviderError(
                f"{self.provider_id} response did not contain a message"
            ) from exc

        content = message.get("content")
        tool_calls = _normalise_tool_calls(message.get("tool_calls") or [])
        if content is None:
            content = ""
        if not isinstance(content, str) or (not content and not tool_calls):
            raise PermanentProviderError(
                f"{self.provider_id} returned neither text nor structured tool calls"
            )
        return ProviderResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason"),
        )



def _normalise_tool_calls(calls: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Normalize Ollama/OpenAI tool calls to the OpenAI chat-completions shape."""
    normalized = []
    for call in calls[:8]:
        function = call.get("function") or {}
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        arguments = function.get("arguments", {})
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        normalized.append({
            "id": call.get("id") or f"call_{uuid.uuid4().hex}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": arguments,
            },
        })
    return tuple(normalized)
