import re
from pathlib import Path

from fastapi.testclient import TestClient

from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.life.store import LifeStore
from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

PAGE = Path(__file__).resolve().parents[1] / "src" / "mia" / "life" / "life.html"


class Monitor:
    def snapshot(self):
        return {"cpu_percent": 1.0, "memory_percent": 1.0}


def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MIA_LIFE_ENABLED", "false")
    app = create_app(
        store=ObservatoryStore(tmp_path / "o.sqlite3"),
        gateway=ModelGateway(ProviderRegistry()),
        memory=MemoryStore(tmp_path / "m.sqlite3"),
        life=LifeStore(tmp_path / "l.sqlite3"),
        resource_monitor=Monitor(),
    )
    return TestClient(app)


def test_life_page_is_served(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as c:
        response = c.get("/life")
    assert response.status_code == 200 and "text/html" in response.headers["content-type"]
    assert "Autonomous life" in response.text


def test_page_never_writes_data_as_html():
    source = PAGE.read_text(encoding="utf-8")
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert forbidden not in source


def test_every_endpoint_the_page_calls_exists(tmp_path, monkeypatch):
    source = PAGE.read_text(encoding="utf-8")
    called = {re.sub(r"'\s*\+.*", "", m) for m in re.findall(r"api\('(/api/life/[^']*)", source)}
    with client(tmp_path, monkeypatch) as c:
        routes = {route.path for route in c.app.routes}
    templated = [re.sub(r"\{[^}]+\}", "", path) for path in routes]
    for path in called:
        assert any(t.startswith(path.rstrip("/")) or path.startswith(t.rstrip("/")) for t in templated), path
