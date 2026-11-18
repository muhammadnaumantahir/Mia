import json

from fastapi.testclient import TestClient

from mia.computer.approvals import ApprovalStore
from mia.computer.filesystem import FilesystemAdapter
from mia.gateway.contracts import ModelCandidate, ModelRequest, ProviderResponse
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


class ToolCallingProvider:
    provider_id = "tool-test"

    def __init__(self, tool_name, arguments, *, data_local=True):
        self.tool_name = tool_name
        self.arguments = arguments
        self.data_local = data_local
        self.calls = 0
        self.tool_output = None

    async def health(self):
        return True

    async def list_models(self):
        return [
            ModelCandidate(
                provider_id=self.provider_id,
                model_id="tool-model",
                capabilities=frozenset({"chat", "tools"}),
                context_window=8192,
                free_tier=True,
                data_local=self.data_local,
                healthy=True,
                enabled=True,
            )
        ]

    async def complete(self, candidate, request: ModelRequest):
        self.calls += 1
        tool_messages = [
            message for message in request.messages
            if message.get("role") == "tool"
        ]
        if not tool_messages:
            assert request.tools
            return ProviderResponse(
                content="",
                tool_calls=({
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": self.tool_name,
                        "arguments": json.dumps(self.arguments),
                    },
                },),
                finish_reason="tool_calls",
            )
        self.tool_output = json.loads(tool_messages[-1]["content"])
        return ProviderResponse(
            content="I checked the requested information and prepared the result."
        )


def build_client(tmp_path, monkeypatch, provider, filesystem=None, approvals=None):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    registry = ProviderRegistry()
    registry.register(provider)
    return TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            gateway=ModelGateway(registry),
            filesystem=filesystem,
            approvals=approvals,
        )
    )


def test_chat_can_call_read_only_filesystem_tool(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "notes.txt").write_text("Mia is testing governed tools.", encoding="utf-8")
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    filesystem = FilesystemAdapter([root], approvals)
    provider = ToolCallingProvider(
        "filesystem.read_text",
        {"root_id": "root_1", "relative_path": "notes.txt"},
    )
    client = build_client(tmp_path, monkeypatch, provider, filesystem, approvals)

    response = client.post(
        "/api/gateway/chat",
        json={"prompt": "Read notes.txt", "use_tools": True},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["tools_available"] is True
    assert payload["pending_actions"] == []
    assert provider.tool_output["content"] == "Mia is testing governed tools."
    assert "checked" in payload["content"].lower()


def test_chat_write_tool_only_previews_until_owner_approves(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    filesystem = FilesystemAdapter([root], approvals)
    provider = ToolCallingProvider(
        "filesystem.preview_write",
        {
            "root_id": "root_1",
            "relative_path": "notes/new.txt",
            "content": "Created only after approval.",
        },
    )
    client = build_client(tmp_path, monkeypatch, provider, filesystem, approvals)

    response = client.post(
        "/api/gateway/chat",
        json={"prompt": "Create notes/new.txt", "use_tools": True},
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["pending_actions"]) == 1
    pending = payload["pending_actions"][0]
    assert pending["kind"] == "filesystem.write"
    assert not (root / "notes" / "new.txt").exists()

    execution = client.post(
        f"/api/approvals/{pending['approval_id']}/execute",
        json={
            "kind": pending["kind"],
            "payload": pending["payload"],
        },
    )
    assert execution.status_code == 200
    assert execution.json()["status"] == "completed"
    assert (root / "notes" / "new.txt").read_text(encoding="utf-8") == (
        "Created only after approval."
    )


def test_rejected_tool_action_never_changes_the_file(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    filesystem = FilesystemAdapter([root], approvals)
    provider = ToolCallingProvider(
        "filesystem.preview_write",
        {
            "root_id": "root_1",
            "relative_path": "notes/no.txt",
            "content": "Never written.",
        },
    )
    client = build_client(tmp_path, monkeypatch, provider, filesystem, approvals)

    response = client.post(
        "/api/gateway/chat",
        json={"prompt": "Prepare a new file", "use_tools": True},
    )
    pending = response.json()["pending_actions"][0]
    rejected = client.post(
        f"/api/approvals/{pending['approval_id']}/reject",
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert not (root / "notes" / "no.txt").exists()

    replay = client.post(
        f"/api/approvals/{pending['approval_id']}/execute",
        json={"kind": pending["kind"], "payload": pending["payload"]},
    )
    assert replay.status_code == 409
    assert not (root / "notes" / "no.txt").exists()


def test_remote_provider_does_not_receive_local_file_contents_by_default(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "notes.txt").write_text(
        "Do not send this to a remote model.",
        encoding="utf-8",
    )
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    filesystem = FilesystemAdapter([root], approvals)
    provider = ToolCallingProvider(
        "filesystem.read_text",
        {"root_id": "root_1", "relative_path": "notes.txt"},
        data_local=False,
    )
    client = build_client(tmp_path, monkeypatch, provider, filesystem, approvals)

    response = client.post(
        "/api/gateway/chat",
        json={"prompt": "Read notes.txt", "use_tools": True},
    )
    assert response.status_code == 200
    assert "blocked from non-local" in provider.tool_output["error"]
    assert "Do not send this to a remote model." not in json.dumps(provider.tool_output)
