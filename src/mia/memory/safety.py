"""Content checks shared by every path that can write to Mia's memory or identity.

Mia must never keep credentials as ordinary memory. This is a conservative pattern
check, not a guarantee: it catches common credential shapes and password/key
assignments, so a failure to match does not prove text is safe.
"""

from __future__ import annotations

import re

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("API key (sk- style)", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+")),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE)),
    (
        "credential assignment",
        re.compile(
            r"\b(?:password|passwd|pwd|passphrase|secret|api[_ -]?key|access[_ -]?token|"
            r"auth[_ -]?token|private[_ -]?key|client[_ -]?secret)\b\s*(?:is|=|:)\s*"
            r"[\"']?[^\s\"']{6,}",
            re.IGNORECASE,
        ),
    ),
)

_CARD_CANDIDATE = re.compile(r"(?<![\d-])(?:\d[ -]?){13,19}(?![\d-])")


def _luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def find_secret(text: str) -> str | None:
    """Return a short description of the first credential-like pattern, or None."""
    for label, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            return label
    for match in _CARD_CANDIDATE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            return "payment card number"
    return None


def reject_secret(text: str, *, field: str = "text") -> None:
    """Raise ValueError (without echoing the value) if text looks like a credential."""
    label = find_secret(text)
    if label:
        raise ValueError(
            f"{field} looks like a {label}; Mia does not store credentials as memory"
        )
