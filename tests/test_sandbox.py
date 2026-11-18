import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from mia.evolution.sandbox import (
    DockerSandboxRunner,
    SandboxExecutionError,
    SandboxUnavailableError,
)
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
            if os.name != "nt":
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


def test_sandbox_accepts_large_but_bounded_json_reports():
    class LargeOutputDocker(FakeDocker):
        def __call__(self, command, **kwargs):
            if command[1:3] == ["image", "inspect"]:
                return super().__call__(command, **kwargs)
            if command[1] == "run":
                report = _report()
                report["evidence"]["large_detail"] = "x" * 20_000
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(report),
                    stderr="",
                )
            return super().__call__(command, **kwargs)

    runner = DockerSandboxRunner(subprocess_run=LargeOutputDocker())
    result = runner.validate_candidate(
        source_code="def f(): return 1\n",
        test_source_code="def test_f(): assert True\n",
    )

    assert result["unit_tests"] is True
    assert len(result["evidence"]["large_detail"]) == 20_000


def test_candidate_tests_are_skipped_after_security_findings():
    from pathlib import Path

    from mia.evolution.sandbox_worker import _candidate_tests_allowed

    test_path = Path("/candidate/test_candidate.py")
    assert _candidate_tests_allowed(test_path, {"candidate": [], "tests": []}) is True
    assert _candidate_tests_allowed(
        test_path,
        {"candidate": ["forbidden import: subprocess"], "tests": []},
    ) is False
    assert _candidate_tests_allowed(
        test_path,
        {"candidate": [], "tests": ["forbidden import: socket"]},
    ) is False
    assert _candidate_tests_allowed(None, {"candidate": [], "tests": []}) is False


def test_worker_security_policy_blocks_builtin_file_access_and_interpreter_reflection():
    direct = "def run(data): return open('/tmp/candidate.py').read()\n"
    builtin = "def run(data): return __builtins__['open']('/tmp/candidate.py').read()\n"

    direct_findings = _security_findings(direct)
    builtin_findings = _security_findings(builtin)

    assert "forbidden dynamic call: open" in direct_findings
    assert "forbidden interpreter reference: __builtins__" in builtin_findings



def test_docker_sandbox_executes_capability_in_a_separate_restricted_container():
    class CapabilityDocker(FakeDocker):
        def __init__(self):
            super().__init__()
            self.stdin_payload = None

        def __call__(self, command, **kwargs):
            if command[1:3] == ["image", "inspect"]:
                return super().__call__(command, **kwargs)
            if command[1] == "run":
                self.calls.append((command, kwargs))
                self.stdin_payload = kwargs.get("input")
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps({"ok": True, "result": {"answer": 42}}),
                    stderr="",
                )
            return super().__call__(command, **kwargs)

    docker = CapabilityDocker()
    runner = DockerSandboxRunner(subprocess_run=docker)
    source = "def run(input_data):\n    return {}\n"
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()

    result = runner.execute_capability(
        source_code=source,
        source_sha256=source_hash,
        input_data={"value": 41},
    )

    run_command = next(call[0] for call in docker.calls if call[0][1] == "run")
    assert docker.stdin_payload == '{"value":41}'
    assert "--network=none" in run_command
    assert "--read-only" in run_command
    assert "--cap-drop=ALL" in run_command
    assert "--user=10001:10001" in run_command
    assert "--entrypoint" in run_command
    assert run_command[run_command.index("--entrypoint") + 1] == "python"
    assert "--pids-limit=32" in run_command
    assert "target=/candidate,readonly" in run_command[run_command.index("--mount") + 1]
    assert result["result"] == {"answer": 42}
    assert result["runner"] == "docker-capability-execution-v1"


def test_capability_execution_rejects_input_over_limit_and_hash_mismatch():
    runner = DockerSandboxRunner(subprocess_run=FakeDocker())
    source = "def run(input_data):\n    return {}\n"
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()

    with pytest.raises(ValueError, match="32 KB"):
        runner.execute_capability(
            source_code=source,
            source_sha256=source_hash,
            input_data={"value": "x" * (32 * 1024)},
        )
    with pytest.raises(SandboxExecutionError, match="hash changed"):
        runner.execute_capability(
            source_code=source,
            source_sha256="0" * 64,
            input_data={},
        )



def test_capability_docker_timeout_removes_container():
    docker = FakeDocker(timeout=True)
    runner = DockerSandboxRunner(subprocess_run=docker)
    source = "def run(input_data):\n    return {}\n"
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()

    with pytest.raises(SandboxExecutionError, match="60-second"):
        runner.execute_capability(
            source_code=source,
            source_sha256=source_hash,
            input_data={},
        )
    assert any(call[0][1:3] == ["rm", "--force"] for call in docker.calls)


def test_capability_docker_rejects_non_worker_json_result():
    runner = DockerSandboxRunner(subprocess_run=FakeDocker())
    source = "def run(input_data):\n    return {}\n"
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()

    with pytest.raises(SandboxExecutionError, match="invalid result contract"):
        runner.execute_capability(
            source_code=source,
            source_sha256=source_hash,
            input_data={},
        )
