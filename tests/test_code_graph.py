from __future__ import annotations

import json
from pathlib import Path

from mia.codegraph.impact import analyze_graph_impact
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
        "def local_helper(value):\n    return value\n\n"
        "class Service:\n"
        "    def run(self, value):\n"
        "        local_helper(value)\n"
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
    assert any(edge["type"] == "CALLS" and edge["confidence"] == "confirmed"
               and edge["evidence"].startswith("ast-call:") for edge in edges)


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
    history = root / "data" / "code-graph" / "history.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text('{"source_digest":"first"}\n', encoding="utf-8")
    first = build_graph(root)["source_digest"]
    history.write_text('{"source_digest":"first"}\n{"source_digest":"second"}\n', encoding="utf-8")
    second = build_graph(root)["source_digest"]
    assert first == second


def test_config_files_are_indexed_but_secret_material_is_excluded(tmp_path):
    root = make_repo(tmp_path)
    config = root / "config" / "settings.yaml"
    config.parent.mkdir()
    config.write_text("service: mia\n", encoding="utf-8")
    (root / ".env.example").write_text("API_KEY=replace-me\n", encoding="utf-8")
    (root / ".env.local").write_text("API_KEY=real-secret\n", encoding="utf-8")
    secrets = root / "secrets"
    secrets.mkdir()
    (secrets / "credentials.json").write_text('{"token":"real-secret"}', encoding="utf-8")
    private_keys = root / "private"
    private_keys.mkdir()
    (private_keys / "signing.pem").write_text("private-key-material", encoding="utf-8")
    graph = build_graph(root)
    paths = {node.get("path") for node in graph["nodes"] if node["kind"] == "file"}
    assert "config/settings.yaml" in paths
    assert ".env.example" in paths
    assert ".env.local" not in paths
    assert "secrets/credentials.json" not in paths
    assert "private/signing.pem" not in paths

    # Runtime user content is not source documentation and must stay out of the graph.
    for directory in ("data", "workspace", "backups", "exports", "recordings", "transcripts"):
        runtime = root / directory
        runtime.mkdir()
        (runtime / "private-note.json").write_text('{"owner":"private"}', encoding="utf-8")
    refreshed = build_graph(root)
    refreshed_paths = {
        node.get("path") for node in refreshed["nodes"] if node["kind"] == "file"
    }
    assert not any(
        path and path.split("/", 1)[0] in {
            "data", "workspace", "backups", "exports", "recordings", "transcripts"
        }
        for path in refreshed_paths
    )


def test_literal_config_reads_and_writes_are_source_grounded(tmp_path):
    root = make_repo(tmp_path)
    config = root / "config"
    config.mkdir()
    (config / "settings.yaml").write_text("mode: local\n", encoding="utf-8")
    (config / "output.json").write_text("{}", encoding="utf-8")
    (root / "src" / "mia" / "config_reader.py").write_text(
        "from pathlib import Path\n"
        "def load_config():\n"
        "    return Path('config/settings.yaml').read_text()\n"
        "def save_config():\n"
        "    with open('config/output.json', 'w') as handle:\n"
        "        handle.write('{}')\n",
        encoding="utf-8",
    )
    graph = build_graph(root)
    source = next(n["id"] for n in graph["nodes"]
                  if n["kind"] == "file" and n["path"] == "src/mia/config_reader.py")
    targets = {n["id"]: n.get("path") for n in graph["nodes"] if n["kind"] == "file"}
    edges = [e for e in graph["edges"] if e["source"] == source]
    assert any(e["type"] == "READS_FROM" and targets[e["target"]] == "config/settings.yaml"
               and e["confidence"] == "confirmed" for e in edges)
    assert any(e["type"] == "WRITES_TO" and targets[e["target"]] == "config/output.json"
               and e["confidence"] == "confirmed" for e in edges)


def test_api_route_is_linked_to_the_actual_qualified_function_node(tmp_path):
    root = make_repo(tmp_path)
    (root / "src" / "mia" / "routes.py").write_text(
        "class ItemRoutes:\n"
        "    @router.get(\"/items\")\n"
        "    def list_items(self):\n"
        "        return []\n",
        encoding="utf-8",
    )
    graph = build_graph(root)
    function = next(
        node for node in graph["nodes"]
        if node["kind"] == "function" and node.get("qualified_name") == "ItemRoutes.list_items"
    )
    endpoint = next(node for node in graph["nodes"] if node["kind"] == "api_endpoint")
    assert any(
        edge["type"] == "EXPOSES_API"
        and edge["source"] == function["id"]
        and edge["target"] == endpoint["id"]
        for edge in graph["edges"]
    )



def test_impact_analysis_finds_import_dependents_and_related_tests(tmp_path):
    graph = build_graph(make_repo(tmp_path))
    impact = analyze_graph_impact(graph, ["src/mia/helpers.py", "docs/notes.md"])

    helper = next(item for item in impact["items"] if item["path"] == "src/mia/helpers.py")
    assert helper["indexed"] is True
    assert helper["direct_dependents"] == ["src/mia/service.py"]
    assert helper["related_tests"] == ["tests/test_service.py"]

    unknown = next(item for item in impact["items"] if item["path"] == "docs/notes.md")
    assert unknown["indexed"] is False
    assert impact["dependent_files"] == ["src/mia/service.py"]


