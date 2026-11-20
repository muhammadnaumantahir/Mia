"""Local Ollama and OpenAI-compatible provider adapters."""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from .contracts import (
    AuthenticationProviderError,
    ModelCandidate,
    ModelRequest,
    PermanentProviderError,
    ProviderResponse,
    StreamChunk,
    TokenUsage,
    TransientProviderError,
)
from .urlpolicy import validate_extra_headers, validate_provider_url

_DEFAULT_CONTEXT = 8192
_MAX_DETAIL_MODELS = 30


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _usage(input_tokens: Any, output_tokens: Any) -> TokenUsage | None:
    prompt, completion = _count(input_tokens), _count(output_tokens)
    if prompt is None or completion is None:
        return None
    return TokenUsage(prompt, completion)


def _retry_after_seconds(response: Any) -> float | None:
    """Parse a Retry-After header (delta-seconds or HTTP-date); None if absent/invalid."""
    try:
        raw = response.headers.get("retry-after")
    except AttributeError:
        return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    raw = raw.strip()
    try:
        seconds = float(raw)
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def _http_error(name: str, exc: Exception, httpx: Any) -> Exception:
    if isinstance(exc, httpx.TimeoutException):
        return TransientProviderError(f"{name} request timed out")
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429 or status >= 500:
            retry_after = _retry_after_seconds(exc.response)
            return TransientProviderError(
                f"{name} returned transient HTTP {status}",
                retry_after=retry_after,
                rate_limited=status == 429 or retry_after is not None,
            )
        if status in (401, 403):
            # Never retried: repeating a rejected credential cannot succeed.
            return AuthenticationProviderError(f"{name} rejected the credentials (HTTP {status})")
        return PermanentProviderError(f"{name} rejected the request with HTTP {status}")
    return TransientProviderError(f"{name} request failed")


class OllamaProvider:
    """Local Ollama adapter using Ollama's native HTTP API."""

    streams_tools = True

    provider_id = "ollama"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 90.0,
        supports_tools: bool = False,
    ):
        self.base_url = validate_provider_url(base_url)
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
        async with httpx.AsyncClient(timeout=10.0) as detail_client:
            for item in list(payload.get("models", []))[:_MAX_DETAIL_MODELS]:
                model_name = item.get("name") or item.get("model")
                if not model_name:
                    continue
                context_window, reported = await self._model_details(
                    detail_client, str(model_name)
                )
                capabilities = {"chat"}
                # Tool calling stays owner-gated; when Ollama reports a capability
                # list, the model must also advertise tools.
                if self.supports_tools and (reported is None or "tools" in reported):
                    capabilities.add("tools")
                if reported and "vision" in reported:
                    capabilities.add("vision")
                candidates.append(
                    ModelCandidate(
                        provider_id=self.provider_id,
                        model_id=str(model_name),
                        capabilities=frozenset(capabilities),
                        context_window=context_window,
                        free_tier=True,
                        data_local=True,
                        healthy=True,
                        enabled=True,
                        reliability_score=0.5,
                        latency_ms=10_000.0,
                    )
                )
        return candidates

    async def _model_details(self, client: Any, model_name: str) -> tuple[int, set[str] | None]:
        """Read real context length and capabilities; fall back to conservative defaults."""
        default = (_DEFAULT_CONTEXT, None)
        try:
            response = await client.post(f"{self.base_url}/api/show", json={"model": model_name})
            response.raise_for_status()
            info = response.json()
        except (self._httpx().HTTPError, ValueError):
            return default
        if not isinstance(info, dict):
            return default
        context = _DEFAULT_CONTEXT
        model_info = info.get("model_info")
        if isinstance(model_info, dict):
            for key, value in model_info.items():
                if str(key).endswith(".context_length"):
                    count = _count(value)
                    if count and count >= 512:
                        context = count
                    break
        caps = info.get("capabilities")
        reported = {str(c) for c in caps} if isinstance(caps, list) else None
        return context, reported

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
        except httpx.HTTPError as exc:
            raise _http_error("Ollama", exc, httpx) from exc
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
            usage=_usage(payload.get("prompt_eval_count"), payload.get("eval_count")),
        )

    async def stream(
        self,
        candidate: ModelCandidate,
        request: ModelRequest,
    ) -> AsyncIterator[StreamChunk]:
        httpx = self._httpx()
        body = {
            "model": candidate.model_id,
            "messages": list(request.messages) or [{"role": "user", "content": request.prompt}],
            "stream": True,
            "options": {"num_predict": request.max_output_tokens},
        }
        if request.tools and self.supports_tools:
            body["tools"] = list(request.tools)
        raw_calls: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/api/chat", json=body
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            payload = json.loads(line)
                        except ValueError as exc:
                            raise PermanentProviderError(
                                "Ollama returned an invalid stream line"
                            ) from exc
                        message = payload.get("message") or {}
                        content = message.get("content")
                        for call in message.get("tool_calls") or []:
                            if isinstance(call, dict):
                                raw_calls.append(call)
                        if payload.get("done"):
                            yield StreamChunk(
                                delta=content if isinstance(content, str) else "",
                                tool_calls=_normalise_tool_calls(raw_calls),
                                finish_reason=payload.get("done_reason") or "stop",
                                usage=_usage(
                                    payload.get("prompt_eval_count"),
                                    payload.get("eval_count"),
                                ),
                            )
                            return
                        if isinstance(content, str) and content:
                            yield StreamChunk(delta=content)
        except httpx.HTTPError as exc:
            raise _http_error("Ollama", exc, httpx) from exc
        raise TransientProviderError("Ollama stream ended before completion")


