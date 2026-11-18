from fastapi.testclient import TestClient

from mia.computer.approvals import ApprovalStore
from mia.computer.filesystem import FilesystemAdapter
from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


def test_knowledge_ingest_api_uses_configured_root(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "project.md").write_text(
        "# Mia\n\nThe assistant has ten SPS layers.",
        encoding="utf-8",
    )
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    filesystem = FilesystemAdapter([root], approvals)
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            approvals=approvals,
            filesystem=filesystem,
            memory=memory,
        )
    )

    response = client.post(
        "/api/knowledge/ingest",
        params={"root_id": "root_1", "relative_path": "project.md"},
    )
    assert response.status_code == 201
    item = memory.get_item(response.json()["item_id"])
    assert item["trust_level"] == "untrusted"
    assert item["metadata"]["instructions_trusted"] is False
    assert item["source_title"] == "project.md"