def test_diff_snapshots_reports_added_modified_removed_and_head_change():
    from mia.codegraph.indexer import diff_snapshots

    before = (("a.py", "1"), ("b.py", "2"), ("c.py", "3"), ("__git_head__:aaa", ""))
    after = (("a.py", "1"), ("b.py", "changed"), ("d.py", "4"), ("__git_head__:bbb", ""))
    result = diff_snapshots(before, after)
    assert result == {
        "added": ["d.py"], "removed": ["c.py"], "modified": ["b.py"], "head_changed": True,
    }
    assert diff_snapshots(before, before)["head_changed"] is False


def test_write_graph_is_atomic_and_keeps_existing_file_when_unchanged(tmp_path):
    from mia.codegraph.indexer import build_graph, write_graph

    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    output = tmp_path / "out" / "graph.json"  # outside the indexed tree
    graph = build_graph(repo)
    assert write_graph(graph, output)["source_digest"] == graph["source_digest"]
    first_mtime = output.stat().st_mtime_ns
    again = build_graph(repo)
    assert write_graph(again, output)["generated_at"] == graph["generated_at"]
    assert output.stat().st_mtime_ns == first_mtime
    assert not list(output.parent.glob(".graph-*.tmp"))


def test_same_file_calls_resolve_after_single_walk_optimisation(tmp_path):
    from mia.codegraph.indexer import build_graph

    (tmp_path / "svc.py").write_text(
        "def helper():\n    return 1\n\n\ndef caller():\n    return helper()\n", encoding="utf-8")
    graph = build_graph(tmp_path)
    labels = {n["id"]: n["label"] for n in graph["nodes"]}
    calls = [(labels[e["source"]], labels[e["target"]]) for e in graph["edges"] if e["type"] == "CALLS"]
    assert ("caller", "helper") in calls
    assert all(e["confidence"] == "confirmed" for e in graph["edges"] if e["type"] == "CALLS")


def _norm(graph):
    graph = dict(graph)
    graph.pop("generated_at")
    return json.dumps(graph, sort_keys=True)


def _write(repo, rel, text):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_incremental_build_equals_clean_rebuild_across_edit_sequences(tmp_path):
    from mia.codegraph.indexer import FactCache, build_graph

    repo = tmp_path / "repo"
    _write(repo, "src/mia/base.py", "class Base:\n    pass\n\n\ndef helper():\n    return 1\n")
    _write(repo, "src/mia/app.py",
           "from mia.base import Base\nfrom fastapi import FastAPI\napp = FastAPI()\n\n\n"
           "class Child(Base):\n    pass\n\n\ndef local():\n    return 2\n\n\n"
           "@app.get('/ping')\ndef ping():\n    return local()\n")
    _write(repo, "tests/test_app.py", "def test_local():\n    pass\n")
    _write(repo, "config/settings.toml", "a = 1\n")
    cache = FactCache()

    def check(step):
        incremental = build_graph(repo, cache=cache)
        clean = build_graph(repo)  # fresh cache every time
        assert _norm(incremental) == _norm(clean), step
        return incremental

    check("initial")
    assert cache.extracted == 3 and cache.reused == 0
    check("no change")
    assert cache.extracted == 0 and cache.reused == 3

    _write(repo, "src/mia/base.py", "class Base:\n    pass\n\n\nclass Other:\n    pass\n")  # modify
    check("modify")
    assert cache.extracted == 1 and cache.reused == 2

    _write(repo, "src/mia/extra.py", "from mia.app import Child\nX = open('config/settings.toml')\n")
    graph = check("add")  # new module creates cross-file IMPORTS and READS_FROM edges
    assert any(e["type"] == "IMPORTS" for e in graph["edges"] if "extra" in str(e["evidence"]))

    (repo / "src/mia/base.py").rename(repo / "src/mia/core.py")  # rename breaks app's import
    check("rename")  # the contract is equality with a clean rebuild, including the broken import

    (repo / "src/mia/extra.py").unlink()
    check("remove")
    assert "src/mia/extra.py" not in cache.entries  # stale entries are pruned

    _write(repo, "src/mia/broken.py", "def (:\n")
    graph = check("syntax error")
    assert any(e["path"] == "src/mia/broken.py" for e in graph["index_errors"])
    _write(repo, "src/mia/broken.py", "def fixed():\n    return 1\n")
    graph = check("syntax error fixed")
    assert not graph["index_errors"]


def test_fact_cache_persists_and_is_invalidated_by_salt(tmp_path):
    from mia.codegraph import indexer
    from mia.codegraph.indexer import FactCache, build_graph

    repo = tmp_path / "repo"
    _write(repo, "m.py", "def f():\n    return 1\n")
    cache = FactCache()
    build_graph(repo, cache=cache)
    stored = tmp_path / "cache" / "facts.json"
    cache.save(stored)
    assert not list(stored.parent.glob(".facts-*.tmp"))

    loaded = FactCache()
    loaded.load(stored)
    build_graph(repo, cache=loaded)
    assert loaded.reused == 1 and loaded.extracted == 0

    original = indexer._FACTS_SALT
    try:
        indexer._FACTS_SALT = "different-extractor"
        stale = FactCache()
        stale.load(stored)
        assert stale.entries == {}
    finally:
        indexer._FACTS_SALT = original
    corrupt = tmp_path / "bad.json"
    corrupt.write_text("{not json", encoding="utf-8")
    fresh = FactCache()
    fresh.load(corrupt)
    assert fresh.entries == {}
