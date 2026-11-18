"""Capture a real, local Day 0 baseline by running Mia's fixed verification suite."""

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
from collections.abc import Callable
from pathlib import Path
from typing import Any


BENCHMARK_SUITE_ID = "mia-foundation-v2"
CONFIG_PATHS = (
    Path("pyproject.toml"),
    Path(".env.example"),
    Path("config/applications.example.json"),
    Path("src/mia/sps/layers.py"),
    Path("tests/benchmarks/test_foundation.py"),
)


class BaselineCaptureError(RuntimeError):
    """The local Day 0 baseline capture could not be verified."""


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


def _safe_child_environment(repo_root: Path) -> dict[str, str]:
    """Keep OS runtime variables but remove common credential-bearing variables."""
    secret_markers = (
        "API_KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD",
        "CREDENTIAL", "AUTH", "COOKIE", "PRIVATE_KEY", "PROXY",
    )
    always_remove = {
        "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP",
        "PYTEST_ADDOPTS", "PYTEST_PLUGINS",
    }
    child = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in always_remove
        and not any(marker in key.upper() for marker in secret_markers)
    }
    child.update({
        "PYTHONPATH": str(repo_root / "src"),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
    })
    return child


def _git_output(
    runner: Callable[..., Any],
    command: list[str],
    *,
    root: Path,
    env: dict[str, str],
) -> str:
    try:
        result = runner(
            command,
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        raise BaselineCaptureError("Git is required to capture the local source baseline") from exc
    if result.returncode != 0:
        raise BaselineCaptureError("Could not inspect the local Git checkout")
    return (result.stdout or "").strip()


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
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """Run Ruff and all repository tests on the Mia host and return a manifest.

    The working tree must be clean so the source commit identifies exactly the
    code that was tested. Child processes receive a sanitized environment.
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

    child_env = _safe_child_environment(root)
    source_commit = _git_output(
        runner,
        ["git", "rev-parse", "HEAD"],
        root=root,
        env=child_env,
    ).lower()
    if len(source_commit) not in {40, 64} or any(
        char not in "0123456789abcdef" for char in source_commit
    ):
        raise BaselineCaptureError("Could not identify the exact local source commit")

    worktree = _git_output(
        runner,
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        root=root,
        env=child_env,
    )
    if worktree:
        raise BaselineCaptureError(
            "Commit or discard tracked changes and untracked source files before capturing Day 0"
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
    try:
        ruff_result = runner(
            [
                sys.executable, "-m", "ruff", "check",
                "--output-format=json", "src", "tests",
            ],
            cwd=str(root),
            env=child_env,
            capture_output=True,
            text=True,
            timeout=min(timeout_seconds, 120),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BaselineCaptureError("Ruff exceeded its Day 0 lint time limit") from exc
    except (FileNotFoundError, OSError) as exc:
        raise BaselineCaptureError("Could not start Ruff; install Mia's dev dependencies") from exc

    with tempfile.TemporaryDirectory(prefix="mia-day0-") as temporary:
        report_path = Path(temporary) / "junit.xml"
        command = [
            sys.executable, "-m", "pytest", "-q",
            "-p", "no:cacheprovider",
            "-p", "pytest_asyncio.plugin",
            "tests",
            "--junitxml", str(report_path),
        ]
        try:
            pytest_result = runner(
                command,
                cwd=str(root),
                env=child_env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise BaselineCaptureError(
                f"Local Day 0 test suite exceeded {timeout_seconds} seconds"
            ) from exc
        except (FileNotFoundError, OSError) as exc:
            raise BaselineCaptureError("Could not start the local Day 0 test suite") from exc
        results = _parse_junit(report_path)

    elapsed = round(time.monotonic() - started, 3)
    ruff_passed = ruff_result.returncode == 0
    pytest_passed = (
        pytest_result.returncode == 0
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
            "ruff_passed": ruff_passed,
            "ruff_exit_code": ruff_result.returncode,
            "ruff_stdout_sha256": _sha256((ruff_result.stdout or "").encode("utf-8")),
            "ruff_stderr_sha256": _sha256((ruff_result.stderr or "").encode("utf-8")),
            "pytest_exit_code": pytest_result.returncode,
            "pytest_stdout_sha256": _sha256((pytest_result.stdout or "").encode("utf-8")),
            "pytest_stderr_sha256": _sha256((pytest_result.stderr or "").encode("utf-8")),
            "elapsed_seconds": elapsed,
            "capture_host": platform.node() or "unknown-local-host",
            "capture_method": "local-host-ruff-pytest-v2",
        },
        "verification_passed": ruff_passed and pytest_passed,
    }
