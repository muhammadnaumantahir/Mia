"""Scoped research with provenance, verified citations and owner review (MIA-LIFE-07, A7).

Builds on the fetch boundary in ``mia.life.web`` (owner host allowlist, HTTPS only, per-hop
redirect checks, public-network guard, size cap, markup stripped). This module adds what a
research *answer* needs:

* **Provenance**: every source records final URL, fetch time, content hash, page date (only
  when the page itself declares one) and where that date came from.
* **Comparison**: sources get an explainable readiness score. It is a heuristic about how
  checkable a page is (dated, recent, readable, not injection-laden), never a truth rating.
* **Verified citations**: a claim is stored only if each citation's quote appears verbatim in
  the stored text of a source in the same run. Nothing is cited from memory.
* **Untrusted content**: page text is data. Instruction-like passages are flagged, can never be
  cited as evidence, and nothing here executes or follows page content.
* **Owner review**: claims start as ``proposed``; only an owner ``confirm`` makes one a finding.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from mia.computer.control import AutomationStopped
from mia.life.web import FetchedPage, FetchError, HttpxFetcher, extract_text

MAX_URLS = 5
MAX_QUOTE = 500
MIN_QUOTE = 12
MAX_CITATIONS = 5
MAX_CLAIMS_PER_RUN = 50
MAX_SEARCH_RESULTS = 10
MAX_SYNTHESIS_INPUT_CHARS = 20_000
MAX_SYNTHESIS_CLAIMS = 10
FETCH_TIMEOUT = 10.0

# Phrases that address an AI reader or try to steer one. A hit never blocks reading the page;
# it marks the passage so it cannot be cited, and tells the owner the page tried to give orders.
_INJECTION_PATTERNS = (
    ("override_instructions", r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|any|your)\b[^.\n]{0,30}\b(instructions?|rules?|prompts?|guidelines?)\b"),
    ("role_reassignment", r"\byou are (now|no longer)\b|\bact as (an?|the)\b[^.\n]{0,30}\b(assistant|ai|agent|system)\b|\bnew instructions?\b"),
    ("prompt_exfiltration", r"\b(reveal|show|print|repeat|output|leak)\b[^.\n]{0,40}\b(system prompt|hidden prompt|instructions|api key|password|secret|token|credentials?)\b"),
    ("chat_markup", r"<\|?(im_start|im_end|system|assistant)\|?>|^\s*(system|assistant)\s*:|\[/?INST\]"),
    ("run_command", r"\b(run|execute|paste|type)\b[^.\n]{0,30}\b(this|the following)\b[^.\n]{0,20}\b(command|script|code)\b|\b(curl|wget|powershell|rm -rf|sudo)\b[^\n]{0,60}"),
    ("send_data", r"\b(send|post|email|upload|forward|submit)\b[^.\n]{0,50}\b(to|at)\b[^.\n]{0,20}(https?://|\S+@\S+)"),
    ("conceal_from_user", r"\b(do not|don't|never)\b[^.\n]{0,20}\b(tell|inform|show|mention)\b[^.\n]{0,20}\b(the )?(user|owner)\b"),
    ("address_to_ai", r"\b(attention|note to|message for|instructions? for)\b[^.\n]{0,20}\b(ai|assistant|language model|llm|agent|chatbot)\b"),
)
_COMPILED = tuple((name, re.compile(rx, re.IGNORECASE | re.MULTILINE)) for name, rx in _INJECTION_PATTERNS)


def detect_injection(text: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Instruction-like spans in untrusted text: [{pattern, start, end, excerpt}]."""
    spans: list[dict[str, Any]] = []
    for name, rx in _COMPILED:
        for match in rx.finditer(text):
            spans.append({"pattern": name, "start": match.start(), "end": match.end(),
                          "excerpt": text[match.start():match.end()][:160]})
            if len(spans) >= limit:
                return sorted(spans, key=lambda s: s["start"])
    return sorted(spans, key=lambda s: s["start"])


