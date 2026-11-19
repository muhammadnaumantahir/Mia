import httpx
import pytest
import respx

from mia.gateway.contracts import (
    AuthenticationProviderError,
    ModelCandidate,
    ModelRequest,
    PermanentProviderError,
)
from mia.gateway.providers import OllamaProvider, OpenAICompatibleProvider
from mia.gateway.urlpolicy import UnsafeProviderURL, validate_provider_url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "ftp://example.com/v1",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://user:secret@example.com/v1",
        "http://example.com/v1?key=abc",
        "http://example.com/v1#frag",
        "http:///v1",
        "http://example.com:99999/v1",
        "http://example.com/v1 x",
        "http://169.254.169.254/latest/meta-data",
        "http://[fe80::1]/v1",
        "http://[::ffff:169.254.169.254]/v1",
        "http://0.0.0.0:8000/v1",
        "http://224.0.0.1/v1",
        "http://metadata.google.internal/v1",
        "http://example.com/" + "a" * 3000,
    ],
)
def test_always_unsafe_urls_rejected(url):
    with pytest.raises(UnsafeProviderURL):
        validate_provider_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434",
        "http://localhost:3001/v1",
        "http://[::1]:8080/v1",
        "http://10.0.0.5/v1",
        "http://192.168.1.20:8000/v1",
    ],
)
def test_private_hosts_allowed_by_default_but_blocked_on_request(url):
    assert validate_provider_url(url) == url.rstrip("/")
    with pytest.raises(UnsafeProviderURL):
        validate_provider_url(url, allow_private=False)


def test_public_https_url_accepted_and_trailing_slash_stripped():
    assert validate_provider_url("https://api.example.com/v1/") == "https://api.example.com/v1"
    assert (
        validate_provider_url("https://api.example.com/v1", allow_private=False)
        == "https://api.example.com/v1"
    )


def test_providers_reject_unsafe_urls_at_construction():
    with pytest.raises(ValueError):
        OllamaProvider(base_url="http://169.254.169.254")
    with pytest.raises(ValueError):
        OpenAICompatibleProvider("x", "http://user:pw@example.com/v1")
    with pytest.raises(ValueError):
        OpenAICompatibleProvider("x", "http://127.0.0.1:1/v1", allow_private_url=False)


@pytest.mark.asyncio
@respx.mock
async def test_auth_failure_is_distinct_permanent_error_and_not_leaky():
    respx.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": "bad key sk-secret"})
    )
    provider = OpenAICompatibleProvider(
        "remote", "https://api.example.com/v1", api_key="sk-secret"
    )
    candidate = ModelCandidate("remote", "m", frozenset({"chat"}), 8192, True, True, True)
    with pytest.raises(AuthenticationProviderError) as info:
        await provider.complete(candidate, ModelRequest(task_type="chat", prompt="hi"))
    assert isinstance(info.value, PermanentProviderError)
    assert "sk-secret" not in str(info.value)
    assert route_count(respx) == 1  # not retried


def route_count(router) -> int:
    return len(router.calls)
