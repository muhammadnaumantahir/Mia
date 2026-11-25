"""MIA-LIFE-07: confirmed research findings enter isolated project memory only."""

import pytest
from fastapi.testclient import TestClient

from mia.life.research import ResearchService
from mia.life.research_memory import ResearchMemoryBridge, ResearchMemoryError
from mia.memory.context import record_in_scope
from mia.memory.store import MemoryStore, project_key
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore
from tests.test_research import NOW, FakeFetcher, PAGES

QUOTE = "The feature is optional and must be enabled when the interpreter is built"


def build(tmp_path):
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    memory.projects.create("Imaanly", goal="Quran and prayer app")
    memory.projects.create("EV360", goal="EV battery monitoring")
    research = ResearchService(
        tmp_path / "research.sqlite3",
        source_allowed=lambda host: host == "docs.example.org",
        fetcher=FakeFetcher(PAGES),
        clock=lambda: NOW,
    )
    run = research.start_run("What is free-threaded Python?", ["https://docs.example.org/page"])
    claim = research.add_claim(
        run["run_id"], "The feature is optional and enabled at build time.",
        [{"source_id": run["sources"][0]["source_id"], "quote": QUOTE}],
    )
    bridge = ResearchMemoryBridge(tmp_path / "research-links.sqlite3", research, memory)
    return memory, research, bridge, run, claim


def confirmed(research, claim):
    return research.review_claim(claim["claim_id"], "confirm")


def test_only_confirmed_findings_import_and_project_scope_is_preserved(tmp_path):
    memory, research, bridge, run, claim = build(tmp_path)
    with pytest.raises(ResearchMemoryError, match="owner-confirmed"):
        bridge.import_finding(claim["claim_id"], "Imaanly")

    confirmed(research, claim)
    record = bridge.import_finding(claim["claim_id"], "Imaanly")
    assert record["record_type"] == "fact"
    assert record["status"] == "active"
    assert record["provenance"] == "source_backed"
    assert record["project"] == "Imaanly"
    assert record["details"]["research_claim_id"] == claim["claim_id"]
    assert record["details"]["research_run_id"] == run["run_id"]
    assert record["details"]["research_citations"][0]["quote"] == QUOTE
    assert record["evidence"] == [{"type": "url", "ref": "https://docs.example.org/page"}]
    assert bridge.import_finding(claim["claim_id"], "Imaanly")["record_id"] == record["record_id"]

    assert [item["record_id"] for item in bridge.list_for_project("Imaanly")] == [record["record_id"]]
    assert bridge.list_for_project("EV360") == []
    assert record_in_scope(record, project_key("Imaanly")) is True
    assert record_in_scope(record, project_key("EV360")) is False
    assert record_in_scope(record, None) is True


def test_correction_requires_a_new_confirmed_finding_and_preserves_project_chain(tmp_path):
    memory, research, bridge, run, original = build(tmp_path)
    confirmed(research, original)
    record = bridge.import_finding(original["claim_id"], "Imaanly")

    replacement = research.add_claim(
        run["run_id"], "The free-threaded build is optional and must be enabled during compilation.",
        [{"source_id": run["sources"][0]["source_id"], "quote": QUOTE}],
    )
    with pytest.raises(ResearchMemoryError, match="owner-confirmed"):
        bridge.correct(record["record_id"], replacement["claim_id"])
    confirmed(research, replacement)

    successor = bridge.correct(record["record_id"], replacement["claim_id"])
    old = memory.structured.get(record["record_id"])
    assert old["status"] == "superseded"
    assert old["superseded_by"] == successor["record_id"]
    assert successor["supersedes_id"] == record["record_id"]
    assert successor["project"] == "Imaanly"
    assert successor["details"]["research_claim_id"] == replacement["claim_id"]
    assert [item["record_id"] for item in bridge.list_for_project("Imaanly")] == [
        successor["record_id"]
    ]


def test_delete_removes_full_correction_chain_and_project_links(tmp_path):
    memory, research, bridge, run, original = build(tmp_path)
    confirmed(research, original)
    record = bridge.import_finding(original["claim_id"], "Imaanly")
    replacement = research.add_claim(
        run["run_id"], "The optional build must be enabled at compilation time.",
        [{"source_id": run["sources"][0]["source_id"], "quote": QUOTE}],
    )
    confirmed(research, replacement)
    successor = bridge.correct(record["record_id"], replacement["claim_id"])

    assert bridge.delete(record["record_id"]) is True
    assert memory.structured.get(record["record_id"]) is None
    assert memory.structured.get(successor["record_id"]) is None
    assert bridge.list_for_project("Imaanly") == []
    assert bridge.delete(record["record_id"]) is False


def test_import_requires_registered_project_and_confirmed_citation(tmp_path):
    _, research, bridge, _run, claim = build(tmp_path)
    with pytest.raises(ResearchMemoryError, match="owner-confirmed"):
        bridge.import_finding(claim["claim_id"], "Unregistered")
    confirmed(research, claim)
    with pytest.raises(ResearchMemoryError, match="Create this project"):
        bridge.import_finding(claim["claim_id"], "Unregistered")


def test_research_memory_http_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    memory.projects.create("Imaanly")
    research = ResearchService(
        tmp_path / "research.sqlite3",
        source_allowed=lambda host: host == "docs.example.org",
        fetcher=FakeFetcher(PAGES),
        clock=lambda: NOW,
    )
    run = research.start_run("What is free-threaded Python?", ["https://docs.example.org/page"])
    first = research.add_claim(
        run["run_id"], "The feature is optional and enabled at build time.",
        [{"source_id": run["sources"][0]["source_id"], "quote": QUOTE}],
    )
    confirmed(research, first)
    app = create_app(
        store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
        memory=memory,
        research=research,
    )
    client = TestClient(app)

    imported = client.post(
        f"/api/research/claims/{first['claim_id']}/memory", json={"project": "Imaanly"}
    )
    assert imported.status_code == 200, imported.text
    record = imported.json()
    assert record["project"] == "Imaanly"
    assert client.get("/api/research/memory", params={"project": "Imaanly"}).json()["items"]

    second = research.add_claim(
        run["run_id"], "The build option is enabled at compilation time.",
        [{"source_id": run["sources"][0]["source_id"], "quote": QUOTE}],
    )
    confirmed(research, second)
    corrected = client.post(
        f"/api/research/memory/{record['record_id']}/correct",
        json={"replacement_claim_id": second["claim_id"]},
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["supersedes_id"] == record["record_id"]

    deleted = client.delete(f"/api/research/memory/{record['record_id']}")
    assert deleted.status_code == 200 and deleted.json() == {"deleted": True}
    assert client.get("/api/research/memory", params={"project": "Imaanly"}).json()["items"] == []
