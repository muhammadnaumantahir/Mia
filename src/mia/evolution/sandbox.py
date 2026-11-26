"""Docker-backed validation runner for untrusted capability candidates."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any


class SandboxUnavailableError(RuntimeError):
    """The configured Docker sandbox image or runtime is unavailable."""


class SandboxExecutionError(RuntimeError):
    """The isolated validation container could not produce a trustworthy report."""


class DockerSandboxRunner:
    """Run candidate tests in a disposable, constrained Docker container.

    The container receives only a read-only mount containing the candidate and
    its tests. It has no network, no extra Linux capabilities, no Docker socket,
    no writable host mount, and bounded CPU, memory, PIDs and runtime.
    """

    MAX_SOURCE_BYTES = 512 * 1024
    OUTPUT_LIMIT_CHARS = 128_000

    def __init__(
        self,
        *,
        docker_executable: str = "docker",
        image: str = "mia-sandbox:py313-v1",
        timeout_seconds: int = 360,
        memory_limit: str = "768m",
        cpu_limit: str = "1.0",
        subprocess_run: Callable[..., Any] | None = None,
    ) -> None:
        if not docker_executable.strip():
            raise ValueError("docker_executable is required")
        if not image.strip() or any(ch.isspace() for ch in image):
            raise ValueError("image must be a non-empty Docker image reference")
        if not 30 <= timeout_seconds <= 900:
            raise ValueError("timeout_seconds must be between 30 and 900")
        if memory_limit not in {"512m", "768m", "1g", "1536m"}:
            raise ValueError("memory_limit must be one of the supported bounded values")
        if cpu_limit not in {"0.5", "1.0", "1.5", "2.0"}:
            raise ValueError("cpu_limit must be one of the supported bounded values")
        self.docker_executable = docker_executable
        self.image = image
        self.timeout_seconds = timeout_seconds
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self._run = subprocess_run or subprocess.run

    def _image_id(self) -> str:
        command = [
            self.docker_executable,
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            self.image,
        ]
        try:
            result = self._run(
                command,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise SandboxUnavailableError(
                "Docker is unavailable. Install/start Docker Desktop and build the Mia sandbox image."
            ) from exc
        image_id = (result.stdout or "").strip()
        if result.returncode != 0 or not image_id.startswith("sha256:"):
            raise SandboxUnavailableError(
                "The Mia sandbox image is not available. Build it with "
                "docker build --pull -f sandbox/Dockerfile -t mia-sandbox:py313-v1 ."
            )
        return image_id

    def is_available(self) -> bool:
        try:
            self._image_id()
        except SandboxUnavailableError:
            return False
        return True

    @staticmethod
    def _hash(text: str | None) -> str | None:
        if text is None:
            return None
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _failed_report(
        self,
        *,
        source_sha256: str,
        test_sha256: str | None,
        reason: str,
        image_id: str | None = None,
        container_name: str | None = None,
    ) -> dict[str, Any]:
        return {
            "static_checks": False,
            "unit_tests": False,
            "security_checks": False,
            "regression_tests": False,
            "evidence": {
                "runner": "docker-isolated-v1",
                "image": self.image,
                "image_id": image_id,
                "source_sha256": source_sha256,
                "test_sha256": test_sha256,
                "container_name": container_name,
                "error": reason[:2000],
                "limits": {
                    "network": "none",
                    "memory": self.memory_limit,
                    "cpus": self.cpu_limit,
                    "pids": 64,
                    "root_filesystem": "read-only",
                    "host_mount": "candidate-and-tests-read-only",
                },
            },
        }

    def _remove_container(self, container_name: str) -> None:
        try:
            self._run(
                [self.docker_executable, "rm", "--force", container_name],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    @staticmethod
    def _validate_worker_report(
        report: Any,
        *,
        source_sha256: str,
        test_sha256: str | None,
    ) -> dict[str, Any]:
        """Validate the worker's evidence before attaching trusted host metadata.

        A successful container exit is not validation evidence by itself. The worker must
        report hashes for the exact read-only inputs and stage details that agree with every
        top-level gate. Missing, malformed or contradictory evidence fails closed.
        """
        required_flags = {
            "static_checks", "unit_tests", "security_checks", "regression_tests",
        }
        if (
            not isinstance(report, dict)
            or not required_flags.issubset(report)
            or any(not isinstance(report.get(key), bool) for key in required_flags)
        ):
            raise SandboxExecutionError("Sandbox worker returned an invalid validation report")

        evidence = report.get("evidence")
        evidence_keys = {
            "source_sha256", "test_sha256", "parse_errors", "security_findings",
            "static", "unit", "regression", "security_policy",
        }
        if not isinstance(evidence, dict) or not evidence_keys.issubset(evidence):
            raise SandboxExecutionError("Sandbox worker returned incomplete validation evidence")

        if evidence.get("source_sha256") != source_sha256:
            raise SandboxExecutionError(
                "Sandbox worker candidate source hash does not match the mounted candidate"
            )
        if evidence.get("test_sha256") != test_sha256:
            raise SandboxExecutionError(
                "Sandbox worker candidate test hash does not match the mounted tests"
            )

        parse_errors = evidence.get("parse_errors")
        if not isinstance(parse_errors, list) or any(
            not isinstance(item, str) or not item for item in parse_errors
        ):
            raise SandboxExecutionError("Sandbox worker returned invalid parse-error evidence")

        findings = evidence.get("security_findings")
        if (
            not isinstance(findings, dict)
            or set(findings) != {"candidate", "tests"}
            or any(
                not isinstance(findings.get(key), list)
                or any(not isinstance(item, str) or not item for item in findings[key])
                for key in ("candidate", "tests")
            )
        ):
            raise SandboxExecutionError("Sandbox worker returned invalid security evidence")

        stages: dict[str, bool] = {}
        for name in ("static", "unit", "regression"):
            result = evidence.get(name)
            if not isinstance(result, dict) or not {
                "passed", "exit_code", "stdout", "stderr",
            }.issubset(result):
                raise SandboxExecutionError(
                    f"Sandbox worker returned invalid {name} stage evidence"
                )
            passed = result.get("passed")
            exit_code = result.get("exit_code")
            stdout = result.get("stdout")
            stderr = result.get("stderr")
            if (
                not isinstance(passed, bool)
                or (
                    exit_code is not None
                    and (not isinstance(exit_code, int) or isinstance(exit_code, bool))
                )
                or not isinstance(stdout, str)
                or not isinstance(stderr, str)
                or len(stdout) > 8_000
                or len(stderr) > 8_000
                or passed != (exit_code == 0)
            ):
                raise SandboxExecutionError(
                    f"Sandbox worker returned inconsistent {name} stage evidence"
                )
            stages[name] = passed

        if evidence.get("security_policy") != "static AST denylist; defense in depth, not a proof":
            raise SandboxExecutionError("Sandbox worker used an unknown security policy")

        expected_flags = {
            "static_checks": stages["static"] and not parse_errors,
            "unit_tests": stages["unit"] and test_sha256 is not None,
            "security_checks": not any(findings.values()),
            "regression_tests": stages["regression"],
        }
        for name, expected in expected_flags.items():
            if report[name] is not expected:
                raise SandboxExecutionError(
                    f"Sandbox worker {name} flag contradicts its supporting evidence"
                )
        return report

    def validate_candidate(
        self,
        *,
        source_code: str,
        test_source_code: str | None,
        source_sha256: str | None = None,
        test_sha256: str | None = None,
    ) -> dict[str, Any]:
        source_bytes = source_code.encode("utf-8")
        test_bytes = test_source_code.encode("utf-8") if test_source_code is not None else None
        if not source_bytes or len(source_bytes) > self.MAX_SOURCE_BYTES:
            raise ValueError("candidate source must be 1-512 KB")
        if test_bytes is not None and (
            not test_bytes or len(test_bytes) > self.MAX_SOURCE_BYTES
        ):
            raise ValueError("candidate tests must be 1-512 KB when supplied")

        actual_source_hash = hashlib.sha256(source_bytes).hexdigest()
        actual_test_hash = hashlib.sha256(test_bytes).hexdigest() if test_bytes else None
        if source_sha256 is not None and source_sha256 != actual_source_hash:
            raise SandboxExecutionError("Candidate source hash changed before validation")
        if test_sha256 is not None and test_sha256 != actual_test_hash:
            raise SandboxExecutionError("Candidate test hash changed before validation")

        image_id = self._image_id()
        container_name = f"mia-validation-{uuid.uuid4().hex[:16]}"

        with tempfile.TemporaryDirectory(prefix="mia-sandbox-") as temporary:
            staging = Path(temporary).resolve()
            # tempfile creates owner-only directories; UID 10001 inside Docker
            # must be able to traverse the mount and read its files.
            staging.chmod(0o755)
            candidate_path = staging / "candidate.py"
            candidate_path.write_bytes(source_bytes)
            candidate_path.chmod(0o444)
            if test_bytes is not None:
                test_path = staging / "test_candidate.py"
                test_path.write_bytes(test_bytes)
                test_path.chmod(0o444)

            mount_path = staging.as_posix()
            mount_spec = (
                f"type=bind,source={mount_path},target=/candidate,readonly"
            )
            command = [
                self.docker_executable,
                "run",
                "--rm",
                "--pull=never",
                "--name",
                container_name,
                "--network=none",
                "--ipc=none",
                "--cgroupns=private",
                "--init",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true",
                "--pids-limit=64",
                f"--memory={self.memory_limit}",
                f"--memory-swap={self.memory_limit}",
                f"--cpus={self.cpu_limit}",
                "--ulimit=nofile=256:256",
                "--user=10001:10001",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,noexec,size=128m",
                "--mount",
                mount_spec,
                "--workdir=/opt/mia",
                "--env",
                "HOME=/tmp",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--env",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
                "--env",
                "PYTHONPATH=/candidate",
                "--env",
                "MIA_SANDBOX_SOURCE=/candidate/candidate.py",
                "--env",
                (
                    "MIA_SANDBOX_TEST=/candidate/test_candidate.py"
                    if test_bytes is not None
                    else "MIA_SANDBOX_TEST="
                ),
                image_id,
            ]
            try:
                result = self._run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                self._remove_container(container_name)
                return self._failed_report(
                    source_sha256=actual_source_hash,
                    test_sha256=actual_test_hash,
                    reason="Sandbox validation exceeded its wall-clock time limit",
                    image_id=image_id,
                    container_name=container_name,
                )
            except FileNotFoundError as exc:
                raise SandboxUnavailableError("Docker executable disappeared") from exc

        stdout = (result.stdout or "")[-self.OUTPUT_LIMIT_CHARS :]
        stderr = (result.stderr or "")[-self.OUTPUT_LIMIT_CHARS :]
        if result.returncode != 0:
            return self._failed_report(
                source_sha256=actual_source_hash,
                test_sha256=actual_test_hash,
                reason=f"Sandbox container exited with code {result.returncode}: {stderr}",
                image_id=image_id,
                container_name=container_name,
            )

        try:
            report = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise SandboxExecutionError(
                "Sandbox worker did not return a JSON validation report"
            ) from exc
        report = self._validate_worker_report(
            report,
            source_sha256=actual_source_hash,
            test_sha256=actual_test_hash,
        )
        evidence = report["evidence"]
        evidence.update({
            "runner": "docker-isolated-v1",
            "image": self.image,
            "image_id": image_id,
            "source_sha256": actual_source_hash,
            "test_sha256": actual_test_hash,
            "container_name": container_name,
            "limits": {
                "network": "none",
                "memory": self.memory_limit,
                "cpus": self.cpu_limit,
                "pids": 64,
                "root_filesystem": "read-only",
                "host_mount": "candidate-and-tests-read-only",
            },
        })
        if stderr:
            evidence["container_stderr"] = stderr
        report["evidence"] = evidence
        return report

    MAX_CAPABILITY_INPUT_BYTES = 32 * 1024
    MAX_CAPABILITY_OUTPUT_BYTES = 64 * 1024

    def execute_capability(
        self,
        *,
        source_code: str,
        source_sha256: str,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Run a registered pure-data capability once in a fresh Docker container.

        Only the hash-verified candidate source is mounted. Input is JSON on
        stdin; there are no host paths, credentials, network access or runtime
        tool handles in the container.
        """
        source_bytes = source_code.encode("utf-8")
        if not source_bytes or len(source_bytes) > self.MAX_SOURCE_BYTES:
            raise ValueError("capability source must be 1-512 KB")
        actual_source_hash = hashlib.sha256(source_bytes).hexdigest()
        if actual_source_hash != source_sha256:
            raise SandboxExecutionError("Capability source hash changed before execution")
        if not isinstance(input_data, dict):
            raise ValueError("capability input_data must be an object")
        try:
            input_json = json.dumps(
                input_data,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("capability input_data must contain JSON values only") from exc
        if len(input_json.encode("utf-8")) > self.MAX_CAPABILITY_INPUT_BYTES:
            raise ValueError("capability input_data exceeds 32 KB")

        image_id = self._image_id()
        container_name = f"mia-capability-{uuid.uuid4().hex[:16]}"
        with tempfile.TemporaryDirectory(prefix="mia-capability-") as temporary:
            staging = Path(temporary).resolve()
            staging.chmod(0o755)
            candidate_path = staging / "candidate.py"
            candidate_path.write_bytes(source_bytes)
            candidate_path.chmod(0o444)
            mount_spec = (
                f"type=bind,source={staging.as_posix()},target=/candidate,readonly"
            )
            command = [
                self.docker_executable,
                "run",
                "--rm",
                "--pull=never",
                "--name",
                container_name,
                "--network=none",
                "--ipc=none",
                "--cgroupns=private",
                "--init",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true",
                "--pids-limit=32",
                f"--memory={self.memory_limit}",
                f"--memory-swap={self.memory_limit}",
                f"--cpus={self.cpu_limit}",
                "--ulimit=nofile=128:128",
                "--user=10001:10001",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,noexec,size=16m",
                "--mount",
                mount_spec,
                "--workdir=/opt/mia",
                "--env",
                "HOME=/tmp",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--env",
                "PYTHONPATH=/candidate",
                "--env",
                "MIA_CAPABILITY_SOURCE=/candidate/candidate.py",
                "--entrypoint",
                "python",
                image_id,
                "-m",
                "mia.evolution.capability_worker",
            ]
            try:
                result = self._run(
                    command,
                    input=input_json,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                self._remove_container(container_name)
                raise SandboxExecutionError(
                    "Capability execution exceeded its 60-second time limit"
                ) from exc
            except FileNotFoundError as exc:
                raise SandboxUnavailableError("Docker executable disappeared") from exc

        stdout = result.stdout or ""
        if len(stdout.encode("utf-8")) > self.MAX_CAPABILITY_OUTPUT_BYTES:
            raise SandboxExecutionError("Capability output exceeded 64 KB")
        if result.returncode != 0:
            raise SandboxExecutionError(
                "Isolated capability worker exited without a successful result"
            )
        try:
            report = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise SandboxExecutionError(
                "Capability worker did not return valid JSON"
            ) from exc
        if (
            not isinstance(report, dict)
            or set(report) != {"ok", "result"}
            or report["ok"] is not True
            or not isinstance(report["result"], dict)
        ):
            raise SandboxExecutionError("Capability worker returned an invalid result contract")
        return {
            "result": report["result"],
            "source_sha256": actual_source_hash,
            "image_id": image_id,
            "runner": "docker-capability-execution-v1",
        }

