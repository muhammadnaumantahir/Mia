import json
import os

from mia.codegraph.watcher import CodeGraphWatcher


def test_watcher_rebuilds_graph_after_source_changes(tmp_path):
    source = tmp_path / "src" / "mia" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("def first():\n    return 1\n", encoding="utf-8")
    output = tmp_path / "docs" / "code-graph" / "graph.json"
    watcher = CodeGraphWatcher(tmp_path, output, interval_seconds=1.0)

    assert watcher.check_once(force=True) is True
    first = output.read_text(encoding="utf-8")
    assert "first" in first
    assert watcher.check_once() is False

    source.write_text("def second():\n    return 200\n", encoding="utf-8")
    assert watcher.check_once() is True
    second = output.read_text(encoding="utf-8")
    assert "second" in second
    assert first != second
    history_path = tmp_path / "data" / "code-graph" / "history.jsonl"
    history = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines()]
    assert len(history) >= 2
    assert all(item["source_digest"] for item in history)
    assert not (tmp_path / "docs" / "code-graph" / "CHANGELOG.md").exists()


def test_watcher_rejects_unsafe_polling_intervals(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        CodeGraphWatcher(tmp_path, tmp_path / "graph.json", interval_seconds=0.1)



def test_watcher_detects_content_change_when_size_and_timestamp_are_unchanged(tmp_path):
    source = tmp_path / "src" / "mia" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    output = tmp_path / "docs" / "code-graph" / "graph.json"
    watcher = CodeGraphWatcher(tmp_path, output, interval_seconds=1.0)
    assert watcher.check_once(force=True) is True
    first_graph = json.loads(output.read_text(encoding="utf-8"))

    original = source.stat()
    source.write_text("def value():\n    return 2\n", encoding="utf-8")
    os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))

    assert source.stat().st_size == original.st_size
    assert source.stat().st_mtime_ns == original.st_mtime_ns
    assert watcher.check_once() is True
    second_graph = json.loads(output.read_text(encoding="utf-8"))
    assert second_graph["source_digest"] != first_graph["source_digest"]


def test_watcher_history_records_changed_paths_and_rebuild_mode(tmp_path):
    source = tmp_path / "src" / "mia" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("def one():\n    return 1\n", encoding="utf-8")
    output = tmp_path / "docs" / "code-graph" / "graph.json"
    watcher = CodeGraphWatcher(tmp_path, output, interval_seconds=1.0)
    watcher.start()
    watcher.stop()
    watcher._snapshot = __import__("mia.codegraph.indexer", fromlist=["x"]).source_snapshot(tmp_path)

    source.write_text("def two():\n    return 2\n", encoding="utf-8")
    (source.parent / "extra.py").write_text("X = 1\n", encoding="utf-8")
    assert watcher.check_once() is True

    assert watcher.last_changes["modified"] == ["src/mia/app.py"]
    assert watcher.last_changes["added"] == ["src/mia/extra.py"]
    assert watcher.last_rebuild_seconds is not None
    history_path = tmp_path / "data" / "code-graph" / "history.jsonl"
    last = json.loads(history_path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["rebuild"] == "full"
    assert last["changes"]["modified"] == ["src/mia/app.py"]
    # History stores paths only, never file contents.
    assert "return 2" not in history_path.read_text(encoding="utf-8")
