"""Untrusted capability validation worker; invoked only inside the Docker sandbox."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


_FORBIDDEN_IMPORTS = {
    "builtins",
    "ctypes",
    "ftplib",
    "http",
    "httpx",
    "importlib",
    "multiprocessing",
    "os",
    "pathlib",
    "pickle",
    "pty",
    "requests",
    "runpy",
    "shutil",
    "shelve",
    "socket",
    "ssl",
    "subprocess",
    "sys",
    "telnetlib",
    "tempfile",
    "urllib",
    "webbrowser",
}
_FORBIDDEN_CALLS = {"__import__", "breakpoint", "compile", "eval", "exec"}
_MAX_DETAIL = 8000


def _security_findings(source: str) -> list[str]:
    findings: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"source is not valid Python: {exc.msg}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in _FORBIDDEN_IMPORTS:
                    findings.append(f"forbidden import: {root}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if root in _FORBIDDEN_IMPORTS:
                findings.append(f"forbidden import: {root}")
        elif isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Name) and function.id in _FORBIDDEN_CALLS:
                findings.append(f"forbidden dynamic call: {function.id}")
            elif isinstance(function, ast.Attribute):
                if function.attr in {
                    "system",
                    "popen",
                    "remove",
                    "unlink",
                    "rmdir",
                    "rename",
                    "replace",
                    "write_text",
                    "write_bytes",
                    "mkdir",
                    "chmod",
                    "chown",
                    "kill",
                }:
                    findings.append(f"forbidden operation: {function.attr}")
    return sorted(set(findings))


def _run(command: list[str], *, timeout: int, env: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
        return {
            "passed": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": (result.stdout or "")[-_MAX_DETAIL:],
            "stderr": (result.stderr or "")[-_MAX_DETAIL:],
        }
    except subprocess.TimeoutExpired:
        return {
            "passed": False,
            "exit_code": None,
            "stdout": "",
            "stderr": f"Command timed out after {timeout} seconds",
        }
    except OSError as exc:
        return {
            "passed": False,
            "exit_code": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
        }



def _all_security_findings(source: str, test_source: str | None) -> dict[str, list[str]]:
    return {
        "candidate": _security_findings(source),
        "tests": _security_findings(test_source) if test_source is not None else [],
    }


def main() -> int:
    source_path = Path(os.environ["MIA_SANDBOX_SOURCE"])
    test_value = os.environ.get("MIA_SANDBOX_TEST", "")
    test_path = Path(test_value) if test_value else None
    source = source_path.read_text(encoding="utf-8")
    test_source = test_path.read_text(encoding="utf-8") if test_path else None

    parse_ok = True
    parse_errors: list[str] = []
    for label, candidate in (("candidate", source), ("tests", test_source)):
        if candidate is None:
            continue
        try:
            ast.parse(candidate)
        except SyntaxError as exc:
            parse_ok = False
            parse_errors.append(f"{label}: {exc.msg} at line {exc.lineno}")

    paths = [str(source_path)]
    if test_path is not None:
        paths.append(str(test_path))
    static_result = _run(
        ["ruff", "check", "--output-format=json", *paths],
        timeout=30,
    )
    static_passed = parse_ok and static_result["passed"]

    security_findings = _all_security_findings(source, test_source)
    security_passed = not any(security_findings.values())

    if test_path is not None:
        unit_result = _run(
            [
                "python",
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "-p",
                "pytest_asyncio.plugin",
                str(test_path),
            ],
            timeout=120,
            env={**os.environ, "PYTHONPATH": "/candidate"},
        )
        unit_passed = unit_result["passed"]
    else:
        unit_result = {
            "passed": False,
            "exit_code": None,
            "stdout": "",
            "stderr": "No candidate test source was supplied",
        }
        unit_passed = False

    regression_result = _run(
        [
            "python",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "pytest_asyncio.plugin",
            "/opt/mia/tests",
        ],
        timeout=240,
        env={**os.environ, "PYTHONPATH": "/opt/mia/src"},
    )

    report = {
        "static_checks": static_passed,
        "unit_tests": unit_passed,
        "security_checks": security_passed,
        "regression_tests": regression_result["passed"],
        "evidence": {
            "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "test_sha256": (
                hashlib.sha256(test_source.encode("utf-8")).hexdigest()
                if test_source is not None
                else None
            ),
            "parse_errors": parse_errors,
            "security_findings": security_findings,
            "static": static_result,
            "unit": unit_result,
            "regression": regression_result,
            "security_policy": "static AST denylist; defense in depth, not a proof",
        },
    }
    sys.stdout.write(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
