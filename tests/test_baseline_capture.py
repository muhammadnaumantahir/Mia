import json
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from mia.observatory.baseline import BaselineCaptureError, capture_baseline_manifest
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _write_baseline_files(root: Path):
    for relative in (
        "pyproject.toml",
        "src/mia/sps/layers.py",
        "tests/benchmarks/test_foundation.py",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("baseline fixture\n", encoding="utf-8")


class FakeSubprocess:
    def __init__(self, *, return_code=0, failures=0, skipped=0, produce_report=True):
        self.calls = []
        self.return_code = return_code
        self.failures = failures
        self.skipped = skipped
        self.produce_report = produce_report

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[0] == "git":
            return subprocess.CompletedProcess(command, 0, stdout=COMMIT, stderr="")
        report_path = Path(command[command.index("--junitxml") + 1])
        if self.produce_report:
            report_path.write_text(
                (
                    '<testsuites><testsuite name="mia-foundation-v1" tests="6" '
                    f'failures="{self.failures}" errors="0" skipped="{self.skipped}" time="1.25">'
                    '</testsuite></testsuites>'
                ),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(
            command,
            self.return_code,
            stdout="6 tests collected",
            stderr="",
        )


def test_local_baseline_capture_runs_git_and_fixed_suite_on_host(tmp_path):
    _write_baseline_files(tmp_path)
    runner = FakeSubprocess()
    manifest = capture_baseline_manifest(tmp_path, subprocess_run=runner)

    assert manifest["verification_passed"] is True
    assert manifest["source_commit"] == COMMIT
    assert manifest["benchmark_suite_id"] == "mia-foundation-v1"
    assert manifest["configuration_hash"].startswith("sha256:")
    assert manifest["baseline_results"]["tests"] == 6
    assert manifest["baseline_results"]["passed"] == 6
    assert manifest["baseline_results"]["capture_method"] == "local-host-pytest-v1"
    assert manifest["environment"]["os"]
    assert runner.calls[0][0] == ["git", "rev-parse", "HEAD"]
    assert runner.calls[1][0][0] == sys.executable
    assert "tests/benchmarks/test_foundation.py" in runner.calls[1][0]


@pytest.mark.parametrize(
    ("return_code", "failures", "skipped"),
    [(1, 1, 0), (0, 0, 1)],
)
def test_local_baseline_capture_fails_closed_on_failure_or_skip(
    tmp_path,
    return_code,
    failures,
    skipped,
):
    _write_baseline_files(tmp_path)
    manifest = capture_baseline_manifest(
        tmp_path,
        subprocess_run=FakeSubprocess(
            return_code=return_code,
            failures=failures,
            skipped=skipped,
        ),
    )
    assert manifest["verification_passed"] is False


def test_local_baseline_capture_requires_junit_evidence(tmp_path):
    _write_baseline_files(tmp_path)
    with pytest.raises(BaselineCaptureError, match="JUnit"):
        capture_baseline_manifest(
            tmp_path,
            subprocess_run=FakeSubprocess(produce_report=False),
        )


def _baseline_manifest(passed=True):
    return {
        "source_commit": COMMIT,
        "configuration_hash": "sha256:" + "a" * 64,
        "benchmark_suite_id": "mia-foundation-v1",
        "environment": {
            "os": "Windows",
            "python": "3.13",
            "platform": "Windows test host",
            "machine": "AMD64",
        },
        "baseline_results": {
            "tests": 6,
            "passed": 6 if passed else 5,
            "failures": 0 if passed else 1,
            "errors": 0,
            "skipped": 0,
            "pytest_exit_code": 0 if passed else 1,
            "capture_method": "local-host-pytest-v1",
            "capture_host": "test-host",
        },
        "verification_passed": passed,
    }


def test_experiment_prepare_api_captures_baseline_instead_of_trusting_client(
    tmp_path,
):
    calls = []

    def local_capture(repo_root):
        calls.append(Path(repo_root))
        return _baseline_manifest()

    store = ObservatoryStore(tmp_path / "observatory.sqlite3")
    client = TestClient(
        create_app(
            store=store,
            baseline_capture=local_capture,
        )
    )
    response = client.post(
        "/api/experiments/prepare",
        json={
            "name": "Local Day 0",
            "planned_days": 30,
            "baseline_manifest": {
                "verification_passed": True,
                "source_commit": "fake",
            },
        },
    )

    assert response.status_code == 201, response.text
    assert len(calls) == 1
    assert calls[0].is_absolute()
    assert response.json()["status"] == "prepared"
    assert response.json()["baseline_results"]["passed"] == 6
    assert len(store.list_snapshots(response.json()["experiment_id"])) == 1


def test_experiment_prepare_api_does_not_prepare_when_local_baseline_fails(tmp_path):
    store = ObservatoryStore(tmp_path / "observatory.sqlite3")
    client = TestClient(
        create_app(
            store=store,
            baseline_capture=lambda repo_root: _baseline_manifest(passed=False),
        )
    )
    response = client.post(
        "/api/experiments/prepare",
        json={"name": "Must not start", "planned_days": 30},
    )

    assert response.status_code == 422
    assert store.list_experiments() == []
