"""Host-side unit tests for the capability worker contract.

These tests use harmless fixture source only; production execution still goes
through DockerSandboxRunner.execute_capability.
"""
import pytest

from mia.evolution.capability_worker import run_candidate


def test_worker_runs_simple_pure_capability_and_returns_json_object(tmp_path):
    candidate = tmp_path / "candidate.py"
    candidate.write_text(
        "def run(input_data):\n"
        "    return {'total': input_data['left'] + input_data['right']}\n",
        encoding="utf-8",
    )
    staged = tmp_path / "stage"
    staged.mkdir()
    candidate.rename(staged / "candidate.py")

    result = run_candidate(staged / "candidate.py", {"left": 2, "right": 3})

    assert result == {"total": 5}


def test_worker_rejects_forbidden_import_and_non_json_output(tmp_path):
    candidate = tmp_path / "candidate.py"
    candidate.write_text(
        "import os\n"
        "def run(input_data):\n"
        "    return {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="restricted pure-Python"):
        run_candidate(candidate, {})

    candidate.write_text(
        "def run(input_data):\n"
        "    return {'bad': object()}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="JSON values"):
        run_candidate(candidate, {})


def test_worker_rejects_oversized_input_and_output(tmp_path):
    candidate = tmp_path / "candidate.py"
    candidate.write_text(
        "def run(input_data):\n"
        "    return {'large': 'x' * (70 * 1024)}\n",
        encoding="utf-8",
    )
    staged = tmp_path / "stage"
    staged.mkdir()
    candidate.rename(staged / "candidate.py")
    with pytest.raises(ValueError, match="64 KB"):
        run_candidate(staged / "candidate.py", {})

    with pytest.raises(ValueError, match="32 KB"):
        run_candidate(staged / "candidate.py", {"large": "x" * (32 * 1024)})


def test_worker_requires_exact_run_input_signature(tmp_path):
    candidate = tmp_path / "candidate.py"
    candidate.write_text(
        "def run(input_data, other=None):\n"
        "    return {}\n",
        encoding="utf-8",
    )
    staged = tmp_path / "stage"
    staged.mkdir()
    candidate.rename(staged / "candidate.py")
    with pytest.raises(ValueError, match="exactly the signature"):
        run_candidate(staged / "candidate.py", {})
