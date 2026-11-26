"""MIA-LIFE-10: source-backed, reviewable personal knowledge graph."""

import pytest
from fastapi.testclient import TestClient

from mia.memory.knowledge_graph import GraphNodeNotFoundError, KnowledgeGraphError
from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


@pytest.fixture
def memory(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.projects.create("Atlas", goal="Deliver the Atlas workspace")
    store.projects.create("Borealis", goal="Deliver the Borealis workspace")
    return store


def add_record(memory, title, project=None, record_type="entity"):
    return memory.structured.add(
        record_type=record_type,
        title=title,
        body=f"Owner context for {title}.",
        project=project,
    )[0]


def add_task(memory, title, project=None, private=False):
    return memory.personal.add(
        kind="task", title=title, project=project, private=private
    )


def test_graph_links_records_tasks_and_projects_with_provenance(memory):
    person = add_record(memory, "Avery", "Atlas", "entity")
    task = add_task(memory, "Prepare the rollout", "Atlas")
    edge_id, created = memory.knowledge_graph.add(
        from_type="record", from_id=person,
        to_type="task", to_id=task,
        relation="works_on", provenance="source_backed",
        confidence=0.82,
        evidence=[{"type": "message", "ref": "message-123"}],
        note="The source names Avery as the rollout owner.",
    )

    assert created is True
    edge = memory.knowledge_graph.get(edge_id)
    assert edge["state"] == "inferred"
    assert edge["confidence"] == 0.82
    assert edge["evidence"] == [{"type": "message", "ref": "message-123"}]
    assert edge["from_node"]["title"] == "Avery"
    assert edge["to_node"]["title"] == "Prepare the rollout"

    # Duplicate creation is idempotent and does not silently overwrite review metadata.
    same_id, created_again = memory.knowledge_graph.add(
        from_type="record", from_id=person, to_type="task", to_id=task,
        relation="works_on", provenance="owner_provided",
    )
    assert (same_id, created_again) == (edge_id, False)


def test_inferred_edge_requires_evidence_and_owner_review(memory):
    person = add_record(memory, "Morgan", "Atlas")
    task = add_task(memory, "Review privacy controls", "Atlas")

    with pytest.raises(KnowledgeGraphError, match="require at least one evidence"):
        memory.knowledge_graph.add(
            from_type="record", from_id=person, to_type="task", to_id=task,
            relation="works_on", provenance="inferred",
        )

    edge_id, _ = memory.knowledge_graph.add(
        from_type="record", from_id=person, to_type="task", to_id=task,
        relation="works_on", provenance="inferred",
        confidence=0.61, evidence=[{"type": "url", "ref": "https://docs.example.org/policy"}],
    )
    assert memory.knowledge_graph.get(edge_id)["state"] == "inferred"
    confirmed = memory.knowledge_graph.review(edge_id, "confirm")
    assert confirmed["state"] == "confirmed"
    assert confirmed["reviewed_at"]
    with pytest.raises(KnowledgeGraphError, match="only inferred"):
        memory.knowledge_graph.review(edge_id, "reject")


def test_correction_resets_derived_edge_to_inferred_and_keeps_history(memory):
    person = add_record(memory, "Taylor", "Atlas")
    project = memory.projects.get("Atlas")["project_key"]
    edge_id, _ = memory.knowledge_graph.add(
        from_type="record", from_id=person,
        to_type="project", to_id=project,
        relation="related_to", provenance="owner_provided",
    )
    assert memory.knowledge_graph.get(edge_id)["state"] == "confirmed"

    corrected = memory.knowledge_graph.correct(
        edge_id, relation="works_on", provenance="inferred", confidence=0.74,
        evidence=[{"type": "record", "ref": person}], note="Needs owner review.",
    )
    assert corrected["relation"] == "works_on"
    assert corrected["state"] == "inferred"
    assert corrected["reviewed_at"] is None
    assert len(corrected["history"]) == 1
    assert corrected["history"][0]["change_type"] == "corrected"


def test_graph_prevents_cross_project_edges_and_filters_project_views(memory):
    atlas_person = add_record(memory, "Atlas owner", "Atlas")
    borealis_person = add_record(memory, "Borealis owner", "Borealis")
    with pytest.raises(KnowledgeGraphError, match="different projects"):
        memory.knowledge_graph.add(
            from_type="record", from_id=atlas_person,
            to_type="record", to_id=borealis_person, relation="related_to",
        )

    atlas_task = add_task(memory, "Atlas rollout", "Atlas")
    borealis_task = add_task(memory, "Borealis rollout", "Borealis")
    atlas_edge, _ = memory.knowledge_graph.add(
        from_type="record", from_id=atlas_person,
        to_type="task", to_id=atlas_task, relation="works_on",
    )
    borealis_edge, _ = memory.knowledge_graph.add(
        from_type="record", from_id=borealis_person,
        to_type="task", to_id=borealis_task, relation="works_on",
    )
    assert [item["edge_id"] for item in memory.knowledge_graph.list_edges(project="Atlas")] == [atlas_edge]
    assert [item["edge_id"] for item in memory.knowledge_graph.list_edges(project="Borealis")] == [borealis_edge]
    assert len(memory.knowledge_graph.list_edges()) == 2


def test_graph_refuses_private_tasks_unknown_projects_and_invalid_confidence(memory):
    person = add_record(memory, "Casey", "Atlas")
    private_task = add_task(memory, "Private task", "Atlas", private=True)
    with pytest.raises(GraphNodeNotFoundError):
        memory.knowledge_graph.add(
            from_type="record", from_id=person,
            to_type="task", to_id=private_task, relation="works_on",
        )
    public_task = add_task(memory, "Visible task", "Atlas")
    with pytest.raises(KnowledgeGraphError, match="registered project"):
        memory.knowledge_graph.add(
            from_type="record", from_id=person,
            to_type="task", to_id=public_task, relation="works_on",
            project="Unregistered",
        )
    for invalid in (-0.1, 1.1, float("nan"), float("inf"), True):
        with pytest.raises(KnowledgeGraphError, match="confidence"):
            memory.knowledge_graph.add(
                from_type="record", from_id=person,
                to_type="task", to_id=public_task, relation="works_on",
                confidence=invalid,
            )


def test_task_and_record_deletion_remove_graph_edges(memory):
    person = add_record(memory, "Jamie", "Atlas")
    task = add_task(memory, "Publish the design", "Atlas")
    edge_id, _ = memory.knowledge_graph.add(
        from_type="record", from_id=person, to_type="task", to_id=task,
        relation="works_on",
    )
    assert memory.knowledge_graph.get(edge_id)
    assert memory.personal.delete(task)
    assert memory.knowledge_graph.list_edges() == []

    task_two = add_task(memory, "Review the design", "Atlas")
    edge_two, _ = memory.knowledge_graph.add(
        from_type="record", from_id=person, to_type="task", to_id=task_two,
        relation="works_on",
    )
    assert memory.structured.delete(person)
    assert memory.knowledge_graph.list_edges() == []
    assert memory.knowledge_graph.remove(edge_two) is False


def test_project_rename_migrates_graph_scope_and_project_nodes(memory):
    person = add_record(memory, "Riley", "Atlas")
    task = add_task(memory, "Prepare handover", "Atlas")
    atlas_key = memory.projects.get("Atlas")["project_key"]
    edge_id, _ = memory.knowledge_graph.add(
        from_type="record", from_id=person,
        to_type="project", to_id=atlas_key, relation="works_on",
    )
    task_edge, _ = memory.knowledge_graph.add(
        from_type="record", from_id=person,
        to_type="task", to_id=task, relation="involves",
    )

    memory.projects.update("Atlas", new_name="Atlas/Next")

    assert memory.knowledge_graph.get(edge_id)["to_node"]["title"] == "Atlas/Next"
    assert memory.knowledge_graph.get(task_edge)["project_key"] == "atlas/next"
    assert {item["edge_id"] for item in memory.knowledge_graph.list_edges(project="Atlas/Next")} == {
        edge_id, task_edge
    }


def test_project_delete_removes_project_node_edges_but_keeps_record_task_edges(memory):
    person = add_record(memory, "Jordan", "Atlas")
    task = add_task(memory, "Write the migration note", "Atlas")
    project_key = memory.projects.get("Atlas")["project_key"]
    project_edge, _ = memory.knowledge_graph.add(
        from_type="record", from_id=person,
        to_type="project", to_id=project_key, relation="part_of",
    )
    task_edge, _ = memory.knowledge_graph.add(
        from_type="record", from_id=person, to_type="task", to_id=task,
        relation="works_on",
    )
    assert memory.projects.delete("Atlas")
    assert memory.knowledge_graph.remove(project_edge) is False
    assert [item["edge_id"] for item in memory.knowledge_graph.list_edges()] == [task_edge]


def test_graph_api_supports_project_nodes_review_correction_and_delete(tmp_path):
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    memory.projects.create("Atlas")
    record = add_record(memory, "Alex", "Atlas")
    task = add_task(memory, "Prepare release", "Atlas")
    app = create_app(
        store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
        memory=memory,
    )
    client = TestClient(app)

    nodes = client.get("/api/memory/graph/nodes", params={"project": "Atlas"})
    assert nodes.status_code == 200
    assert {item["node_type"] for item in nodes.json()["items"]} == {"record", "task", "project"}

    created = client.post("/api/memory/graph/edges", json={
        "from_type": "record", "from_id": record,
        "to_type": "task", "to_id": task,
        "relation": "works_on", "provenance": "source_backed",
        "confidence": 0.9, "evidence": [{"type": "message", "ref": "turn-15"}],
    })
    assert created.status_code == 201, created.text
    edge_id = created.json()["edge"]["edge_id"]
    assert created.json()["edge"]["state"] == "inferred"

    project_graph = client.get("/api/memory/graph", params={"project": "Atlas"})
    assert project_graph.status_code == 200
    assert [edge["edge_id"] for edge in project_graph.json()["edges"]] == [edge_id]

    reviewed = client.post(
        f"/api/memory/graph/edges/{edge_id}/review", json={"decision": "confirm"}
    )
    assert reviewed.status_code == 200 and reviewed.json()["edge"]["state"] == "confirmed"

    corrected = client.patch(
        f"/api/memory/graph/edges/{edge_id}",
        json={"relation": "involves", "note": "Updated owner context."},
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["edge"]["relation"] == "involves"
    assert corrected.json()["edge"]["state"] == "inferred"
    assert client.get(
        "/api/memory/graph/nodes", params={"project": "Unregistered"}
    ).json()["items"] == []

    removed = client.delete(f"/api/memory/graph/edges/{edge_id}")
    assert removed.status_code == 200 and removed.json()["deleted"] is True
    assert client.get("/api/memory/graph", params={"project": "Atlas"}).json()["edges"] == []


def test_knowledge_graph_page_is_served(tmp_path):
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    client = TestClient(create_app(
        store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
        memory=memory,
    ))
    response = client.get("/knowledge-graph")
    assert response.status_code == 200
    assert "Personal Knowledge Graph" in response.text
