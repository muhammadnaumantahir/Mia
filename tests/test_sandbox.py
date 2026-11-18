import json
import subprocess

import pytest

from mia.evolution.sandbox import DockerSandboxRunner, SandboxUnavailableError
from mia.evolution.sandbox_worker import _security_findings


def _report():
    return {
        "static_checks": True,
        "unit_tests": True,
        "security_checks": True,
        "regression_tests": True,
        "evidence": {"unit": {"passed": True}},
    }


class FakeDocker:
    def __init__(self, *, image_available=True, timeout=False):
        self.calls = []
        self.image_available = image_available
        self.timeout = timeout

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[1:3] == ["image", "inspect"]:
            code = 0 if self.image_available else 1
            return subprocess.CompletedProcess(
                command,
                code,
                stdout="sha256:fake-image\n" if code == 0 else "",
                stderr="missing image" if code else "",
            )
        if command[1] == "run":
            mount = command[command.index("--mount") + 1]
            source_dir = mount.split("source=", 1)[1].split(",target=", 1)[0]
            staging = Path(source_dir)
            assert staging.stat().st_mode & 0o111
            assert (staging / "candidate.py").stat().st_mode & 0o444
            assert not ((staging / "candidate.py").stat().st_mode & 0o222)
            if (staging / "test_candidate.py").exists():
                assert (staging / "test_candidate.py").stat().st_mode & 0o444
                assert not ((staging / "test_candidate.py").stat().st_mode & 0o222)
            if self.timeout:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(_report()),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def test_docker_sandbox_uses_no_network_and_only_read_only_candidate_mount():
    docker = FakeDocker()
    runner = DockerSandboxRunner(subprocess_run=docker)
    source = "def calculate(value):\n    return value + 1\n"
    tests = "from candidate import calculate\n\ndef test_calculate():\n    assert calculate(1) == 2\n"

    report = runner.validate_candidate(source_code=source, test_source_code=tests)

    run_command = next(call[0] for call in docker.calls if call[0][1] == "run")
    assert "--network=none" in run_command
    assert "--ipc=none" in run_command
    assert "--cgroupns=private" in run_command
    assert "--init" in run_command
    assert "--memory-swap=768m" in run_command
    assert "--read-only" in run_command
    assert "--cap-drop=ALL" in run_command
    assert "--security-opt=no-new-privileges:true" in run_command
    assert "--pids-limit=64" in run_command
    assert "--memory=768m" in run_command
    assert "--cpus=1.0" in run_command
    assert "--user=10001:10001" in run_command
    mount = run_command[run_command.index("--mount") + 1]
    assert "target=/candidate,readonly" in mount
    assert report["static_checks"] is True
    assert report["evidence"]["source_sha256"]
    assert report["evidence"]["test_sha256"]


def test_sandbox_fails_closed_when_image_is_unavailable():
    runner = DockerSandboxRunner(subprocess_run=FakeDocker(image_available=False))
    assert runner.is_available() is False
    with pytest.raises(SandboxUnavailableError):
        runner.validate_candidate(source_code="def f(): return 1\n", test_source_code=None)


def test_sandbox_timeout_returns_failed_validation_report_and_removes_container():
    docker = FakeDocker(timeout=True)
    runner = DockerSandboxRunner(subprocess_run=docker)
    report = runner.validate_candidate(
        source_code="def f(): return 1\n",
        test_source_code="def test_f(): assert True\n",
    )
    assert report["static_checks"] is False
    assert report["unit_tests"] is False
    assert any(call[0][1:3] == ["rm", "--force"] for call in docker.calls)


def test_worker_security_policy_blocks_process_and_network_imports():
    source = "import subprocess\nimport socket\n\ndef run():\n    return 1\n"
    findings = _security_findings(source)
    assert "forbidden import: subprocess" in findings
    assert "forbidden import: socket" in findings


def test_worker_security_policy_allows_simple_pure_logic():
    source = "import math\n\ndef area(radius):\n    return math.pi * radius ** 2\n"
    assert _security_findings(source) == []



def test_worker_security_policy_scans_candidate_tests_too():
    from mia.evolution.sandbox_worker import _all_security_findings

    findings = _all_security_findings(
        "def calculate(value):\n    return value + 1\n",
        "import subprocess\n\ndef test_example():\n    assert True\n",
    )
    assert findings["candidate"] == []
    assert "forbidden import: subprocess" in findings["tests"]
