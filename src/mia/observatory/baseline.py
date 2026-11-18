"""Capture a real, local Day 0 baseline by running Mia's fixed benchmark suite."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable


BENCHMARK_SUITE_ID = "mia-foundation-v1"
BENCHMARK_PATH = Path("tests/benchmarks/test_foundation.py")
CONFIG_PATHS = (
    Path("pyproject.toml"),
    Path("src/mia/sps/layers.py"),
    BENCHMARK_PATH,
)


class BaselineCaptureError(RuntimeError):
    """The local Day 0 benchmark capture could not be verified."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run_default(command: list[str], **kwargs: Any) -> Any:
    return subprocess.run(command, **kwargs)


def _parse_junit(path: Path) -> dict[str, int | float]:
    if not path.is_file():
        raise BaselineCaptureError("Pytest did not produce a JUnit benchmark report")
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise BaselineCaptureError("Pytest produced an invalid JUnit report") from exc

    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        raise BaselineCaptureError("JUnit report contains no test suites")

    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    duration = 0.0
    for suite in suites:
        for key in totals:
            try:
                totals[key] += int(suite.attrib.get(key, "0"))
            except ValueError as exc:
                raise BaselineCaptureError("JUnit report contains invalid counts") from exc
        try:
            duration += float(suite.attrib.get("time", "0"))
        except ValueError:
            pass
    totals["passed"] = (
        totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]
    )
    totals["duration_seconds"] = round(duration, 3)
    return totals


def _environment() -> dict[str, Any]:
    memory_total = None
    try:
        import psutil

        memory_total = int(psutil.virtual_memory().total)
    except (ImportError, AttributeError, OSError):
        pass

    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "memory_total_bytes": memory_total,
    }


def capture_baseline_manifest(
    repo_root: str | Path,
    *,
    subprocess_run: Callable[..., Any] | None = None,
    timeout_seconds: int = 360,
) -> dict[str, Any]:
    """Run the fixed benchmark suite on the Mia host and return a verified manifest.

    This operation never runs on GitHub Actions. The caller must invoke it from
    the locally running Mia service on the machine whose Day 0 is being captured.
    """
    root = Path(repo_root).resolve()
    if not 30 <= timeout_seconds <= 900:
        raise ValueError("timeout_seconds must be between 30 and 900")
    runner = subprocess_run or _run_default
    if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
        raise BaselineCaptureError(
            "Day 0 must be captured on the Mia host, not a GitHub Actions runner"
        )

    for relative in CONFIG_PATHS:
        if not (root / relative).is_file():
            raise BaselineCaptureError(f"Required baseline file is missing: {relative}")

    try:
        git = runner(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        raise BaselineCaptureError("Git is required to capture the local source commit") from exc
    source_commit = (git.stdout or "").strip().lower()
    if git.returncode != 0 or len(source_commit) not in {40, 64} or any(
        char not in "0123456789abcdef" for char in source_commit
    ):
        raise BaselineCaptureError("Could not identify the exact local source commit")

    try:
        worktree = runner(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        raise BaselineCaptureError("Could not verify the local Git worktree") from exc
    if worktree.returncode != 0:
        raise BaselineCaptureError("Could not verify the local Git worktree")
    if (worktree.stdout or "").strip():
        raise BaselineCaptureError(
            "Commit or discard tracked changes before capturing Day 0"
        )

    config_parts = []
    for relative in CONFIG_PATHS:
        payload = (root / relative).read_bytes()
        config_parts.append({
            "path": relative.as_posix(),
            "sha256": _sha256(payload),
        })
    configuration_hash = "sha256:" + _sha256(
        json.dumps(config_parts, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="mia-day0-") as temporary:
        report_path = Path(temporary) / "junit.xml"
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            str(BENCHMARK_PATH.as_posix()),
            "--junitxml",
            str(report_path),
        ]
        try:
            environment = os.environ.copy()
            source_path = str(root / "src")
            existing_pythonpath = environment.get("PYTHONPATH", "")
            environment["PYTHONPATH"] = os.pathsep.join(
                value for value in (source_path, existing_pythonpath) if value
            )
            result = runner(
                command,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise BaselineCaptureError(
                f"Local baseline suite exceeded {timeout_seconds} seconds"
            ) from exc
        except (FileNotFoundError, OSError) as exc:
            raise BaselineCaptureError("Could not start local pytest baseline suite") from exc

        results = _parse_junit(report_path)

    elapsed = round(time.monotonic() - started, 3)
    passed = (
        result.returncode == 0
        and results["tests"] > 0
        and results["failures"] == 0
        and results["errors"] == 0
        and results["skipped"] == 0
        and results["passed"] == results["tests"]
    )
    return {
        "source_commit": source_commit,
        "configuration_hash": configuration_hash,
        "benchmark_suite_id": BENCHMARK_SUITE_ID,
        "environment": _environment(),
        "baseline_results": {
            **results,
            "pytest_exit_code": result.returncode,
            "elapsed_seconds": elapsed,
            "stdout_sha256": _sha256((result.stdout or "").encode("utf-8")),
            "stderr_sha256": _sha256((result.stderr or "").encode("utf-8")),
            "stdout_tail": (result.stdout or "")[-4000:],
            "stderr_tail": (result.stderr or "")[-4000:],
            "capture_host": platform.node(),
            "capture_method": "local-host-pytest-v1",
        },
        "verification_passed": passed,
    }
