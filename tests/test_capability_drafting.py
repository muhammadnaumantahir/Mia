import json

import pytest
from fastapi.testclient import TestClient

from mia.evolution.capabilities import CapabilityStore
from mia.gateway.service import CompletionResult
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


class DraftGateway:
    def __init__(self, content, tool_calls=()):
        self.content = content
        self.tool_calls = tool_calls
        self.policy = None
        self.prompt = ""

    async def discover_models(self):
        return [object()]

    async def complete(self, request, candidates, policy):
        self.policy = policy
        self.prompt = request.prompt
        return CompletionResult(
            provider_id="local",
            model_id="draft-model",
            content=self.content,
            attempted_candidates=("local/draft-model",),
            tool_calls=self.tool_calls,
            data_local=True,
        )


VALID_DRAFT = {
    "source_code": (
        "def run(input_data: dict) -> dict:\n"
        "    value = input_data.get('value')\n"
        "    if not isinstance(value, int):\n"
        "        raise ValueError('value must be an integer')\n"
        "    return {'doubled': value * 2}\n"
    ),
    "test_source_code": (
        "from candidate import run\n\n"
        "def test_run():\n"
        "    assert run({'value': 4}) == {'doubled': 8}\n\n"
        "def test_run_rejects_invalid_value():\n"
        "    import pytest\n"
        "    with pytest.raises(ValueError):\n"
        "        run({'value': '4'})\n"
    ),
}


def _client(tmp_path, gateway):
    capabilities = CapabilityStore(
        tmp_path / "capabilities.sqlite3",
        tmp_path / "generated",
    )
    app = create_app(
        store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
        gateway=gateway,
        capability_store=capabilities,
    )
    return TestClient(app), capabilities


def test_ai_capability_draft_is_free_only_and_not_persisted(tmp_path):
    gateway = DraftGateway(json.dumps(VALID_DRAFT))
    client, capabilities = _client(tmp_path, gateway)

    response = client.post(
        "/api/capabilities/draft",
        json={
            "capability_id": "double-value",
            "name": "Double value",
            "objective": "Create a pure function that doubles an integer from input_data.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source_code"] == VALID_DRAFT["source_code"]
    assert body["test_source_code"] == VALID_DRAFT["test_source_code"]
    assert body["persisted"] is False
    assert body["executed"] is False
    assert gateway.policy.allow_paid is False
    assert gateway.policy.max_request_cost_usd == 0.0
    assert "no filesystem" in gateway.prompt.lower() or "do not access files" in gateway.prompt.lower()
    assert capabilities.list_versions() == []


def test_ai_capability_draft_rejects_tool_calls_and_unsafe_source(tmp_path):
    gateway = DraftGateway(
        json.dumps(VALID_DRAFT),
        tool_calls=({"name": "filesystem.write"},),
    )
    client, _ = _client(tmp_path / "tools", gateway)
    response = client.post(
        "/api/capabilities/draft",
        json={
            "capability_id": "double-value",
            "name": "Double value",
            "objective": "Draft a pure integer double function.",
        },
    )
    assert response.status_code == 502
    assert "tool calls" in response.json()["detail"].lower()

    unsafe = dict(VALID_DRAFT)
    unsafe["source_code"] = "import subprocess\n\ndef run(input_data): return {}\n"
    client, capabilities = _client(tmp_path / "unsafe", DraftGateway(json.dumps(unsafe)))
    response = client.post(
        "/api/capabilities/draft",
        json={
            "capability_id": "unsafe-draft",
            "name": "Unsafe draft",
            "objective": "Try to import subprocess.",
        },
    )
    assert response.status_code == 502
    assert capabilities.list_versions() == []


def test_ai_capability_draft_requires_run_function_and_candidate_tests(tmp_path):
    invalid = {
        "source_code": "def calculate(value): return value\n",
        "test_source_code": "def test_value(): assert True\n",
    }
    client, capabilities = _client(tmp_path, DraftGateway(json.dumps(invalid)))
    response = client.post(
        "/api/capabilities/draft",
        json={
            "capability_id": "missing-run",
            "name": "Missing run",
            "objective": "Draft an example.",
        },
    )
    assert response.status_code == 502
    assert "run(input_data)" in response.json()["detail"]
    assert capabilities.list_versions() == []


def test_ai_capability_draft_rejects_bad_json_and_invalid_request(tmp_path):
    client, capabilities = _client(tmp_path, DraftGateway("not json"))
    response = client.post(
        "/api/capabilities/draft",
        json={
            "capability_id": "bad-json",
            "name": "Bad JSON",
            "objective": "Draft a pure function.",
        },
    )
    assert response.status_code == 502
    assert response.json()["detail"] == "Model returned invalid capability JSON"

    invalid_request = client.post(
        "/api/capabilities/draft",
        json={
            "capability_id": "Bad ID",
            "name": "Bad ID",
            "objective": "Draft a pure function.",
        },
    )
    assert invalid_request.status_code == 422
    assert capabilities.list_versions() == []
