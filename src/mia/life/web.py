"""Scoped, read-only web exploration for the autonomous cycle (permission level 1).

Only exact hostnames the owner added to the source allowlist can be fetched, over
HTTPS on the default port, with no credentials or cookies. Every redirect hop is
re-checked against the allowlist and the public-network guard. Page text is
treated purely as untrusted data: it is size-capped, stripped of markup, stored as
an ``untrusted`` research note with its source and content hash, and never
interpreted as instructions. Nothing is posted, submitted or executed.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from time import monotonic
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from mia.computer.browser import BrowserAccessError, validate_public_url
from mia.life.runner import NO_MODEL_RESOURCE, CycleContext, ExplorationResult, MemoryReviewExplorer
from mia.life.store import LifeStore, normalise_source_host
from mia.memory.store import MemoryStore

MAX_BYTES = 500_000
MAX_NOTE_CHARS = 20_000
MAX_REDIRECTS = 3
ALLOWED_CONTENT_TYPES = ("text/html", "text/plain", "application/xhtml+xml")
PERMANENT_FAILURES = frozenset({
    "source_not_permitted", "redirect_not_permitted", "blocked_target", "unsupported_content_type",
    "http_401", "http_403", "http_404", "http_410", "invalid_url", "no_readable_text", "bad_redirect",
})
USER_AGENT = "MiaResearch/0.1 (read-only personal assistant; contact: owner)"


class FetchError(Exception):
    """A fetch was refused or failed; `reason` is a short stable code (never page content)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def normalise_research_url(url: str) -> str:
    """Return a canonical https URL or raise ValueError. Used at creation and again at fetch."""
    parsed = urlsplit(url.strip())
    if parsed.scheme != "https":
        raise ValueError("research URLs must use https")
    if parsed.username or parsed.password:
        raise ValueError("research URLs must not contain credentials")
    if parsed.port not in (None, 443):
        raise ValueError("research URLs must use the default https port")
    host = normalise_source_host(parsed.hostname or "")
    if len(url) > 2000:
        raise ValueError("research URL is too long")
    path = parsed.path or "/"
    return f"https://{host}{path}" + (f"?{parsed.query}" if parsed.query else "")


@dataclass(frozen=True, slots=True)
class FetchedPage:
    url: str
    content_type: str
    body: bytes
    truncated: bool


class HttpxFetcher:
    """Manual-redirect HTTPS GET with per-hop scope checks and a streamed size cap."""

    def __init__(
        self,
        *,
        max_bytes: int = MAX_BYTES,
        max_redirects: int = MAX_REDIRECTS,
        url_validator: Callable[[str], str] = validate_public_url,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.url_validator = url_validator
        self.transport = transport

    def fetch(
        self,
        url: str,
        *,
        host_allowed: Callable[[str], bool],
        timeout: float,
        should_stop: Callable[[], bool],
    ) -> FetchedPage:
        current = url
        for hop in range(self.max_redirects + 1):
            try:
                current = normalise_research_url(current)
            except ValueError as exc:
                raise FetchError("invalid_url" if hop == 0 else "bad_redirect") from exc
            host = urlsplit(current).hostname or ""
            if not host_allowed(host):
                raise FetchError("source_not_permitted" if hop == 0 else "redirect_not_permitted")
            try:
                self.url_validator(current)
            except BrowserAccessError as exc:
                raise FetchError("blocked_target") from exc
            if should_stop():
                raise FetchError("stopped")
            # A fresh client per hop: no cookie jar, no connection reuse, no auth.
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(max(1.0, timeout)),
                    follow_redirects=False,
                    headers={"User-Agent": USER_AGENT, "Accept": ", ".join(ALLOWED_CONTENT_TYPES)},
                    transport=self.transport,
                    trust_env=False,
                ) as client, client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        target = response.headers.get("location", "")
                        if not target:
                            raise FetchError("bad_redirect")
                        current = urljoin(current, target)
                        continue
                    if response.status_code != 200:
                        raise FetchError(f"http_{response.status_code}")
                    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if content_type not in ALLOWED_CONTENT_TYPES:
                        raise FetchError("unsupported_content_type")
                    charset = response.charset_encoding or "utf-8"
                    chunks: list[bytes] = []
                    size, truncated = 0, False
                    for chunk in response.iter_bytes():
                        if should_stop():
                            raise FetchError("stopped")
                        room = self.max_bytes - size
                        if len(chunk) > room:
                            chunks.append(chunk[:room])
                            truncated = True
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                    return FetchedPage(current, f"{content_type}; charset={charset}", b"".join(chunks), truncated)
            except FetchError:
                raise
            except httpx.TimeoutException as exc:
                raise FetchError("timeout") from exc
            except httpx.HTTPError as exc:
                raise FetchError("network_error") from exc
        raise FetchError("too_many_redirects")


