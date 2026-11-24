"""Owner-facing experiment privacy notice and verifiable evidence export (MIA-OBS-08).

* ``privacy_notice`` tells the owner, before they start a 30-day run, exactly which fields Mia
  records, where they stay, what is never collected and which resource caps apply. Its field list is
  ``FIELD_DISCLOSURE``; a test runs the real baseline capture and a real snapshot and fails if either
  records a top-level field this table does not describe, so the notice cannot silently go stale.
* ``build_export`` wraps the hash-verified experiment report in a self-describing bundle with a
  content hash, and ``verify_export`` re-checks it offline (no Mia, no network):
  ``python -m mia.observatory.experiment_export verify FILE``.

Hashes use the same canonical JSON as the store (sorted keys, compact separators, UTF-8), so the
baseline and every daily snapshot can be re-verified independently of Mia.

What verification proves, and what it does not: it detects corruption and edits that leave the bundle
internally inconsistent (a changed snapshot or baseline, a snapshot from another baseline, an edited
summary). It is not a signature. Someone who rewrites the whole bundle consistently and recomputes every
hash cannot be detected from the file alone, and fields that are not themselves hashed (such as the
experiment name or conclusion) are only protected by ``content_sha256``. To anchor a bundle, write down
or separately store its ``content_sha256`` (or the baseline and snapshot hashes) when you export it.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .baseline import NOT_CAPTURED

PRIVACY_NOTICE_VERSION = 1
EXPORT_FORMAT = "mia-experiment-export"
EXPORT_VERSION = 1
CANONICALIZATION = "JSON, sorted keys, separators (',', ':'), ensure_ascii=false, UTF-8"

# Every top-level field the Day 0 baseline or a daily snapshot records -> (group, plain description).
FIELD_DISCLOSURE: dict[str, tuple[str, str]] = {
    "source_commit": ("Software version", "The git commit hash of the Mia code that was tested."),
    "configuration_hash": ("Software version", "A hash of Mia's configuration files and the non-secret settings below."),
    "benchmark_suite_id": ("Software version", "The name and version of the fixed benchmark suite."),
    "baseline_sha256": ("Software version", "A hash tying each daily snapshot to the Day 0 baseline."),
    "dependencies": ("Software version", "Installed versions of the Python packages Mia declares."),
    "runtime_settings": (
        "Settings",
        "Non-secret on/off flags and numbers (autonomy, CPU/RAM limits, spend limits, port). "
        "No file paths, web addresses or keys.",
    ),
    "environment": (
        "This computer",
        "Operating system and release, platform string, Python version, CPU model and count, "
        "machine type and total memory.",
    ),
    "resource_sample": ("This computer", "One CPU and memory percentage reading at capture time."),
    "resources": ("This computer", "CPU and memory percentage readings at each daily capture."),
    "baseline_results": (
        "Test results",
        "Counts, exit codes, durations and hashes of the test and lint runs (not their output), plus "
        "this computer's host name (capture_host).",
    ),
    "verification": ("Test results", "The same test and lint summary, repeated in each daily snapshot."),
    "fixed_benchmark_results": ("Test results", "Counts, exit code, duration and output hashes of the fixed benchmark."),
    "verification_passed": ("Test results", "Whether every check passed."),
    "not_captured": ("Honest gaps", "A list of things deliberately not measured on Day 0, with the reason."),
    "captured_at": ("Timing", "When the snapshot was taken."),
    "day_index": ("Timing", "Which experiment day the snapshot belongs to."),
    "missing_day_indices": ("Timing", "Days known not to have been captured."),
    "activity": (
        "Usage counts",
        "Counts and totals for the day's window: model requests, tokens, cost, latency and provider/model "
        "names; capability and task event counts. Counts only, never the content.",
    ),
}

NEVER_COLLECTED = [
    "What you say to Mia or what Mia says back (conversation text)",
    "Memory contents, notes, tasks or documents",
    "Contents of your files, screen, camera or microphone",
    "API keys, passwords or other credentials (provider keys stay in your operating system's credential store)",
    "File paths and web addresses (they are removed from the recorded settings)",
    "Anything about other people",
]


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def privacy_notice(scheduler_status: dict[str, Any]) -> dict[str, Any]:
    """The notice shown before the owner starts a run; caps come from the live scheduler."""
    groups: dict[str, list[dict[str, str]]] = {}
    for field, (group, description) in FIELD_DISCLOSURE.items():
        groups.setdefault(group, []).append({"field": field, "what": description})
    return {
        "version": PRIVACY_NOTICE_VERSION,
        "summary": (
            "A 30-day run records a small set of measurements about Mia's software, this computer's "
            "capacity and Mia's usage counts, once a day, so you can see whether Mia improves. It never "
            "records what you say or any of your content."
        ),
        "stored": "Only on this computer, in Mia's local observatory database. Nothing is uploaded; "
                  "the daily capture opens no network connections.",
        "collected": [{"group": name, "fields": items} for name, items in groups.items()],
        "never_collected": list(NEVER_COLLECTED),
        "not_captured_day0": dict(NOT_CAPTURED),
        "controls": {
            "start": "Nothing starts until you press start for a prepared experiment.",
            "pause_resume": "Pause or resume automatic daily capture at any time; a paused day is recorded as paused.",
            "stop": "Stop ends the run early and keeps everything recorded so far; it is never reported as completed.",
            "export": "Export gives you a verifiable file of the evidence. Deleting data is done by deleting Mia's "
                      "local data files.",
        },
        "resource_caps": {
            "max_cpu_percent": scheduler_status.get("max_cpu_percent"),
            "max_memory_percent": scheduler_status.get("max_memory_percent"),
            "check_interval_seconds": scheduler_status.get("interval_seconds"),
            "retry_seconds": scheduler_status.get("retry_seconds"),
            "behaviour": "A daily capture is deferred, not forced, while CPU or memory is at or above these limits.",
        },
        "automatic_capture_enabled": bool(scheduler_status.get("enabled")),
    }


def _content(report: dict[str, Any], notice: dict[str, Any], final: bool) -> dict[str, Any]:
    return {"format": EXPORT_FORMAT, "version": EXPORT_VERSION, "final": final,
            "privacy_notice": notice, "report": report}


def build_export(
    report: dict[str, Any], notice: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Wrap an already hash-verified report. The content hash excludes ``exported_at``."""
    final = bool(report.get("report_ready"))
    content = _content(report, notice, final)
    return {
        **content,
        "exported_at": (now or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "integrity": {"algorithm": "sha256", "canonicalization": CANONICALIZATION,
                      "content_sha256": sha256_text(canonical(content))},
    }


def verify_export(bundle: Any) -> list[str]:
    """Return a list of problems (empty means the bundle is intact). Works fully offline."""
    problems: list[str] = []
    if not isinstance(bundle, dict) or bundle.get("format") != EXPORT_FORMAT:
        return ["not a Mia experiment export"]
    if bundle.get("version") != EXPORT_VERSION:
        return [f"unsupported export version {bundle.get('version')!r}"]
    report, notice = bundle.get("report"), bundle.get("privacy_notice")
    integrity = bundle.get("integrity")
    if not isinstance(report, dict) or not isinstance(notice, dict) or not isinstance(integrity, dict):
        return ["the export is missing required sections"]
    if integrity.get("content_sha256") != sha256_text(canonical(_content(report, notice, bool(bundle.get("final"))))):
        problems.append("content hash does not match: the export was changed after it was created")
    baseline = report.get("baseline") or {}
    if sha256_text(canonical(baseline.get("manifest"))) != baseline.get("sha256"):
        problems.append("the Day 0 baseline does not match its recorded hash")
    for snapshot in report.get("snapshots") or []:
        if not isinstance(snapshot, dict):
            problems.append("a snapshot entry is malformed")
            continue
        day = snapshot.get("day_index")
        evidence = snapshot.get("evidence")
        if sha256_text(canonical(evidence)) != snapshot.get("manifest_sha256"):
            problems.append(f"Day {day} snapshot does not match its recorded hash")
        if day == 0:
            if snapshot.get("manifest_sha256") != baseline.get("sha256"):
                problems.append("the Day 0 snapshot is not the recorded baseline")
        elif isinstance(evidence, dict) and evidence.get("baseline_sha256") != baseline.get("sha256"):
            problems.append(f"Day {day} snapshot belongs to a different baseline")
    return problems


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2 or arguments[0] != "verify":
        print("usage: python -m mia.observatory.experiment_export verify FILE", file=sys.stderr)
        return 2
    try:
        bundle = json.loads(Path(arguments[1]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("Error: the file could not be read as an experiment export.", file=sys.stderr)
        return 1
    problems = verify_export(bundle)
    if problems:
        for problem in problems:
            print("PROBLEM: " + problem)
        return 1
    print("Export verified: content, Day 0 baseline and every daily snapshot match their hashes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