class OpenAICompatibleProvider:
    """Adapter for OpenAI-compatible endpoints, including a local FreeLLMAPI sidecar.

    Do not set free_tier=True for a gateway unless its model/quota policy
    proves that the selected route is currently free. Local hosting alone does
    not make upstream inference free.
    """

    streams_tools = True  # assembles streamed tool-call fragments into whole calls

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
        allow_private_url: bool = True,
        extra_headers: dict[str, str] | None = None,
    ):
        if not provider_id.strip():
            raise ValueError("provider_id is required")
        base_url = validate_provider_url(base_url, allow_private=allow_private_url)
        self.provider_id = provider_id.strip().lower()
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.free_tier = free_tier
        self.model_context_window = model_context_window
        self.supports_tools = supports_tools
        self.data_local = data_local
        self.extra_headers = validate_extra_headers(extra_headers)

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
        headers = dict(self.extra_headers)
        headers["Content-Type"] = "application/json"
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
        except httpx.HTTPStatusError as exc:
            raise _http_error(self.provider_id, exc, httpx) from exc
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
        except httpx.HTTPError as exc:
            raise _http_error(self.provider_id, exc, httpx) from exc
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
        raw_usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        return ProviderResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason"),
            usage=_usage(raw_usage.get("prompt_tokens"), raw_usage.get("completion_tokens")),
        )

    async def stream(
        self,
        candidate: ModelCandidate,
        request: ModelRequest,
    ) -> AsyncIterator[StreamChunk]:
        httpx = self._httpx()
        body = {
            "model": candidate.model_id,
            "messages": list(request.messages) or [{"role": "user", "content": request.prompt}],
            "max_tokens": request.max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.tools and self.supports_tools:
            body["tools"] = list(request.tools)
            body["tool_choice"] = "auto"
        finish: str | None = None
        usage: TokenUsage | None = None
        pending_calls: dict[int, dict[str, str]] = {}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=body,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            yield StreamChunk(
                                finish_reason=finish or "stop",
                                usage=usage,
                                tool_calls=_assemble_stream_tool_calls(pending_calls),
                            )
                            return
                        try:
                            payload = json.loads(data)
                        except ValueError as exc:
                            raise PermanentProviderError(
                                f"{self.provider_id} returned an invalid stream event"
                            ) from exc
                        raw_usage = payload.get("usage")
                        if isinstance(raw_usage, dict):
                            usage = _usage(
                                raw_usage.get("prompt_tokens"),
                                raw_usage.get("completion_tokens"),
                            ) or usage
                        choices = payload.get("choices") or []
                        if not choices:
                            continue
                        choice = choices[0]
                        finish = choice.get("finish_reason") or finish
                        delta = choice.get("delta") or {}
                        _accumulate_stream_tool_calls(pending_calls, delta.get("tool_calls"))
                        content = delta.get("content")
                        if isinstance(content, str) and content:
                            yield StreamChunk(delta=content)
        except httpx.HTTPError as exc:
            raise _http_error(self.provider_id, exc, httpx) from exc
        raise TransientProviderError(f"{self.provider_id} stream ended before completion")



_MAX_STREAM_TOOL_CALLS = 8
_MAX_STREAM_TOOL_ARG_CHARS = 200_000


def _accumulate_stream_tool_calls(pending: dict[int, dict[str, str]], fragments: Any) -> None:
    """Merge OpenAI-style streamed tool-call fragments (by ``index``) into ``pending``.

    The name is taken once (first non-empty value wins, so servers that repeat it
    on every fragment are tolerated); argument text is concatenated. Bounded so a
    misbehaving server cannot grow memory without limit.
    """
    if not isinstance(fragments, list):
        return
    for fragment in fragments:
        if not isinstance(fragment, dict):
            continue
        index = fragment.get("index", 0)
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < _MAX_STREAM_TOOL_CALLS:
            raise PermanentProviderError("provider sent an invalid tool-call index")
        slot = pending.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if isinstance(fragment.get("id"), str) and fragment["id"] and not slot["id"]:
            slot["id"] = fragment["id"][:200]
        function = fragment.get("function")
        if isinstance(function, dict):
            if isinstance(function.get("name"), str) and function["name"] and not slot["name"]:
                slot["name"] = function["name"][:200]
            if isinstance(function.get("arguments"), str):
                slot["arguments"] += function["arguments"]
                if len(slot["arguments"]) > _MAX_STREAM_TOOL_ARG_CHARS:
                    raise PermanentProviderError("provider sent oversized tool-call arguments")


def _assemble_stream_tool_calls(pending: dict[int, dict[str, str]]) -> tuple[dict[str, Any], ...]:
    calls = [
        {"id": slot["id"] or None, "function": {"name": slot["name"], "arguments": slot["arguments"]}}
        for _, slot in sorted(pending.items())
    ]
    return _normalise_tool_calls(calls)


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
