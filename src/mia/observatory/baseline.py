"""Capture a real, local Day 0 baseline by running Mia's fixed verification suite."""

from __future__ import annotations

import hashlib
import json
import math
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


RUNTIME_BOOLEAN_DEFAULTS = {
    "MIA_EXPERIMENT_AUTO_CAPTURE": "false",
    "MIA_AUTONOMY_ENABLED": "false",
    "MIA_EMERGENCY_STOP": "false",
    "MIA_ALLOW_PAID": "false",
    "MIA_ALLOW_REMOTE_FILE_CONTEXT": "false",
    "MIA_OLLAMA_ENABLE_TOOL_CALLING": "false",
    "MIA_FREELLMAPI_ENABLED": "false",
    "MIA_FREELLMAPI_ROUTE_FREE": "false",
    "MIA_FREELLMAPI_ENABLE_TOOL_CALLING": "false",
    "MIA_FREELLMAPI_DATA_LOCAL": "false",
}
RUNTIME_NUMBER_DEFAULTS = {
    "MIA_MAX_CPU_PERCENT": "75",
    "MIA_MAX_RAM_PERCENT": "85",
    "MIA_AUTONOMY_INTERVAL_SECONDS": "30",
    "MIA_MAX_MONTHLY_SPEND_USD": "0",
    "MIA_MAX_REQUEST_SPEND_USD": "0",
    "MIA_PORT": "8765",
}
RUNTIME_HASH_DEFAULTS = {
    "MIA_HOST": "127.0.0.1",
    "MIA_OLLAMA_BASE_URL": "http://127.0.0.1:11434",
    "MIA_FREELLMAPI_BASE_URL": "http://127.0.0.1:3001/v1",
    "MIA_COMPUTER_ROOTS": "",
    "MIA_APPLICATIONS_FILE": "config/applications.json",
    "MIA_DOCKER_EXECUTABLE": "docker",
    "MIA_SANDBOX_IMAGE": "mia-sandbox:py313-v1",
}


