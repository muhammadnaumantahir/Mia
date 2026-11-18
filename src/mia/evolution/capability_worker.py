"""One-shot worker for already-registered, pure-data capability functions.

This module is invoked only as a Docker container entry point. It never runs
inside Mia's trusted API process.
"""
from __future__ import annotations

import ast
import contextlib
import json
import os
import runpy
import sys
from pathlib import Path
from typing import Any

from .sandbox_worker import _security_findings

MAX_INPUT_BYTES = 32 * 1024
MAX_OUTPUT_BYTES = 64 * 1024


class _DiscardOutput:
    def write(self, value: str) -> int:
        return len(value)

    def flush(self) -> None:
        return None


def _validate_source_contract(source: str) -> None:
    findings = _security_findings(source)
    if findings:
        raise ValueError("candidate source failed the restricted pure-Python policy")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError("candidate source is not valid Python") from exc
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run"
    ]
    if len(functions) != 1:
        raise ValueError("candidate must define exactly one synchronous run function")
    args = functions[0].args
    if (
        len(args.args) != 1
        or args.args[0].arg != "input_data"
        or args.posonlyargs
        or args.vararg is not None
        or args.kwarg is not None
        or args.kwonlyargs
        or args.defaults
        or any(value is not None for value in args.kw_defaults)
    ):
        raise ValueError("run must use exactly the signature run(input_data)")


def run_candidate(source_path: str | Path, input_data: dict[str, Any]) -> dict[str, Any]:
    """Load one candidate and call run(input_data) inside the isolated worker."""
    if not isinstance(input_data, dict):
        raise ValueError("capability input must be an object")
    try:
        encoded_input = json.dumps(
            input_data,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("capability input must contain JSON values only") from exc
    if len(encoded_input) > MAX_INPUT_BYTES:
        raise ValueError("capability input exceeds 32 KB")

    candidate_path = Path(source_path).resolve()
    if candidate_path.name != "candidate.py":
        raise ValueError("capability source path must name candidate.py")
    source = candidate_path.read_text(encoding="utf-8")
    if not source or len(source.encode("utf-8")) > 512 * 1024:
        raise ValueError("candidate source must be 1-512 KB")
    _validate_source_contract(source)

    sink = _DiscardOutput()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        namespace = runpy.run_path(str(candidate_path), run_name="mia_candidate")
        run_function = namespace.get("run")
        if not callable(run_function):
            raise ValueError("candidate did not export run(input_data)")
        result = run_function(input_data)

    if not isinstance(result, dict):
        raise ValueError("capability result must be a dictionary")
    try:
        serialized = json.dumps(
            result,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        normalized = json.loads(serialized)
    except (TypeError, ValueError) as exc:
        raise ValueError("capability result must contain JSON values only") from exc
    if len(serialized.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ValueError("capability result exceeds 64 KB")
    if not isinstance(normalized, dict):
        raise ValueError("capability result must be a JSON object")
    return normalized


def main() -> int:
    try:
        payload = sys.stdin.read(MAX_INPUT_BYTES + 1)
        if len(payload.encode("utf-8")) > MAX_INPUT_BYTES:
            raise ValueError("input exceeds 32 KB")
        input_data = json.loads(payload)
        source_path = os.environ.get("MIA_CAPABILITY_SOURCE", "")
        expected_path = Path("/candidate/candidate.py").resolve()
        if not source_path or Path(source_path).resolve() != expected_path:
            raise ValueError("capability source path is not the mounted candidate")
        result = run_candidate(source_path, input_data)
        sys.stdout.write(json.dumps(
            {"ok": True, "result": result},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ))
        return 0
    except BaseException as exc:
        sys.stderr.write(f"Capability execution failed: {type(exc).__name__}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
