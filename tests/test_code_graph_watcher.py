from pathlib import Path

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


def test_watcher_rejects_unsafe_polling_intervals(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        CodeGraphWatcher(tmp_path, tmp_path / "graph.json", interval_seconds=0.1)
