from types import SimpleNamespace

from fastapi.testclient import TestClient

from mia.agent.resources import ResourceMonitor
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


class FakeProcess:
    def __init__(self, pid, name, status="running"):
        self.info = {"pid": pid, "name": name, "status": status}


class FakePsutil:
    class NoSuchProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass

    class ZombieProcess(Exception):
        pass

    def cpu_percent(self, interval):
        assert interval == 0.2
        return 31.5

    def virtual_memory(self):
        return SimpleNamespace(percent=61.0, available=6_000, total=16_000)

    def process_iter(self, attrs):
        assert attrs == ["pid"] or attrs == ["pid", "name", "status"]
        if attrs == ["pid"]:
            return iter([FakeProcess(1, "System"), FakeProcess(2, "Mia")])
        return iter([
            FakeProcess(1, "System"),
            FakeProcess(2, "Mia"),
            FakeProcess(3, "Browser"),
        ])


def test_resource_monitor_exposes_only_bounded_process_metadata():
    monitor = ResourceMonitor(psutil_module=FakePsutil())
    snapshot = monitor.snapshot()
    assert snapshot["cpu_percent"] == 31.5
    assert snapshot["memory_percent"] == 61.0
    assert snapshot["process_count"] == 2

    processes = monitor.list_processes(limit=2)
    assert len(processes) == 2
    assert processes[0] == {"pid": 1, "name": "System", "status": "running"}
    assert "cmdline" not in processes[0]
    assert "environment" not in processes[0]


def test_system_resource_api_returns_real_monitor_snapshot(tmp_path):
    monitor = ResourceMonitor(psutil_module=FakePsutil())
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            resource_monitor=monitor,
        )
    )
    resources = client.get("/api/system/resources")
    assert resources.status_code == 200
    assert resources.json()["cpu_percent"] == 31.5

    processes = client.get("/api/system/processes?limit=2")
    assert processes.status_code == 200
    assert len(processes.json()["items"]) == 2
