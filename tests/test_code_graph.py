from __future__ import annotations

import json
from pathlib import Path

from mia.codegraph.indexer import build_graph


def make_repo(tmp_path: Path) -> Path:
    (tmp_path / "src" / "mia").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "mia" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "mia" / "helpers.py").write_text(
        "def helper(value):\n    return value\n", encoding="utf-8"
    )
    (tmp_path / "src" / "mia" / "service.py").write_text(
        "from mia.helpers import helper\n\n"
        "class Service:\n"
        "    def run(self, value):\n"
        "        return helper(value)\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_service.py").write_text(
        "def test_run():\n    assert True\n", encoding="utf-8"
    )
    return tmp_path


def test_graph_is_deterministic_and_has_stable_ids(tmp_path):
    root = make_repo(tmp_path)
    first = build_graph(root)
    second = build_graph(root)
    assert first["source_digest"] == second["source_digest"]
    assert [(n["id"], n["key"]) for n in first["nodes"]] == [
        (n["id"], n["key"]) for n in second["nodes"]
    ]
    assert first["edges"] == second["edges"]


def test_graph_records_confirmed_import_and_declarations(tmp_path):
    graph = build_graph(make_repo(tmp_path))
    edges = graph["edges"]
    assert any(edge["type"] == "IMPORTS" and edge["confidence"] == "confirmed"
               for edge in edges)
    assert any(edge["type"] == "DECLARES" and edge["evidence"].startswith("ast:")
               for edge in edges)


def test_inferred_edges_are_never_labelled_confirmed(tmp_path):
    graph = build_graph(make_repo(tmp_path))
    inferred = [edge for edge in graph["edges"] if edge["confidence"] == "inferred"]
    assert all(edge["type"] in {"BELONGS_TO_LAYER", "EXTENDS", "TESTED_BY"}
               for edge in inferred)


def test_graph_write_is_atomic_and_machine_readable(tmp_path):
    root = make_repo(tmp_path)
    output = tmp_path / "docs" / "code-graph" / "graph.json"
    graph = build_graph(root, output=output)
    stored = json.loads(output.read_text(encoding="utf-8"))
    assert stored["source_digest"] == graph["source_digest"]
    assert not list(output.parent.glob(".graph-*.tmp"))


def test_parse_errors_are_reported_without_fabricating_symbols(tmp_path):
    root = make_repo(tmp_path)
    (root / "src" / "mia" / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    graph = build_graph(root)
    assert graph["summary"]["parse_errors"] == 1
    assert graph["index_errors"][0]["path"] == "src/mia/broken.py"

 
def test_import_cycles_are_reported_as_static_diagnostics(tmp_path):
    root = make_repo(tmp_path)
    (root / "src" / "mia" / "helpers.py").write_text(
        "from mia.service import Service\ndef helper(value):\n    return value\n",
        encoding="utf-8",
    )
    graph = build_graph(root)
    assert graph["diagnostics"]["circular_imports"]
    assert "not proof of dead code" in graph["diagnostics"]["note"]


def test_unreferenced_candidates_are_explicitly_conservative(tmp_path):
    graph = build_graph(make_repo(tmp_path))
    assert isinstance(graph["diagnostics"]["unreferenced_static_candidates"], list)
    assert "not proof of dead code" in graph["diagnostics"]["note"]


def test_generated_graph_history_does_not_change_source_digest(tmp_path):
    root = make_repo(tmp_path)
    history = root / "docs" / "code-graph" / "CHANGELOG.md"
    history.parent.mkdir(parents=True)
    history.write_text("# History\n", encoding="utf-8")
    first = build_graph(root)["source_digest"]
    history.write_text("# History\n\nA new generated record.\n", encoding="utf-8")
    second = build_graph(root)["source_digest"]
    assert first == second
