import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mia.observatory.api import create_app
from mia.observatory.baseline import BaselineCaptureError, capture_baseline_manifest
from mia.observatory.store import ObservatoryStore


COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _write_baseline_files(root: Path):
    for relative in (
        "pyproject.toml",
        ".env.example",
        "config/applications.example.json",
        "src/mia/sps/layers.py",
        "tests/benchmarks/test_foundation.py",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("baseline fixture\n", encoding="utf-8")


class FakeSubprocess:
    def __init__(
        self,
        *,
        return_code=0,
        failures=0,
        skipped=0,
        produce_report=True,
        dirty=False,
        ruff_return_code=0,
    ):
        self.calls = []
        self.dirty = dirty
        self.return_code = return_code
        self.failures = failures
        self.skipped = skipped
        self.produce_report = produce_report
        self.ruff_return_code = ruff_return_code

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout=COMMIT, stderr="")
        if command[:2] == ["git", "status"]:
            output = " M src/mia/api.py" if self.dirty else ""
            return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
        if command[1:3] == ["-m", "ruff"]:
            return subprocess.CompletedProcess(
                command,
                self.ruff_return_code,
                stdout="[]" if self.ruff_return_code == 0 else '[{"code":"E999"}]',
                stderr="",
            )
        report_path = Path(command[command.index("--junitxml") + 1])
        if self.produce_report:
            report_path.write_text(
                (
                    '<testsuites><testsuite name="mia-foundation-v2" tests="6" '
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


def test_local_baseline_capture_runs_git_and_fixed_suite_on_host(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    _write_baseline_files(tmp_path)
    runner = FakeSubprocess()
    manifest = capture_baseline_manifest(tmp_path, subprocess_run=runner)

    assert manifest["verification_passed"] is True
    assert manifest["source_commit"] == COMMIT
    assert manifest["benchmark_suite_id"] == "mia-foundation-v2"
    assert manifest["configuration_hash"].startswith("sha256:")
    assert manifest["baseline_results"]["tests"] == 6
    assert manifest["baseline_results"]["passed"] == 6
    assert manifest["baseline_results"]["capture_method"] == "local-host-ruff-pytest-v2"
    assert manifest["baseline_results"]["ruff_passed"] is True
    assert manifest["environment"]["os"]
    assert runner.calls[0][0] == ["git", "rev-parse", "HEAD"]
    assert runner.calls[1][0] == ["git", "status", "--porcelain", "--untracked-files=normal"]
    assert runner.calls[2][0][0:3] == [sys.executable, "-m", "ruff"]
    assert runner.calls[3][0][0:3] == [sys.executable, "-m", "pytest"]
    assert "tests" in runner.calls[3][0]
    assert runner.calls[4][0] == ["git", "status", "--porcelain", "--untracked-files=normal"]
    assert str(tmp_path / "src") in runner.calls[2][1]["env"]["PYTHONPATH"]
    assert str(tmp_path / "src") in runner.calls[3][1]["env"]["PYTHONPATH"]


@pytest.mark.parametrize(
    ("return_code", "ruff_return_code", "failures", "skipped"),
    [(1, 0, 1, 0), (0, 0, 0, 1), (0, 1, 0, 0)],
)
def test_local_baseline_capture_fails_closed_on_failure_or_skip(
    tmp_path,
    return_code,
    ruff_return_code,
    failures,
    skipped,
    monkeypatch,
):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    _write_baseline_files(tmp_path)
    manifest = capture_baseline_manifest(
        tmp_path,
        subprocess_run=FakeSubprocess(
            return_code=return_code,
            ruff_return_code=ruff_return_code,
            failures=failures,
            skipped=skipped,
        ),
    )
    assert manifest["verification_passed"] is False


def test_local_baseline_capture_requires_junit_evidence(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
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
        "benchmark_suite_id": "mia-foundation-v2",
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
            "ruff_passed": passed,
            "ruff_exit_code": 0 if passed else 1,
            "pytest_exit_code": 0 if passed else 1,
            "capture_method": "local-host-ruff-pytest-v2",
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
    assert response.json()["baseline_results"]["ruff_passed"] is True
    assert len(store.list_snapshots(response.json()["experiment_id"])) == 1
    events = store.list_events(limit=10)
    assert any(event["event_type"] == "day0_baseline_prepared" for event in events)
    baseline = client.get(
        f"/api/experiments/{response.json()['experiment_id']}/baseline"
    )
    assert baseline.status_code == 200
    assert baseline.json()["baseline_sha256"] == response.json()["baseline_sha256"]


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


def test_local_baseline_capture_rejects_dirty_tracked_worktree(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    _write_baseline_files(tmp_path)
    with pytest.raises(BaselineCaptureError, match="tracked changes"):
        capture_baseline_manifest(
            tmp_path,
            subprocess_run=FakeSubprocess(dirty=True),
        )


def test_local_baseline_capture_rejects_github_actions_environment(tmp_path, monkeypatch):
    _write_baseline_files(tmp_path)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    runner = FakeSubprocess()

    with pytest.raises(BaselineCaptureError, match="not a GitHub Actions"):
        capture_baseline_manifest(tmp_path, subprocess_run=runner)

    assert runner.calls == []


def test_local_baseline_capture_strips_secret_environment_values(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("MIA_TEST_TOKEN", "do-not-pass-to-tools")
    _write_baseline_files(tmp_path)
    runner = FakeSubprocess()
    capture_baseline_manifest(tmp_path, subprocess_run=runner)
    assert all("MIA_TEST_TOKEN" not in call[1]["env"] for call in runner.calls[2:])



def test_experiment_start_is_explicit_and_records_event(tmp_path):
    store = ObservatoryStore(tmp_path / "observatory.sqlite3")
    client = TestClient(
        create_app(
            store=store,
            baseline_capture=lambda repo_root: _baseline_manifest(),
        )
    )
    prepared = client.post(
        "/api/experiments/prepare",
        json={"name": "Explicit start", "planned_days": 30},
    )
    assert prepared.status_code == 201
    experiment_id = prepared.json()["experiment_id"]
    assert store.list_experiments()[0]["status"] == "prepared"

    started = client.post(f"/api/experiments/{experiment_id}/start")
    assert started.status_code == 200
    assert store.list_experiments()[0]["status"] == "running"
    assert any(
        event["event_type"] == "experiment_started"
        for event in store.list_events(limit=10)
    )
