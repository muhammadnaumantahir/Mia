"""Git-backed impact analysis and graph provenance, against real temporary repositories."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mia.codegraph.impact import analyze_impact, changed_paths_since
from mia.codegraph.indexer import build_graph

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git is required",
)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit_all(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, ".gitignore", "ignored.log\n")
    write(root, "src/mia/core.py", "def compute():\n    return 1\n")
    write(root, "src/mia/user.py", "from mia.core import compute\n\n\ndef run():\n    return compute()\n")
    write(root, "tests/test_core.py", "def test_compute():\n    assert True\n")
    commit_all(root, "base")
    write(root, "src/mia/user.py", "from mia.core import compute\n\n\ndef run():\n    return compute() + 1\n")
    commit_all(root, "head change")
    return root


def test_committed_only_by_default_and_working_tree_changes_when_requested(repo):
    write(repo, "src/mia/core.py", "def compute():\n    return 2\n")  # unstaged edit
    write(repo, "src/mia/new_untracked.py", "X = 1\n")  # untracked
    write(repo, "src/mia/staged.py", "Y = 1\n")
    git(repo, "add", "src/mia/staged.py")  # staged new file
    write(repo, "ignored.log", "noise\n")  # ignored, must not appear

    assert changed_paths_since(repo, "HEAD~1") == ["src/mia/user.py"]
    assert changed_paths_since(repo, "HEAD~1", include_working_tree=True) == [
        "src/mia/core.py", "src/mia/new_untracked.py", "src/mia/staged.py", "src/mia/user.py"]


def test_renames_report_old_and_new_paths(repo):
    git(repo, "mv", "src/mia/core.py", "src/mia/engine.py")
    commit_all(repo, "rename")
    paths = changed_paths_since(repo, "HEAD~1")
    assert "src/mia/core.py" in paths and "src/mia/engine.py" in paths


def test_unusual_filenames_are_not_quoted_or_dropped(repo):
    write(repo, "src/mia/naïve file.py", "Z = 1\n")
    assert "src/mia/naïve file.py" in changed_paths_since(repo, "HEAD~1", include_working_tree=True)


def test_analyze_impact_sees_uncommitted_edit_and_reports_scope(repo):
    write(repo, "src/mia/core.py", "def compute():\n    return 99\n")  # uncommitted
    committed_only = analyze_impact(repo, "HEAD", include_working_tree=False)
    assert committed_only["changed_files"] == 0 and committed_only["includes_uncommitted"] is False

    impact = analyze_impact(repo, "HEAD")
    assert impact["includes_uncommitted"] is True
    core = next(i for i in impact["items"] if i["path"] == "src/mia/core.py")
    assert core["indexed"] is True
    assert core["direct_dependents"] == ["src/mia/user.py"]
    assert core["related_tests"] == ["tests/test_core.py"]
    assert impact["related_tests"] == ["tests/test_core.py"]


def test_bad_refs_still_fail_closed(repo):
    for bad in ("--output=x", "", "does-not-exist", "a\nb"):
        with pytest.raises(ValueError):
            changed_paths_since(repo, bad, include_working_tree=True)


def test_graph_records_whether_the_tree_matches_its_commit(repo, tmp_path):
    clean = build_graph(repo)
    assert clean["commit"] == git(repo, "rev-parse", "HEAD")
    assert clean["tree"] == {"dirty": False, "dirty_paths": 0}

    write(repo, "src/mia/core.py", "def compute():\n    return 3\n")
    write(repo, "src/mia/another.py", "Q = 1\n")
    dirty = build_graph(repo)
    assert dirty["commit"] == clean["commit"]  # HEAD is unchanged, so `tree` is what discloses it
    assert dirty["tree"] == {"dirty": True, "dirty_paths": 2}
    assert dirty["source_digest"] != clean["source_digest"]

    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    write(outside, "m.py", "A = 1\n")
    assert build_graph(outside)["tree"] == {"dirty": None, "dirty_paths": None}


def test_deleted_file_still_reports_remaining_importers_and_tests(repo):
    git(repo, "rm", "-q", "src/mia/core.py")  # staged deletion; user.py still imports it
    impact = analyze_impact(repo, "HEAD")
    item = next(i for i in impact["items"] if i["path"] == "src/mia/core.py")
    assert item["indexed"] is False and item["removed"] is True
    assert item["direct_dependents"] == ["src/mia/user.py"]
    assert item["related_tests"] == ["tests/test_core.py"]
    assert impact["dependent_files"] == ["src/mia/user.py"]
    assert impact["base_graph_commit"] == git(repo, "rev-parse", "HEAD")


def test_renamed_file_reports_importers_of_the_old_path(repo):
    git(repo, "mv", "src/mia/core.py", "src/mia/engine.py")
    commit_all(repo, "rename")
    impact = analyze_impact(repo, "HEAD~1")
    old = next(i for i in impact["items"] if i["path"] == "src/mia/core.py")
    new = next(i for i in impact["items"] if i["path"] == "src/mia/engine.py")
    assert old["removed"] is True and old["direct_dependents"] == ["src/mia/user.py"]
    assert new["indexed"] is True and "removed" not in new


def test_removed_path_not_in_base_graph_stays_unindexed_and_unflagged(repo):
    write(repo, "notes/todo.md", "x\n")
    commit_all(repo, "add doc")
    git(repo, "rm", "-q", "notes/todo.md")
    item = next(i for i in analyze_impact(repo, "HEAD~1")["items"] if i["path"] == "notes/todo.md")
    assert item["indexed"] is False and item["direct_dependents"] == []


def test_base_graph_is_not_built_when_every_changed_path_is_indexed(repo, monkeypatch):
    from mia.codegraph import impact as impact_module

    def boom(*args, **kwargs):
        raise AssertionError("base graph must not be built")

    monkeypatch.setattr(impact_module, "_graph_at_commit", boom)
    write(repo, "src/mia/core.py", "def compute():\n    return 5\n")
    assert analyze_impact(repo, "HEAD")["changed_files"] == 1


def test_compute_source_digest_matches_build_graph_and_freshness_reasons(repo, tmp_path):
    import json

    from mia.codegraph.indexer import compute_source_digest, graph_freshness, write_graph

    graph = build_graph(repo)
    assert compute_source_digest(repo) == graph["source_digest"]  # guards against drift
    stored = tmp_path / "graph.json"
    assert graph_freshness(repo, stored)["state"] == "missing"
    write_graph(graph, stored)
    assert graph_freshness(repo, stored)["state"] == "current"

    write(repo, "src/mia/core.py", "def compute():\n    return 7\n")
    stale = graph_freshness(repo, stored)
    assert stale["state"] == "stale" and stale["reasons"] == ["source_changed"]

    git(repo, "checkout", "-q", "--", "src/mia/core.py")
    git(repo, "commit", "-q", "--allow-empty", "-m", "empty")
    moved = graph_freshness(repo, stored)
    assert moved["reasons"] == ["commit_changed"] and moved["stored_tree"] == {
        "dirty": False, "dirty_paths": 0}

    stored.write_text("{broken", encoding="utf-8")
    assert graph_freshness(repo, stored)["state"] == "unreadable"
    stored.write_text(json.dumps({"commit": "x"}), encoding="utf-8")
    assert graph_freshness(repo, stored)["state"] == "unreadable"