class _MetaDates(HTMLParser):
    """Collect dates a page declares about itself (meta tags, JSON-LD keys, <time>)."""

    _META_KEYS = {"article:published_time", "og:article:published_time", "datepublished", "date",
                  "dc.date", "dc.date.issued", "publishdate", "pubdate", "article:modified_time",
                  "datemodified", "last-modified"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str]] = []
        self._script = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key in self._META_KEYS and a.get("content"):
                self.found.append((f"meta:{key}", a["content"]))
        elif tag == "time" and a.get("datetime"):
            self.found.append(("time", a["datetime"]))
        elif tag == "script" and "ld+json" in a.get("type", "").lower():
            self._script, self._buffer = True, []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script:
            self._script = False
            blob = "".join(self._buffer)
            for key in ("datePublished", "dateModified"):
                m = re.search(rf'"{key}"\s*:\s*"([^"]+)"', blob)
                if m:
                    self.found.append((f"jsonld:{key}", m.group(1)))

    def handle_data(self, data: str) -> None:
        if self._script:
            self._buffer.append(data)


class _SearchResultParser(HTMLParser):
    """Extract result anchor titles and hrefs; snippets/page instructions are ignored."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or self._current is not None:
            return
        values = {key.lower(): value or "" for key, value in attrs}
        classes = set(values.get("class", "").split())
        if "result__a" in classes and values.get("href"):
            self._current = {"url": values["href"], "title": ""}

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["title"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current is not None:
            self._current["title"] = _norm(self._current["title"])[:200]
            self.results.append(self._current)
            self._current = None


class DuckDuckGoSearchProvider:
    """Small, read-only HTML search adapter; the search host itself must be owner-allowed."""

    search_url = "https://html.duckduckgo.com/html/"

    def __init__(self, fetcher: Any) -> None:
        self.fetcher = fetcher

    def search(
        self,
        query: str,
        *,
        limit: int,
        host_allowed: Callable[[str], bool],
        should_stop: Callable[[], bool],
    ) -> list[dict[str, str]]:
        url = self.search_url + "?" + urlencode({"q": query})
        page = self.fetcher.fetch(
            url, host_allowed=host_allowed, timeout=FETCH_TIMEOUT, should_stop=should_stop
        )
        charset = re.search(r"charset=([\w-]+)", page.content_type)
        try:
            raw = page.body.decode(charset.group(1) if charset else "utf-8", errors="replace")
        except LookupError:
            raw = page.body.decode("utf-8", errors="replace")
        parser = _SearchResultParser()
        parser.feed(raw)
        parser.close()

        found: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in parser.results:
            target = urljoin(page.url, item["url"])
            parsed = urlsplit(target)
            if parsed.hostname in {"duckduckgo.com", "www.duckduckgo.com"}:
                wrapped = parse_qs(parsed.query).get("uddg", [])
                if not wrapped:
                    continue
                target = unquote(wrapped[0])
            try:
                target = normalise_research_url(target)
            except ValueError:
                continue
            host = urlsplit(target).hostname or ""
            if host == "html.duckduckgo.com" or not host_allowed(host) or target in seen:
                continue
            seen.add(target)
            found.append({"url": target, "host": host, "title": item["title"]})
            if len(found) >= limit:
                break
        return found


def _parse_date(raw: str) -> date | None:
    m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", raw)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def extract_page_date(page: FetchedPage, *, today: date) -> tuple[str | None, str | None]:
    """(ISO date, where it came from). Only dates the page declares; never guessed. Future dates ignored."""
    if page.content_type.startswith("text/plain"):
        return None, None
    parser = _MetaDates()
    try:
        parser.feed(page.body.decode("utf-8", errors="replace"))
        parser.close()
    except Exception:  # malformed markup must not break research
        return None, None
    candidates = [(src, d) for src, raw in parser.found if (d := _parse_date(raw)) and d <= today]
    if not candidates:
        return None, None
    # Prefer publication over modification dates.
    candidates.sort(key=lambda c: ("modified" in c[0].lower(), c[1]))
    src, chosen = candidates[0]
    return chosen.isoformat(), src


def score_source(*, text: str, page_date: str | None, truncated: bool, injection_count: int,
                 today: date) -> tuple[int, list[str]]:
    """Explainable 0-100 readiness score. It measures checkability, not correctness."""
    score, reasons = 50, ["owner-allowlisted host, fetched over HTTPS"]
    if page_date:
        age = (today - date.fromisoformat(page_date)).days / 365.25
        if age <= 2:
            score += 20
            reasons.append(f"dated {page_date} (about {age:.1f} years old, +20)")
        elif age <= 5:
            score += 5
            reasons.append(f"dated {page_date} (about {age:.1f} years old, +5)")
        else:
            score -= 10
            reasons.append(f"dated {page_date}, over 5 years old (-10)")
    else:
        score -= 10
        reasons.append("page declares no date (-10)")
    if len(text) >= 1500:
        score += 10
        reasons.append("substantial readable text (+10)")
    elif len(text) < 300:
        score -= 15
        reasons.append("very little readable text (-15)")
    if truncated:
        score -= 10
        reasons.append("page was larger than the read limit (-10)")
    if injection_count:
        score -= min(40, 15 * injection_count)
        reasons.append(f"{injection_count} instruction-like passage(s) aimed at an AI reader")
    return max(0, min(100, score)), reasons


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _now() -> datetime:
    return datetime.now(UTC)


class ResearchError(ValueError):
    """Invalid request, refused citation or unknown id."""


class ResearchService:
    def __init__(self, database_path: str | Path, *, source_allowed: Callable[[str], bool],
                 fetcher: Any | None = None, searcher: Any | None = None,
                 should_stop: Callable[[], bool] = lambda: False,
                 clock: Callable[[], datetime] = _now) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.source_allowed = source_allowed
        self.fetcher = fetcher or HttpxFetcher()
        self.searcher = searcher or DuckDuckGoSearchProvider(self.fetcher)
        self.should_stop = should_stop
        self.clock = clock
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS research_runs(
              run_id TEXT PRIMARY KEY, question TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_sources(
              source_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
              requested_url TEXT NOT NULL, url TEXT, host TEXT, title TEXT, fetched_at TEXT NOT NULL,
              sha256 TEXT, text TEXT, page_date TEXT, date_source TEXT, score INTEGER,
              score_reasons TEXT, injection TEXT, truncated INTEGER DEFAULT 0, failure TEXT);
            CREATE TABLE IF NOT EXISTS research_claims(
              claim_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
              text TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, reviewed_at TEXT);
            CREATE TABLE IF NOT EXISTS research_citations(
              claim_id TEXT NOT NULL REFERENCES research_claims(claim_id) ON DELETE CASCADE,
              source_id TEXT NOT NULL, quote TEXT NOT NULL, position INTEGER NOT NULL);
            """)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    # ---- bounded search discovery ----------------------------------------------------------
    def search(self, query: str, limit: int = MAX_SEARCH_RESULTS) -> dict[str, Any]:
        query = _norm(query or "")
        if not 3 <= len(query) <= 300:
            raise ResearchError("search query must be 3-300 characters")
        limit = max(1, min(int(limit), MAX_SEARCH_RESULTS))
        if self.should_stop():
            raise AutomationStopped("Research search stopped by owner")
        try:
            candidates = self.searcher.search(
                query, limit=limit, host_allowed=self.source_allowed,
                should_stop=self.should_stop,
            )
        except FetchError as exc:
            if exc.reason == "stopped":
                raise AutomationStopped("Research search stopped by owner") from exc
            raise ResearchError(f"search provider unavailable ({exc.reason})") from exc
        except Exception as exc:
            raise ResearchError(f"search provider unavailable ({type(exc).__name__})") from exc

        items: list[dict[str, str]] = []
        seen: set[str] = set()
        for candidate in candidates[: MAX_SEARCH_RESULTS * 3]:
            raw_url = candidate.get("url") if isinstance(candidate, dict) else candidate
            title = candidate.get("title", "") if isinstance(candidate, dict) else ""
            if not isinstance(raw_url, str):
                continue
            try:
                url = normalise_research_url(raw_url)
            except ValueError:
                continue
            host = urlsplit(url).hostname or ""
            if not self.source_allowed(host) or url in seen:
                continue
            seen.add(url)
            items.append({
                "url": url, "host": host, "title": _norm(str(title))[:200],
                "trust": "untrusted_search_result",
            })
            if len(items) >= limit:
                break
        return {"query": query, "items": items, "limit": limit,
                "provider": "duckduckgo_html" if isinstance(self.searcher, DuckDuckGoSearchProvider)
                else "configured_search_provider"}

    # ---- runs and sources ------------------------------------------------------------------
    def start_run(self, question: str, urls: list[str]) -> dict[str, Any]:
        question = _norm(question or "")
        if not 3 <= len(question) <= 300:
            raise ResearchError("question must be 3-300 characters")
        if not isinstance(urls, list) or not 1 <= len(urls) <= MAX_URLS:
            raise ResearchError(f"give 1-{MAX_URLS} source URLs")
        run_id = uuid.uuid4().hex
        today = self.clock().date()
        with self._db() as db:
            db.execute("INSERT INTO research_runs VALUES (?,?,?)", (run_id, question, self.clock().isoformat()))
        seen: set[str] = set()
        for url in urls:
            if not isinstance(url, str) or url in seen:
                continue
            seen.add(url)
            self._fetch_source(run_id, url, today)
        return self.get_run(run_id)

    def _fetch_source(self, run_id: str, url: str, today: date) -> None:
        source_id, fetched_at = uuid.uuid4().hex, self.clock().isoformat()
        base = (source_id, run_id, url[:2000], fetched_at)
        try:
            page = self.fetcher.fetch(url, host_allowed=self.source_allowed, timeout=FETCH_TIMEOUT,
                                      should_stop=self.should_stop)
        except FetchError as exc:
            self._insert_failure(base, exc.reason)
            return
        except Exception as exc:  # a broken fetcher must degrade to a recorded failure
            self._insert_failure(base, f"fetch_error:{type(exc).__name__}")
            return
        title, text = extract_text(page)
        if len(text) < 40:
            self._insert_failure(base, "no_readable_text")
            return
        page_date, date_source = extract_page_date(page, today=today)
        flags = detect_injection(text)
        score, reasons = score_source(text=text, page_date=page_date, truncated=page.truncated,
                                      injection_count=len(flags), today=today)
        with self._db() as db:
            db.execute(
                "INSERT INTO research_sources(source_id,run_id,requested_url,url,host,title,fetched_at,sha256,"
                "text,page_date,date_source,score,score_reasons,injection,truncated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (source_id, run_id, url[:2000], page.url, urlsplit(page.url).hostname, title,
                 fetched_at, hashlib.sha256(text.encode()).hexdigest(), text, page_date, date_source,
                 score, json.dumps(reasons), json.dumps(flags), int(page.truncated)))

    def _insert_failure(self, base: tuple, reason: str) -> None:
        with self._db() as db:
            db.execute("INSERT INTO research_sources(source_id,run_id,requested_url,fetched_at,failure) "
                       "VALUES (?,?,?,?,?)", (*base[:2], base[2], base[3], reason))

    @staticmethod
    def _source_view(row: sqlite3.Row, *, with_text: bool = False) -> dict[str, Any]:
        out = {
            "source_id": row["source_id"], "url": row["url"], "requested_url": row["requested_url"],
            "host": row["host"], "title": row["title"], "fetched_at": row["fetched_at"],
            "sha256": row["sha256"], "page_date": row["page_date"], "date_source": row["date_source"],
            "score": row["score"], "score_reasons": json.loads(row["score_reasons"] or "[]"),
            "injection_flags": json.loads(row["injection"] or "[]"),
            "truncated": bool(row["truncated"]), "failure": row["failure"], "trust": "untrusted",
            "text_chars": len(row["text"] or ""),
        }
        if with_text:
            out["text"] = row["text"]
        return out

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._db() as db:
            run = db.execute("SELECT * FROM research_runs WHERE run_id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(run_id)
            sources = [self._source_view(r) for r in db.execute(
                "SELECT * FROM research_sources WHERE run_id=? ORDER BY score DESC, fetched_at", (run_id,))]
        claims = self.claims(run_id=run_id)
        return {"run_id": run_id, "question": run["question"], "created_at": run["created_at"],
                "sources": sources, "claims": claims,
                "comparison_note": "Scores measure how checkable a page is (date, recency, readable text, "
                                   "no instructions aimed at an AI). They are not a measure of truth."}

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT r.run_id, r.question, r.created_at, "
                "(SELECT COUNT(*) FROM research_sources s WHERE s.run_id=r.run_id AND s.failure IS NULL) AS sources, "
                "(SELECT COUNT(*) FROM research_claims c WHERE c.run_id=r.run_id AND c.status='confirmed') AS findings "
                "FROM research_runs r ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),))
            return [dict(r) for r in rows]

    def source_text(self, run_id: str, source_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM research_sources WHERE run_id=? AND source_id=?",
                             (run_id, source_id)).fetchone()
        if row is None:
            raise KeyError(source_id)
        return self._source_view(row, with_text=True)

    def delete_run(self, run_id: str) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM research_runs WHERE run_id=?", (run_id,)).rowcount > 0

    # ---- claims, citations, review ---------------------------------------------------------
    def add_claim(self, run_id: str, text: str, citations: list[dict[str, str]]) -> dict[str, Any]:
        text = _norm(text or "")
        if not 5 <= len(text) <= 600:
            raise ResearchError("claim must be 5-600 characters")
        if not isinstance(citations, list) or not 1 <= len(citations) <= MAX_CITATIONS:
            raise ResearchError(f"a claim needs 1-{MAX_CITATIONS} citations")
        with self._db() as db:
            if db.execute("SELECT 1 FROM research_runs WHERE run_id=?", (run_id,)).fetchone() is None:
                raise KeyError(run_id)
            if db.execute("SELECT COUNT(*) FROM research_claims WHERE run_id=?", (run_id,)).fetchone()[0] >= MAX_CLAIMS_PER_RUN:
                raise ResearchError("this run already has the maximum number of claims")
            verified = []
            for index, cite in enumerate(citations, start=1):
                if not isinstance(cite, dict) or set(cite) - {"source_id", "quote"}:
                    raise ResearchError(f"citation {index}: only source_id and quote are allowed")
                quote = _norm(str(cite.get("quote", "")))
                if not MIN_QUOTE <= len(quote) <= MAX_QUOTE:
                    raise ResearchError(f"citation {index}: quote must be {MIN_QUOTE}-{MAX_QUOTE} characters")
                row = db.execute("SELECT * FROM research_sources WHERE run_id=? AND source_id=? AND failure IS NULL",
                                 (run_id, str(cite.get("source_id", "")))).fetchone()
                if row is None:
                    raise ResearchError(f"citation {index}: source is not part of this run")
                source_text = _norm(row["text"])
                position = source_text.find(quote)
                if position < 0:
                    raise ResearchError(f"citation {index}: the quote does not appear verbatim in the source")
                end = position + len(quote)
                for flag in detect_injection(source_text, limit=50):
                    if position < flag["end"] and end > flag["start"]:
                        raise ResearchError(
                            f"citation {index}: the quote overlaps an instruction-like passage and cannot be used as evidence")
                verified.append((row["source_id"], quote, index))
            claim_id = uuid.uuid4().hex
            db.execute("INSERT INTO research_claims VALUES (?,?,?,?,?,NULL)",
                       (claim_id, run_id, text, "proposed", self.clock().isoformat()))
            db.executemany("INSERT INTO research_citations VALUES (?,?,?,?)",
                           [(claim_id, sid, q, i) for sid, q, i in verified])
        return self.get_claim(claim_id)

    def get_claim(self, claim_id: str) -> dict[str, Any]:
        with self._db() as db:
            claim = db.execute("SELECT * FROM research_claims WHERE claim_id=?", (claim_id,)).fetchone()
            if claim is None:
                raise KeyError(claim_id)
            cites = db.execute(
                "SELECT c.quote, s.source_id, s.url, s.host, s.title, s.fetched_at, s.sha256, s.page_date, s.date_source "
                "FROM research_citations c JOIN research_sources s ON s.source_id=c.source_id "
                "WHERE c.claim_id=? ORDER BY c.position", (claim_id,)).fetchall()
        return {"claim_id": claim["claim_id"], "run_id": claim["run_id"], "text": claim["text"],
                "status": claim["status"], "created_at": claim["created_at"],
                "reviewed_at": claim["reviewed_at"], "citations": [dict(c) for c in cites]}

    def claims(self, *, run_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        where, params = [], []
        if run_id:
            where.append("run_id=?")
            params.append(run_id)
        if status:
            where.append("status=?")
            params.append(status)
        with self._db() as db:
            ids = [r["claim_id"] for r in db.execute(
                "SELECT claim_id FROM research_claims" + (" WHERE " + " AND ".join(where) if where else "")
                + " ORDER BY created_at", params)]
        return [self.get_claim(i) for i in ids]

    def review_claim(self, claim_id: str, decision: str) -> dict[str, Any]:
        if decision not in {"confirm", "reject"}:
            raise ResearchError("decision must be confirm or reject")
        claim = self.get_claim(claim_id)
        if claim["status"] != "proposed":
            raise ResearchError(f"this claim was already {claim['status']}")
        with self._db() as db:
            db.execute("UPDATE research_claims SET status=?, reviewed_at=? WHERE claim_id=?",
                       ("confirmed" if decision == "confirm" else "rejected", self.clock().isoformat(), claim_id))
        return self.get_claim(claim_id)

    def findings(self) -> list[dict[str, Any]]:
        """Owner-confirmed claims, each with its verified citations and source provenance."""
        return self.claims(status="confirmed")



class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=3, max_length=300)
    urls: list[str] = Field(min_length=1, max_length=MAX_URLS)


class CitationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=64)
    quote: str = Field(min_length=1, max_length=MAX_QUOTE * 2)


class ClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    citations: list[CitationInput] = Field(min_length=1, max_length=MAX_CITATIONS)


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern="^(confirm|reject)$")


def register_research_routes(app: Any, service: ResearchService, record: Callable[..., Any],
                             activity: Callable[[str], Any] | None = None) -> None:
    """Routes on the app itself (not include_router) so route enumeration stays flat."""
    def fail(exc: Exception) -> HTTPException:
        if isinstance(exc, KeyError):
            return HTTPException(status_code=404, detail="Unknown research item")
        return HTTPException(status_code=422, detail=str(exc))

    def run_with_activity(question: str, urls: list[str]) -> dict[str, Any]:
        if activity is None:
            return service.start_run(question, urls)
        with activity(f"Reading {len(urls)} allowed source(s) for: {question[:60]}"):
            return service.start_run(question, urls)

    @app.post("/api/research/runs", status_code=201)
    async def start(item: RunInput) -> dict[str, Any]:
        try:
            run = await run_in_threadpool(run_with_activity, item.question, item.urls)
        except AutomationStopped as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ResearchError as exc:
            raise fail(exc) from exc
        ok = sum(1 for s in run["sources"] if not s["failure"])
        record(event_type="research_run", summary=f"Research run: {ok}/{len(run['sources'])} sources read",
               outcome="success" if ok else "failure", payload={"run_id": run["run_id"]})
        return run

    @app.get("/api/research/runs")
    def runs(limit: int = 50) -> dict[str, Any]:
        return {"items": service.list_runs(limit)}

    @app.get("/api/research/findings")
    def findings() -> dict[str, Any]:
        return {"items": service.findings()}

    @app.get("/api/research/runs/{run_id}")
    def run_detail(run_id: str) -> dict[str, Any]:
        try:
            return service.get_run(run_id)
        except KeyError as exc:
            raise fail(exc) from exc

    @app.get("/api/research/runs/{run_id}/sources/{source_id}")
    def source_detail(run_id: str, source_id: str) -> dict[str, Any]:
        try:
            return service.source_text(run_id, source_id)
        except KeyError as exc:
            raise fail(exc) from exc

    @app.post("/api/research/runs/{run_id}/claims", status_code=201)
    def add_claim(run_id: str, item: ClaimInput) -> dict[str, Any]:
        try:
            return service.add_claim(run_id, item.text, [c.model_dump() for c in item.citations])
        except (KeyError, ResearchError) as exc:
            raise fail(exc) from exc

    @app.post("/api/research/claims/{claim_id}/review")
    def review(claim_id: str, item: ReviewInput) -> dict[str, Any]:
        try:
            claim = service.review_claim(claim_id, item.decision)
        except (KeyError, ResearchError) as exc:
            raise fail(exc) from exc
        record(event_type="research_claim_reviewed", summary=f"Owner {item.decision}ed a research claim",
               outcome="success", payload={"claim_id": claim_id})
        return claim

    @app.delete("/api/research/runs/{run_id}")
    def delete_run(run_id: str) -> dict[str, bool]:
        return {"deleted": service.delete_run(run_id)}