def _runtime_configuration(repo_root: Path) -> dict[str, Any]:
    """Capture non-secret policy/configuration without persisting private paths or URLs."""
    settings: dict[str, Any] = {}
    for name, default in RUNTIME_BOOLEAN_DEFAULTS.items():
        raw = os.getenv(name, default).strip().lower()
        if raw not in {"true", "false"}:
            raise BaselineCaptureError(f"{name} must be true or false")
        settings[name] = raw == "true"

    for name, default in RUNTIME_NUMBER_DEFAULTS.items():
        raw = os.getenv(name, default).strip()
        try:
            value = float(raw)
        except ValueError as exc:
            raise BaselineCaptureError(f"{name} must be a valid number") from exc
        if not math.isfinite(value):
            raise BaselineCaptureError(f"{name} must be finite")
        if name in {"MIA_MAX_CPU_PERCENT", "MIA_MAX_RAM_PERCENT"} and not 1 <= value <= 100:
            raise BaselineCaptureError(f"{name} must be between 1 and 100")
        if name == "MIA_AUTONOMY_INTERVAL_SECONDS" and not 1 <= value <= 3600:
            raise BaselineCaptureError(f"{name} must be between 1 and 3600")
        if name in {"MIA_MAX_MONTHLY_SPEND_USD", "MIA_MAX_REQUEST_SPEND_USD"} and value < 0:
            raise BaselineCaptureError(f"{name} cannot be negative")
        if name == "MIA_PORT" and not 1 <= value <= 65535:
            raise BaselineCaptureError("MIA_PORT must be between 1 and 65535")
        settings[name] = int(value) if value.is_integer() else value

    for name, default in RUNTIME_HASH_DEFAULTS.items():
        raw = os.getenv(name, default).strip()
        settings[f"{name}_SHA256"] = _sha256(raw.encode("utf-8"))

    applications_path_raw = os.getenv(
        "MIA_APPLICATIONS_FILE", "config/applications.json"
    )
    applications_path = Path(applications_path_raw).expanduser()
    if not applications_path.is_absolute():
        applications_path = repo_root / applications_path
    applications_hash = None
    if applications_path.is_file():
        applications_hash = _sha256(applications_path.read_bytes())
    settings["MIA_APPLICATIONS_CONFIG_PRESENT"] = applications_path.is_file()
    settings["MIA_APPLICATIONS_CONFIG_SHA256"] = applications_hash
    return settings


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
    runtime_configuration = _runtime_configuration(root)
    configuration_hash = "sha256:" + _sha256(
        json.dumps(
            {
                "files": config_parts,
                "runtime_settings": runtime_configuration,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
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
        temporary_path = Path(temporary)
        benchmark_report = temporary_path / "fixed-benchmark.xml"
        regression_report = temporary_path / "full-regression.xml"
        fixed_command = [
            sys.executable, "-m", "pytest", "-q",
            "-p", "no:cacheprovider", "-p", "pytest_asyncio.plugin",
            "tests/benchmarks/test_foundation.py",
            "--junitxml", str(benchmark_report),
        ]
        regression_command = [
            sys.executable, "-m", "pytest", "-q",
            "-p", "no:cacheprovider", "-p", "pytest_asyncio.plugin",
            "tests", "--ignore=tests/benchmarks",
            "--junitxml", str(regression_report),
        ]
        try:
            fixed_result = runner(
                fixed_command, cwd=str(root), env=child_env,
                capture_output=True, text=True,
                timeout=min(timeout_seconds, 120), check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise BaselineCaptureError("Fixed benchmark suite exceeded its time limit") from exc
        except (FileNotFoundError, OSError) as exc:
            raise BaselineCaptureError("Could not start the fixed benchmark suite") from exc
        try:
            regression_result = runner(
                regression_command, cwd=str(root), env=child_env,
                capture_output=True, text=True, timeout=timeout_seconds, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise BaselineCaptureError(
                f"Full regression suite exceeded {timeout_seconds} seconds"
            ) from exc
        except (FileNotFoundError, OSError) as exc:
            raise BaselineCaptureError("Could not start the full regression suite") from exc
        fixed_benchmark_results = _parse_junit(benchmark_report)
        regression_results = _parse_junit(regression_report)

    post_test_worktree = _git_output(
        runner,
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        root=root,
        env=child_env,
    )
    if post_test_worktree:
        raise BaselineCaptureError(
            "The local test suite changed tracked or untracked source files; Day 0 was not captured"
        )

    elapsed = round(time.monotonic() - started, 3)
    ruff_passed = ruff_result.returncode == 0
    fixed_benchmark_passed = (
        fixed_result.returncode == 0
        and fixed_benchmark_results["tests"] > 0
        and fixed_benchmark_results["failures"] == 0
        and fixed_benchmark_results["errors"] == 0
        and fixed_benchmark_results["skipped"] == 0
        and fixed_benchmark_results["passed"] == fixed_benchmark_results["tests"]
    )
    regression_passed = (
        regression_result.returncode == 0
        and regression_results["tests"] > 0
        and regression_results["failures"] == 0
        and regression_results["errors"] == 0
        and regression_results["skipped"] == 0
        and regression_results["passed"] == regression_results["tests"]
    )
    return {
        "source_commit": source_commit,
        "configuration_hash": configuration_hash,
        "benchmark_suite_id": BENCHMARK_SUITE_ID,
        "environment": _environment(),
        "runtime_settings": runtime_configuration,
        "baseline_results": {
            **regression_results,
            "ruff_passed": ruff_passed,
            "ruff_exit_code": ruff_result.returncode,
            "ruff_stdout_sha256": _sha256((ruff_result.stdout or "").encode("utf-8")),
            "ruff_stderr_sha256": _sha256((ruff_result.stderr or "").encode("utf-8")),
            "pytest_exit_code": regression_result.returncode,
            "pytest_stdout_sha256": _sha256((regression_result.stdout or "").encode("utf-8")),
            "pytest_stderr_sha256": _sha256((regression_result.stderr or "").encode("utf-8")),
            "elapsed_seconds": elapsed,
            "capture_host": platform.node() or "unknown-local-host",
            "capture_method": "local-host-fixed-benchmark-and-regression-v3",
        },
        "fixed_benchmark_results": {
            **fixed_benchmark_results,
            "pytest_exit_code": fixed_result.returncode,
            "stdout_sha256": _sha256((fixed_result.stdout or "").encode("utf-8")),
            "stderr_sha256": _sha256((fixed_result.stderr or "").encode("utf-8")),
            "suite_id": BENCHMARK_SUITE_ID,
        },
        "verification_passed": ruff_passed and fixed_benchmark_passed and regression_passed,
    }
