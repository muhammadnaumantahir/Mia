"""Validation of provider base URLs (SSRF hardening).

Provider URLs come from owner configuration today and from an admin API later.
The checks here are purely syntactic: they do not resolve DNS, so they do not
defend against DNS rebinding. Loopback and private-network addresses are allowed
by default because local inference servers (Ollama) live there; callers that take
a URL from a less-trusted source pass ``allow_private=False``.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

_MAX_URL_LENGTH = 2048
_ALWAYS_BLOCKED_HOSTS = frozenset({"metadata.google.internal", "metadata", "instance-data"})


class UnsafeProviderURL(ValueError):
    """The provider URL is malformed or points at a disallowed destination."""


def validate_provider_url(url: str, *, allow_private: bool = True) -> str:
    """Return the URL without a trailing slash, or raise ``UnsafeProviderURL``.

    Always rejected: non-HTTP(S) schemes, embedded credentials, missing host,
    link-local / cloud-metadata, unspecified and multicast addresses.
    Rejected unless ``allow_private``: loopback, private and ``localhost`` hosts.
    """
    if not isinstance(url, str) or not url.strip():
        raise UnsafeProviderURL("provider URL is required")
    url = url.strip()
    if len(url) > _MAX_URL_LENGTH or any(ch in url for ch in "\r\n\t "):
        raise UnsafeProviderURL("provider URL is malformed")
    try:
        parts = urlsplit(url)
        port = parts.port  # raises ValueError for an invalid port
    except ValueError as exc:
        raise UnsafeProviderURL("provider URL is malformed") from exc
    if parts.scheme not in ("http", "https"):
        raise UnsafeProviderURL("provider URL must use http or https")
    if parts.username is not None or parts.password is not None:
        raise UnsafeProviderURL("provider URL must not contain credentials")
    if parts.query or parts.fragment:
        raise UnsafeProviderURL("provider URL must not contain a query or fragment")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeProviderURL("provider URL has no host")
    if port is not None and not 0 < port < 65536:
        raise UnsafeProviderURL("provider URL has an invalid port")
    if host in _ALWAYS_BLOCKED_HOSTS:
        raise UnsafeProviderURL("provider URL points at a blocked metadata host")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        if (
            address.is_link_local
            or address.is_unspecified
            or address.is_multicast
            or (address.is_reserved and not address.is_loopback)
        ):
            raise UnsafeProviderURL("provider URL points at a blocked address")
        if not allow_private and (address.is_loopback or address.is_private):
            raise UnsafeProviderURL("private and loopback provider addresses are not allowed")
    elif not allow_private and (host == "localhost" or host.endswith(".localhost")):
        raise UnsafeProviderURL("private and loopback provider addresses are not allowed")
    return url.rstrip("/")


_FORBIDDEN_HEADERS = frozenset(
    {"authorization", "host", "content-length", "content-type", "transfer-encoding", "connection"}
)
_HEADER_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def validate_extra_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """Validate owner-configured custom request headers.

    Credentials belong in the OS credential store and are sent only as the
    Authorization header, so that header (and framing headers) cannot be set here.
    """
    if not headers:
        return {}
    if len(headers) > 20:
        raise ValueError("too many custom headers")
    clean: dict[str, str] = {}
    for name, value in headers.items():
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or not name
            or len(name) > 100
            or not set(name) <= _HEADER_NAME_CHARS
        ):
            raise ValueError("invalid custom header name")
        if name.lower() in _FORBIDDEN_HEADERS:
            raise ValueError(f"custom header not allowed: {name}")
        if len(value) > 1000 or any(ch in value for ch in "\r\n\0"):
            raise ValueError(f"invalid custom header value for {name}")
        clean[name] = value
    return clean


def parse_timeout_seconds(raw: str | None, default: float) -> float:
    """Parse a request timeout; invalid, non-finite or out-of-range values use the default."""
    import math

    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if not math.isfinite(value) or not 1.0 <= value <= 600.0:
        return default
    return value
