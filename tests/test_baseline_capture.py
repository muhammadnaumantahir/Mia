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
        fixed_failures=0,
    ):
        self.calls = []
        self.dirty = dirty
        self.return_code = return_code
        self.failures = failures
        self.skipped = skipped
        self.produce_report = produce_report
        self.ruff_return_code = ruff_return_code
        self.fixed_failures = fixed_failures

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
            fixed_benchmark = "tests/benchmarks/test_foundation.py" in command
            test_count = 6 if fixed_benchmark else 12
            failure_count = self.fixed_failures if fixed_benchmark else self.failures
            report_path.write_text(
                (
                    '<testsuites><testsuite name="mia-foundation-v2" '
                    f'tests="{test_count}" failures="{failure_count}" '
                    f'errors="0" skipped="{self.skipped}" time="1.25">'
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
    assert manifest["baseline_results"]["tests"] == 12
    assert manifest["baseline_results"]["passed"] == 12
    assert manifest["baseline_results"]["capture_method"] == "local-host-fixed-benchmark-and-regression-v3"
    assert manifest["baseline_results"]["ruff_passed"] is True
    assert manifest["fixed_benchmark_results"]["tests"] == 6
    assert manifest["fixed_benchmark_results"]["passed"] == 6
    assert manifest["fixed_benchmark_results"]["suite_id"] == "mia-foundation-v2"
    assert manifest["environment"]["os"]
    assert runner.calls[0][0] == ["git", "rev-parse", "HEAD"]
    assert runner.calls[1][0] == ["git", "status", "--porcelain", "--untracked-files=normal"]
    assert runner.calls[2][0][0:3] == [sys.executable, "-m", "ruff"]
    assert runner.calls[3][0][0:3] == [sys.executable, "-m", "pytest"]
    assert "tests/benchmarks/test_foundation.py" in runner.calls[3][0]
    assert runner.calls[4][0][0:3] == [sys.executable, "-m", "pytest"]
    assert "--ignore=tests/benchmarks" in runner.calls[4][0]
    assert "tests" in runner.calls[4][0]
    assert runner.calls[5][0] == ["git", "status", "--porcelain", "--untracked-files=normal"]
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
        "fixed_benchmark_results": {
            "tests": 6,
            "passed": 6,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "duration_seconds": 1.25,
            "pytest_exit_code": 0,
            "suite_id": "mia-foundation-v2",
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
            "capture_method": "local-host-fixed-benchmark-and-regression-v3",
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


class FakeDailyResources:
    def snapshot(self):
        return {
            "cpu_percent": 17.5,
            "memory_percent": 42.0,
            "memory_available_bytes": 500000,
            "memory_total_bytes": 1000000,
            "process_count": 37,
        }


def test_daily_snapshot_is_captured_on_host_and_not_client_supplied(tmp_path):
    from datetime import UTC, datetime, timedelta
    import sqlite3

    store = ObservatoryStore(tmp_path / "daily.sqlite3")
    client = TestClient(
        create_app(
            store=store,
            baseline_capture=lambda root: _baseline_manifest(),
            resource_monitor=FakeDailyResources(),
        )
    )
    prepared = client.post(
        "/api/experiments/prepare",
        json={"name": "Daily capture", "planned_days": 3},
    )
    assert prepared.status_code == 201
    experiment_id = prepared.json()["experiment_id"]
    assert client.post(f"/api/experiments/{experiment_id}/start").status_code == 200

    started_at = (datetime.now(UTC) - timedelta(days=1, hours=2)).isoformat()
    with sqlite3.connect(store.database_path) as db:
        db.execute(
            "UPDATE experiments SET started_at = ? WHERE experiment_id = ?",
            (started_at, experiment_id),
        )
        db.commit()

    due = client.get(f"/api/experiments/{experiment_id}/snapshot-status")
    assert due.status_code == 200
    assert due.json()["due"] is True
    assert due.json()["day_index"] == 1

    captured = client.post(f"/api/experiments/{experiment_id}/snapshots/capture")
    assert captured.status_code == 201, captured.text
    body = captured.json()
    assert body["day_index"] == 1
    assert body["resources"]["cpu_percent"] == 17.5
    assert body["verification"]["ruff_passed"] is True
    snapshots = store.list_snapshots(experiment_id)
    assert [item["day_index"] for item in snapshots] == [0, 1]
    assert any(
        item["metric_name"] == "cpu_percent"
        for item in store.list_metrics()
    )

    client_supplied = client.post(
        f"/api/experiments/{experiment_id}/snapshots",
        json={
            "day_index": 2,
            "source_commit": "f" * 40,
            "snapshot": {"made_up": True},
        },
    )
    assert client_supplied.status_code == 410


def test_daily_snapshot_does_not_fill_missing_days_with_fake_values(tmp_path):
    from datetime import UTC, datetime, timedelta
    import sqlite3

    store = ObservatoryStore(tmp_path / "missed-days.sqlite3")
    client = TestClient(
        create_app(
            store=store,
            baseline_capture=lambda root: _baseline_manifest(),
            resource_monitor=FakeDailyResources(),
        )
    )
    prepared = client.post(
        "/api/experiments/prepare",
        json={"name": "Missed days", "planned_days": 5},
    )
    experiment_id = prepared.json()["experiment_id"]
    client.post(f"/api/experiments/{experiment_id}/start")
    started_at = (datetime.now(UTC) - timedelta(days=3, hours=2)).isoformat()
    with sqlite3.connect(store.database_path) as db:
        db.execute(
            "UPDATE experiments SET started_at = ? WHERE experiment_id = ?",
            (started_at, experiment_id),
        )
        db.commit()

    status = client.get(f"/api/experiments/{experiment_id}/snapshot-status").json()
    assert status["day_index"] == 3
    assert status["days_missed"] == [1, 2]

    captured = client.post(f"/api/experiments/{experiment_id}/snapshots/capture")
    assert captured.status_code == 201
    assert captured.json()["missing_day_indices"] == [1, 2]
    snapshots = store.list_snapshots(experiment_id)
    assert [item["day_index"] for item in snapshots] == [0, 3]
    assert any(
        event["event_type"] == "daily_snapshots_unavailable"
        for event in store.list_events(limit=20)
    )


def test_daily_snapshot_blocks_configuration_drift_from_day_zero(tmp_path):
    manifests = [_baseline_manifest(), _baseline_manifest()]
    manifests[1]["configuration_hash"] = "sha256:" + "c" * 64
    calls = []

    def capture(root):
        calls.append(root)
        return manifests[min(len(calls) - 1, 1)]

    store = ObservatoryStore(tmp_path / "config-drift.sqlite3")
    client = TestClient(
        create_app(
            store=store,
            baseline_capture=capture,
            resource_monitor=FakeDailyResources(),
        )
    )
    prepared = client.post(
        "/api/experiments/prepare",
        json={"name": "Config drift", "planned_days": 2},
    )
    experiment_id = prepared.json()["experiment_id"]
    client.post(f"/api/experiments/{experiment_id}/start")
    from datetime import UTC, datetime, timedelta
    import sqlite3

    started_at = (datetime.now(UTC) - timedelta(days=1, hours=1)).isoformat()
    with sqlite3.connect(store.database_path) as db:
        db.execute(
            "UPDATE experiments SET started_at = ? WHERE experiment_id = ?",
            (started_at, experiment_id),
        )
        db.commit()

    response = client.post(f"/api/experiments/{experiment_id}/snapshots/capture")
    assert response.status_code == 409
    assert "configuration differs" in response.json()["detail"]
    assert [item["day_index"] for item in store.list_snapshots(experiment_id)] == [0]


def test_experiment_snapshot_scheduler_is_opt_in_by_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_EXPERIMENT_AUTO_CAPTURE", "true")
    app = create_app(
        store=ObservatoryStore(tmp_path / "scheduler.sqlite3"),
        baseline_capture=lambda root: _baseline_manifest(),
        resource_monitor=FakeDailyResources(),
    )
    client = TestClient(app)
    status = client.get("/api/experiments/scheduler/status")
    assert status.status_code == 200
    assert status.json()["enabled"] is True
    assert status.json()["interval_seconds"] == 60


def test_local_baseline_capture_requires_fixed_and_full_regression_suites_to_pass(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    _write_baseline_files(tmp_path)
    manifest = capture_baseline_manifest(
        tmp_path,
        subprocess_run=FakeSubprocess(fixed_failures=1),
    )

    assert manifest["verification_passed"] is False
    assert manifest["baseline_results"]["passed"] == 12
    assert manifest["fixed_benchmark_results"]["tests"] == 6
    assert manifest["fixed_benchmark_results"]["passed"] == 5
    assert manifest["fixed_benchmark_results"]["failures"] == 1


def test_runtime_configuration_is_captured_without_raw_private_values(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("MIA_AUTONOMY_ENABLED", "true")
    monkeypatch.setenv("MIA_ALLOW_PAID", "false")
    monkeypatch.setenv("MIA_COMPUTER_ROOTS", "D:/Private/ClientProjects")
    _write_baseline_files(tmp_path)
    runner = FakeSubprocess()

    manifest = capture_baseline_manifest(tmp_path, subprocess_run=runner)

    settings = manifest["runtime_settings"]
    assert settings["MIA_AUTONOMY_ENABLED"] is True
    assert settings["MIA_ALLOW_PAID"] is False
    assert settings["MIA_COMPUTER_ROOTS_SHA256"]
    assert "D:/Private/ClientProjects" not in str(manifest)
    assert manifest["configuration_hash"].startswith("sha256:")


def test_runtime_policy_changes_are_detected_as_configuration_drift(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    _write_baseline_files(tmp_path)
    monkeypatch.setenv("MIA_AUTONOMY_ENABLED", "false")
    before = capture_baseline_manifest(
        tmp_path, subprocess_run=FakeSubprocess()
    )
    monkeypatch.setenv("MIA_AUTONOMY_ENABLED", "true")
    after = capture_baseline_manifest(
        tmp_path, subprocess_run=FakeSubprocess()
    )

    assert before["configuration_hash"] != after["configuration_hash"]
    assert before["runtime_settings"]["MIA_AUTONOMY_ENABLED"] is False
    assert after["runtime_settings"]["MIA_AUTONOMY_ENABLED"] is True


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("MIA_MAX_CPU_PERCENT", "0", "between 1 and 100"),
        ("MIA_MAX_RAM_PERCENT", "0", "between 1 and 100"),
        ("MIA_MAX_CPU_PERCENT", "101", "between 1 and 100"),
        ("MIA_AUTONOMY_INTERVAL_SECONDS", "0", "between 1 and 3600"),
        ("MIA_ALLOW_PAID", "yes", "true or false"),
        ("MIA_MAX_REQUEST_SPEND_USD", "nan", "finite"),
    ],
)
def test_invalid_runtime_policy_prevents_baseline_capture(
    tmp_path, monkeypatch, name, value, message
):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv(name, value)
    _write_baseline_files(tmp_path)

    with pytest.raises(BaselineCaptureError, match=message):
        capture_baseline_manifest(tmp_path, subprocess_run=FakeSubprocess())
