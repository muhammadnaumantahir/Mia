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
    history = (tmp_path / "docs" / "code-graph" / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "Source digest" in history
    assert history.count("## ") >= 2


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
