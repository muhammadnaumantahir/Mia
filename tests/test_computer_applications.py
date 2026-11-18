from mia.computer.approvals import ApprovalError, ApprovalStore
from mia.computer.applications import (
    ApplicationDefinition,
    ApplicationLaunchError,
    ApplicationLauncher,
)


def test_application_launch_requires_approval_and_fixed_catalog(tmp_path, monkeypatch):
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    launcher = ApplicationLauncher(
        [
            ApplicationDefinition(
                "test-app",
                "Test App",
                str(tmp_path / "fake-app.exe"),
                ("--safe-mode",),
            )
        ],
        approvals,
    )

    monkeypatch.setattr(
        "mia.computer.applications.ApplicationLauncher._resolve_executable",
        staticmethod(lambda value: str(tmp_path / "fake-app.exe")),
    )
    captured = {}

    class FakeProcess:
        pid = 4321

    def fake_popen(command, *, shell, close_fds):
        captured["command"] = command
        captured["shell"] = shell
        captured["close_fds"] = close_fds
        return FakeProcess()

    monkeypatch.setattr("mia.computer.applications.subprocess.Popen", fake_popen)

    preview = launcher.preview_launch("test-app")
    try:
        launcher.execute_launch("test-app", preview["approval_id"])
    except ApprovalError:
        pass
    else:
        raise AssertionError("Launch must require approval")

    approvals.approve(preview["approval_id"])
    result = launcher.execute_launch("test-app", preview["approval_id"])
    assert result["process_id"] == 4321
    assert captured["command"] == [
        str(tmp_path / "fake-app.exe"),
        "--safe-mode",
    ]
    assert captured["shell"] is False


def test_unlisted_and_shell_applications_are_blocked(tmp_path):
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    launcher = ApplicationLauncher(
        [ApplicationDefinition("shell", "Shell", "powershell.exe")],
        approvals,
    )

    try:
        launcher.preview_launch("unknown")
    except ApplicationLaunchError as exc:
        assert "allowlist" in str(exc).lower()
    else:
        raise AssertionError("Unknown applications must be blocked")

    try:
        launcher.preview_launch("shell")
    except ApplicationLaunchError as exc:
        assert "blocked" in str(exc).lower()
    else:
        raise AssertionError("Shell applications must be blocked")
