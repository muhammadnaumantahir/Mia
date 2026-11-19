"""Prepare and verify Mia's supervisor demo using the running local API.

Usage:
    python -m mia.supervisor_demo seed
    python -m mia.supervisor_demo check

The seed data is fictional, explicitly labelled [MIA DEMO], and never overwrites
or deletes existing owner memory. No provider credentials are read or displayed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEMO_PREFIX = "[MIA DEMO]"
DEFAULT_BASE_URL = f"http://127.0.0.1:{os.getenv('MIA_PORT', '8765')}"

DEMO_RECORDS: tuple[dict[str, Any], ...] = (
    {
        "record_type": "project",
        "title": f"{DEMO_PREFIX} Atlas Learning Portal project",
        "body": (
            "Atlas Learning Portal is a fictional university software project used "
            "only for this supervisor demonstration. Its sample scope is course "
            "announcements and attendance summaries. It must never contain real "
            "student names, student identifiers, credentials or private records."
        ),
        "details": {"demo_only": True, "fictional": True, "dataset": "supervisor-demo"},
    },
    {
        "record_type": "entity",
        "title": f"{DEMO_PREFIX} Atlas Learning Portal",
        "body": (
            "Atlas Learning Portal is the fictional sample project in Mia's "
            "supervisor demonstration. Keep this entity separate from the Mia "
            "assistant application and its local data stores."
        ),
        "details": {"demo_only": True, "fictional": True, "dataset": "supervisor-demo"},
    },
    {
        "record_type": "decision",
        "title": f"{DEMO_PREFIX} Atlas Learning Portal database decision",
        "body": (
            "For the fictional Atlas Learning Portal, the recorded design decision "
            "is to use PostgreSQL as the planned production relational database. "
            "Mia itself stores its local conversations, structured memories and "
            "observability data separately in SQLite. Do not confuse the sample "
            "project database with Mia's own local SQLite stores."
        ),
        "details": {
            "demo_only": True,
            "fictional": True,
            "dataset": "supervisor-demo",
            "rationale": (
                "Keep application data architecture separate from the assistant's "
                "local memory and observability infrastructure."
            ),
        },
    },
    {
        "record_type": "goal",
        "title": f"{DEMO_PREFIX} Demonstrate persistent and explainable memory",
        "body": (
            "Show that Mia can retrieve an earlier owner-provided decision in a "
            "separate conversation, identify its provenance, and expose the memory "
            "reference used to build the answer."
        ),
        "details": {"demo_only": True, "fictional": True, "dataset": "supervisor-demo"},
    },
    {
        "record_type": "episode",
        "title": f"{DEMO_PREFIX} Supervisor demo rehearsal",
        "body": (
            "This is a synthetic rehearsal record. The walkthrough should show "
            "persistent memory across chat sessions, retrieved source references, "
            "a manually approved read-only CPU and RAM check, and the Evolution "
            "Observatory. It is not evidence of an autonomous 30-day experiment."
        ),
        "details": {"demo_only": True, "fictional": True, "dataset": "supervisor-demo"},
    },
)


def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(
        base_url.rstrip("/") + path,
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=8) as response:
            raw = response.read(2_000_001)
    except HTTPError as exc:
        detail = exc.read(2000).decode("utf-8", errors="replace")
        raise RuntimeError(f"API returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Cannot reach Mia at {base_url}: {exc.reason}") from exc
    if len(raw) > 2_000_000:
        raise RuntimeError(f"API response was unexpectedly large: {path}")
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"API returned invalid JSON for {path}") from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"API returned an unexpected response for {path}")
    return result


def seed(base_url: str) -> int:
    health = request_json(base_url, "/health")
    if health.get("status") != "ok":
        raise RuntimeError("Mia's health endpoint did not report status=ok.")

    existing = request_json(base_url, "/api/memory/records?limit=100").get("items", [])
    known = {
        (item.get("record_type"), item.get("title"))
        for item in existing
        if isinstance(item, dict)
    }
    created = 0
    for record in DEMO_RECORDS:
        key = (record["record_type"], record["title"])
        if key in known:
            print(f"Already present: {record['title']}")
            continue
        payload = {
            **record,
            "provenance": "owner_provided",
            "confidence": 1.0,
            "evidence": [],
        }
        result = request_json(
            base_url,
            "/api/memory/records",
            method="POST",
            payload=payload,
        )
        created += 1
        print(f"Added: {record['title']} ({result.get('record_id', 'saved')})")

    print(f"\nDemo memory ready: {created} new, {len(DEMO_RECORDS) - created} already present.")
    print("All seeded records are fictional and visibly prefixed [MIA DEMO].")
    return 0


def check(base_url: str) -> int:
    failures = 0
    warnings = 0

    def report(label: str, message: str, state: str = "OK") -> None:
        nonlocal failures, warnings
        print(f"[{state:<4}] {label}: {message}")
        if state == "FAIL":
            failures += 1
        elif state == "WARN":
            warnings += 1

    try:
        health = request_json(base_url, "/health")
        if health.get("status") != "ok":
            report("API", f"unexpected health response: {health}", "FAIL")
            return 1
        report("API", f"{health.get('service', 'Mia')} responds at {base_url}")
    except RuntimeError as exc:
        report("API", str(exc), "FAIL")
        print("\nStart the app with run_demo.cmd, then run this check again.")
        return 1

    try:
        providers = request_json(base_url, "/api/gateway/providers").get("items", [])
        online = [
            item.get("provider_id", "unknown")
            for item in providers
            if isinstance(item, dict) and item.get("status") == "online"
        ]
        if online:
            report("AI provider", ", ".join(online))
        else:
            report(
                "AI provider",
                "none online; start Ollama with an installed model or enable a configured provider",
                "WARN",
            )
    except RuntimeError as exc:
        report("AI provider", str(exc), "WARN")

    try:
        records = request_json(base_url, "/api/memory/records?limit=100").get("items", [])
        demo_records = [
            item for item in records
            if isinstance(item, dict) and (item.get("title") or "").startswith(DEMO_PREFIX)
        ]
        report(
            "Demo memories",
            f"{len(demo_records)}/{len(DEMO_RECORDS)} labelled records available",
            "OK" if len(demo_records) >= len(DEMO_RECORDS) else "WARN",
        )
        if len(demo_records) < len(DEMO_RECORDS):
            print("       Run: python -m mia.supervisor_demo seed")
    except RuntimeError as exc:
        report("Demo memories", str(exc), "FAIL")

    try:
        tools = request_json(base_url, "/api/tools").get("items", [])
        resource_tool = any(
            isinstance(item, dict)
            and item.get("name") == "system.read_resources"
            and item.get("mode") == "read"
            for item in tools
        )
        report(
            "Read-only resource tool",
            "system.read_resources is registered" if resource_tool else "resource tool is unavailable",
            "OK" if resource_tool else "WARN",
        )
    except RuntimeError as exc:
        report("Read-only resource tool", str(exc), "WARN")

    try:
        voice = request_json(base_url, "/api/voice/status")
        stt = voice.get("speech_to_text", "not reported")
        report("Voice input", f"speech-to-text status: {stt}", "OK" if stt == "ready" else "WARN")
    except RuntimeError as exc:
        report("Voice input", str(exc), "WARN")

    print(f"\nPreflight result: {failures} failure(s), {warnings} warning(s).")
    if failures:
        return 1
    if warnings:
        print("Core app is reachable. Review warnings before relying on those demo sections.")
    else:
        print("Core interactive demo preflight passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed and preflight Mia's supervisor demo.")
    parser.add_argument("command", choices=("seed", "check"))
    parser.add_argument(
        "--base-url",
        default=os.getenv("MIA_DEMO_URL", DEFAULT_BASE_URL),
        help="Mia base URL (default: http://127.0.0.1:$MIA_PORT)",
    )
    args = parser.parse_args()
    try:
        return seed(args.base_url) if args.command == "seed" else check(args.base_url)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