class _TextExtractor(HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "template", "svg", "iframe", "head"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        elif tag in self._SKIP:
            self._skip_depth += 1
        elif tag in {"p", "br", "li", "div", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            self.parts.append(data)


def extract_text(page: FetchedPage) -> tuple[str, str]:
    """(title, text) from a fetched page: markup stripped, whitespace collapsed, capped."""
    charset_match = re.search(r"charset=([\w-]+)", page.content_type)
    try:
        raw = page.body.decode(charset_match.group(1) if charset_match else "utf-8", errors="replace")
    except LookupError:
        raw = page.body.decode("utf-8", errors="replace")
    if page.content_type.startswith("text/plain"):
        title, text = "", raw
    else:
        parser = _TextExtractor()
        parser.feed(raw)
        parser.close()
        title, text = parser.title, "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    title = re.sub(r"\s+", " ", title).strip()[:200]
    return title, text[:MAX_NOTE_CHARS]


def item_url(item: dict[str, Any]) -> str | None:
    ref = item.get("source_ref") or {}
    return str(ref["ref"]) if ref.get("type") == "url" and ref.get("ref") else None


class WebExplorer:
    name = "web_research"
    permission_level = 1

    def __init__(self, life: LifeStore, memory: MemoryStore, fetcher: Any | None = None) -> None:
        self.life = life
        self.memory = memory
        self.fetcher = fetcher or HttpxFetcher()

    @staticmethod
    def _blocked(reason: str, summary: str, actions: list[str] | None = None) -> ExplorationResult:
        permanent = reason in PERMANENT_FAILURES
        return ExplorationResult(
            outcome="deferred",
            item_state="blocked" if permanent else "deferred",
            state_reason=reason,
            summary=summary,
            actions=actions or [],
            resource=dict(NO_MODEL_RESOURCE),
            result={"external_research": True, "failure": reason},
        )

    def explore(self, item: dict[str, Any], context: CycleContext) -> ExplorationResult:
        raw_url = item_url(item) or ""
        try:
            url = normalise_research_url(raw_url)
        except ValueError:
            return self._blocked("invalid_url", "The item's URL is not a valid https research URL.")
        host = urlsplit(url).hostname or ""
        if not self.life.source_allowed(host):
            return self._blocked(
                "source_not_permitted",
                f"{host} is not on the owner's research source list, so nothing was fetched.",
            )
        remaining = context.deadline - monotonic()
        if remaining < 1.0:
            return self._blocked("timeout", "Not enough time budget left to fetch.")
        try:
            page = self.fetcher.fetch(
                url,
                host_allowed=self.life.source_allowed,
                timeout=min(10.0, remaining - 0.5),
                should_stop=context.should_stop,
            )
        except FetchError as exc:
            if exc.reason == "stopped":
                return ExplorationResult(outcome="deferred", item_state="deferred", summary="Stopped early")
            return self._blocked(
                exc.reason, f"Fetch from {host} did not complete ({exc.reason}).", [f"http.get:{host}"]
            )

        title, text = extract_text(page)
        final_host = urlsplit(page.url).hostname or host
        actions = [f"http.get:{final_host}"]
        if len(text) < 40:
            return self._blocked("no_readable_text", f"{final_host} returned no readable text.", actions)

        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        for existing in self.memory.list_items(limit=500):
            if existing.get("source_uri") == page.url and (existing.get("metadata") or {}).get("sha256") == digest:
                return ExplorationResult(
                    outcome="completed",
                    item_state="done",
                    summary=f"{final_host} is unchanged since the stored note; nothing new was added.",
                    sources=[{"type": "url", "ref": page.url, "host": final_host,
                              "sha256": digest, "stored_item_id": existing["item_id"], "new": False}],
                    actions=actions,
                    result={"external_research": True, "stored_item_id": existing["item_id"], "new": False},
                )
        stored = self.memory.add_item(
            kind="research",
            title=(title or f"Page from {final_host}")[:200],
            content=text,
            source_uri=page.url,
            source_title=title or None,
            trust_level="untrusted",
            tags=["autonomous-research"],
            metadata={"sha256": digest, "life_item_id": item["item_id"], "bytes": len(page.body),
                      "truncated": page.truncated, "reviewed": False},
        )
        actions.append("memory.add_item:research(untrusted)")
        return ExplorationResult(
            outcome="completed",
            item_state="done",
            summary=(
                f"Fetched {final_host}: {len(page.body)} bytes{' (truncated)' if page.truncated else ''}, "
                "stored as an untrusted research note. Its content has not been reviewed or verified."
            ),
            sources=[{"type": "url", "ref": page.url, "host": final_host, "bytes": len(page.body),
                      "sha256": digest, "stored_item_id": stored, "trust": "untrusted", "new": True}],
            actions=actions,
            result={"external_research": True, "stored_item_id": stored, "new": True, "sha256": digest},
        )


class ResearchExplorer:
    """Items with a URL go to the scoped web explorer; everything else stays internal."""

    name = "research"
    permission_level = 1  # the highest level this explorer can use

    def __init__(self, life: LifeStore, memory: MemoryStore, fetcher: Any | None = None) -> None:
        self.memory_review = MemoryReviewExplorer(memory)
        self.web = WebExplorer(life, memory, fetcher)

    def permission_for(self, item: dict[str, Any]) -> int:
        return self.web.permission_level if item_url(item) else self.memory_review.permission_level

    def explore(self, item: dict[str, Any], context: CycleContext) -> ExplorationResult:
        if item_url(item):
            return self.web.explore(item, context)
        return self.memory_review.explore(item, context)
