"""Deterministic, source-grounded code graph for Mia.

The indexer is read-only with respect to application source. It writes only the
requested graph artifacts and never executes indexed code.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

EXCLUDED = {".git", ".venv", "venv", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".ssh", ".private", ".cache", "cache", "private", "certs", "certificates", "dist", "build", "secrets", "credentials", "private_keys", "data", "workspace", "backups", "exports", "logs", "tmp", "temp", "runtime", "uploads", "downloads", "recordings", "transcripts", "sessions", "artifacts"}
SENSITIVE_FILES = {".env", ".env.local", ".env.production", "secrets.json", "credentials.json",
                   "service-account.json", "id_rsa", "id_ed25519", "authorized_keys"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore", ".jks"}
SOURCE_SUFFIXES = {
    ".py", ".html", ".js", ".ts", ".tsx", ".jsx", ".css", ".toml", ".sql", ".md",
    ".json", ".yaml", ".yml", ".ini", ".cfg", ".properties", ".xml", ".sh", ".ps1",
    ".bat", ".txt",
}
LAYER_HINTS = {
    "SPS-01": ("config", "contracts"),
    "SPS-02": ("permission", "governance", "approval", "policy", "budget"),
    "SPS-03": ("brain", "agent", "planner", "intent", "reason"),
    "SPS-04": ("knowledge", "research", "document", "retrieval"),
    "SPS-05": ("memory", "conversation", "experience", "life"),
    "SPS-06": ("evaluation", "benchmark", "reflection", "observatory"),
    "SPS-07": ("routing", "adaptation", "workflow", "selection"),
    "SPS-08": ("evolution", "capabilit", "plugin"),
    "SPS-09": ("test", "verify", "validation", "sandbox"),
    "SPS-10": ("computer", "execution", "runtime", "voice"),
}
LAYER_NAMES = {
    "SPS-01": "Software DNA Core", "SPS-02": "Governance Core",
    "SPS-03": "Cognitive Core", "SPS-04": "Knowledge Core",
    "SPS-05": "Experience Core", "SPS-06": "Meta-Learning Core",
    "SPS-07": "Adaptation Core", "SPS-08": "Evolution Core",
    "SPS-09": "Verification & Validation Core", "SPS-10": "Execution Core",
}


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                                timeout=5, check=False)
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _tree_state(root: Path) -> dict[str, Any]:
    """Report whether the indexed working tree matches HEAD (untracked, non-ignored files count)."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=root,
            capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return {"dirty": None, "dirty_paths": None}
    if result.returncode != 0:
        return {"dirty": None, "dirty_paths": None}
    entries = [e for e in result.stdout.split(b"\0") if e]
    return {"dirty": bool(entries), "dirty_paths": len(entries)}


def _node_id(kind: str, key: str) -> str:
    return f"{kind}:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def _layer_for(path: str) -> tuple[str, str] | None:
    lowered = path.lower()
    for layer, hints in LAYER_HINTS.items():
        if any(hint in lowered for hint in hints):
            return layer, LAYER_NAMES[layer]
    return None


def _module_path(path: Path, root: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _python_files(root: Path) -> list[Path]:
    found = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED)
        for name in sorted(files):
            path = Path(base) / name
            relative = path.relative_to(root).as_posix()
            # Generated graph history must not become its own indexing input.
            if relative in {"docs/code-graph/CHANGELOG.md", "docs/code-graph/graph.json"}:
                continue
            if name.lower() in SENSITIVE_FILES or path.suffix.lower() in SENSITIVE_SUFFIXES or (
                name.lower().startswith(".env.") and name.lower() != ".env.example"
            ):
                continue
            if path.suffix.lower() in SOURCE_SUFFIXES or name in {"Dockerfile", "Makefile", ".env.example"}:
                found.append(path)
    return sorted(found, key=lambda p: p.relative_to(root).as_posix())


def source_snapshot(root: str | Path) -> tuple[tuple[str, str], ...]:
    """Content-based snapshot used by the watcher to detect every source change."""
    root = Path(root).resolve()
    snapshot = []
    for path in _python_files(root):
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        snapshot.append((path.relative_to(root).as_posix(), digest))
    # A commit can change without file contents changing; include HEAD in the key.
    snapshot.append((f"__git_head__:{_git(root, 'rev-parse', 'HEAD')}", ""))
    return tuple(snapshot)


def _fingerprint(pairs: Any) -> Any:
    """Digest over (relative path, content sha256) pairs in indexing order."""
    fingerprint = hashlib.sha256()
    for rel, digest in pairs:
        fingerprint.update(rel.encode("utf-8") + b"\0" + bytes.fromhex(digest))
    return fingerprint


def compute_source_digest(root: str | Path) -> str:
    """The ``source_digest`` a fresh ``build_graph`` would produce, without parsing anything."""
    root = Path(root).resolve()
    pairs = []
    for path in _python_files(root):
        try:
            pairs.append((path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
        except OSError:
            continue
    return _fingerprint(pairs).hexdigest()


def graph_freshness(root: str | Path, graph_path: str | Path) -> dict[str, Any]:
    """Compare a stored graph file with the checkout it claims to describe.

    ``state`` is ``missing``, ``unreadable``, ``current`` or ``stale``; ``reasons`` lists
    ``source_changed`` (indexed file content differs) and/or ``commit_changed`` (HEAD moved).
    """
    root = Path(root).resolve()
    graph_path = Path(graph_path)
    head = _git(root, "rev-parse", "HEAD")
    if not graph_path.is_file():
        return {"state": "missing", "reasons": ["no_stored_graph"], "head": head}
    try:
        stored = json.loads(graph_path.read_text(encoding="utf-8"))
        stored_digest = stored["source_digest"]
        stored_commit = stored.get("commit")
        if not isinstance(stored_digest, str) or not isinstance(stored, dict):
            raise ValueError("invalid graph file")
    except (OSError, ValueError, KeyError, TypeError):
        return {"state": "unreadable", "reasons": ["stored_graph_unreadable"], "head": head}
    reasons = []
    if stored_digest != compute_source_digest(root):
        reasons.append("source_changed")
    if stored_commit != head:
        reasons.append("commit_changed")
    return {
        "state": "stale" if reasons else "current",
        "reasons": reasons,
        "head": head,
        "stored_commit": stored_commit,
        "stored_tree": stored.get("tree"),
        "stored_generated_at": stored.get("generated_at"),
    }


def diff_snapshots(
    previous: tuple[tuple[str, str], ...], current: tuple[tuple[str, str], ...]
) -> dict[str, Any]:
    """Compare two ``source_snapshot`` results by content digest.

    This is change *detection* only: it names the files whose content differs so
    tooling and history can report exactly what triggered a rebuild. It does not
    prove which graph edges are affected (cross-file edges need the full pass).
    """
    sentinel = "__git_head__:"
    before = {path: digest for path, digest in previous if not path.startswith(sentinel)}
    after = {path: digest for path, digest in current if not path.startswith(sentinel)}
    head_before = next((p for p, _ in previous if p.startswith(sentinel)), None)
    head_after = next((p for p, _ in current if p.startswith(sentinel)), None)
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "modified": sorted(p for p in set(before) & set(after) if before[p] != after[p]),
        "head_changed": head_before != head_after,
    }


_ROUTE_METHODS = {"get", "post", "put", "patch", "delete"}
_FACTS_SALT = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


def _module_name(rel: str) -> str:
    parts = list(Path(rel).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _extract_facts(rel: str, tree: ast.Module) -> dict[str, Any]:
    """Per-file, JSON-serialisable facts. Depends only on the file's own content and path."""
    decls: list[dict[str, Any]] = []
    keymap: dict[ast.AST, str] = {}

    def visit_scope(body: list[ast.stmt], parent_key: str) -> None:
        for item in body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                is_class = isinstance(item, ast.ClassDef)
                key = f"{rel}:{parent_key + '.' if parent_key else ''}{item.name}"
                decls.append({
                    "kind": "class" if is_class else "function", "key": key, "name": item.name,
                    "qualified_name": parent_key + "." + item.name if parent_key else item.name,
                    "line": item.lineno, "end_line": getattr(item, "end_lineno", item.lineno),
                    "bases": [b.id for b in item.bases if isinstance(b, ast.Name)] if is_class else [],
                })
                keymap[item] = key
                visit_scope(item.body, key.split(":", 1)[1])
            elif isinstance(item, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With,
                                    ast.AsyncWith, ast.Try)):
                for field in ("body", "orelse", "finalbody"):
                    child = getattr(item, field, None)
                    if isinstance(child, list):
                        visit_scope(child, parent_key)

    visit_scope(tree.body, "")
    imports: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    accesses: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                module = node.module or ""
                names = [f"{module}.{alias.name}".strip(".") for alias in node.names]
                if node.level:
                    package = _module_name(rel).split(".")[:-node.level]
                    module = ".".join(package + ([module] if module else []))
                    names = [f"{module}.{alias.name}".strip(".") for alias in node.names]
            imports.append({"line": node.lineno, "names": names})
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                        and decorator.func.attr in _ROUTE_METHODS):
                    route = next((a.value for a in decorator.args
                                  if isinstance(a, ast.Constant) and isinstance(a.value, str)), None)
                    if route:
                        routes.append({"method": decorator.func.attr.upper(), "route": route,
                                       "line": decorator.lineno, "function": keymap.get(node)})
            collected: list[ast.Call] = []

            class CallVisitor(ast.NodeVisitor):
                def visit_FunctionDef(self, inner: ast.FunctionDef) -> None:
                    if inner is node:
                        self.generic_visit(inner)

                def visit_AsyncFunctionDef(self, inner: ast.AsyncFunctionDef) -> None:
                    if inner is node:
                        self.generic_visit(inner)

                def visit_Call(self, call: ast.Call) -> None:
                    collected.append(call)
                    self.generic_visit(call)

            CallVisitor().visit(node)
            calls.append({"line": node.lineno,
                          "calls": [[c.func.id, c.lineno] for c in collected
                                    if isinstance(c.func, ast.Name)]})
        raw_path: str | None = None
        relation: str | None = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "Path" and node.args and isinstance(node.args[0], ast.Constant):
                if isinstance(node.args[0].value, str):
                    raw_path = node.args[0].value
                    relation = "USES"
            elif node.func.id == "open" and node.args and isinstance(node.args[0], ast.Constant):
                if isinstance(node.args[0].value, str):
                    raw_path = node.args[0].value
                    mode = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else "r"
                    relation = "WRITES_TO" if isinstance(mode, str) and any(f in mode for f in ("w", "a", "x", "+")) else "READS_FROM"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"read_text", "read_bytes", "write_text", "write_bytes"}:
                receiver = node.func.value
                if (isinstance(receiver, ast.Call) and isinstance(receiver.func, ast.Name)
                        and receiver.func.id == "Path" and receiver.args
                        and isinstance(receiver.args[0], ast.Constant)
                        and isinstance(receiver.args[0].value, str)):
                    raw_path = receiver.args[0].value
                    relation = "WRITES_TO" if node.func.attr.startswith("write") else "READS_FROM"
        if raw_path and relation:
            accesses.append({"raw": raw_path, "relation": relation, "line": node.lineno})
    return {"decls": decls, "imports": imports, "routes": routes,
            "accesses": accesses, "calls": calls}


class FactCache:
    """Per-file extraction cache keyed by content digest (optionally persisted as JSON).

    Entries hold only facts derived from a single file. All cross-file relationships are
    recomputed by the global resolution pass on every build, so a stale cache entry can
    never leave a stale cross-file edge. The cache is salted with a hash of this module's
    source, so changing the extractor invalidates every entry.
    """

    def __init__(self) -> None:
        self.entries: dict[str, tuple[str, dict[str, Any]]] = {}
        self.reused = 0
        self.extracted = 0

    def load(self, path: str | Path) -> None:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if data.get("salt") == _FACTS_SALT:
                self.entries = {k: (v[0], v[1]) for k, v in data["entries"].items()}
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
            self.entries = {}

    def save(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"salt": _FACTS_SALT, "entries": self.entries}, sort_keys=True)
        fd, temporary = tempfile.mkstemp(prefix=".facts-", suffix=".tmp", dir=destination.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def build_graph(root: str | Path, *, output: str | Path | None = None,
                cache: FactCache | None = None) -> dict[str, Any]:
    """Build a deterministic graph from repository source and documentation."""
    root = Path(root).resolve()
    files = _python_files(root)
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    modules = {_module_path(path, root): path for path in files if path.suffix == ".py"}
    symbol_index: dict[str, list[str]] = {}
    function_nodes_by_location: dict[tuple[str, int], list[str]] = {}
    errors: list[dict[str, str]] = []

    def add_node(kind: str, key: str, **attrs: Any) -> str:
        ident = _node_id(kind, key)
        nodes.setdefault(ident, {"id": ident, "kind": kind, "key": key, **attrs})
        return ident

    def add_edge(source: str, target: str, relation: str, confidence: str,
                 evidence: str, line: int | None = None) -> None:
        key = (source, target, relation, evidence)
        edges[key] = {"source": source, "target": target, "type": relation,
                      "confidence": confidence, "evidence": evidence, "line": line}

    root_id = add_node("repository", root.name, label=root.name, path=".")
    dir_ids: dict[str, str] = {}
    file_ids: dict[Path, str] = {}
    for path in files:
        rel = path.relative_to(root).as_posix()
        parent = path.parent.relative_to(root).as_posix()
        if parent != ".":
            current = ""
            for part in parent.split("/"):
                current = f"{current}/{part}".strip("/")
                if current not in dir_ids:
                    dir_ids[current] = add_node("directory", current, label=part, path=current)
                    parent_id = dir_ids.get(str(Path(current).parent).replace("\\", "/"), root_id)
                    add_edge(parent_id, dir_ids[current], "CONTAINS", "confirmed", f"filesystem:{current}")
        fid = add_node("file", rel, label=path.name, path=rel, language=path.suffix.lstrip("."))
        file_ids[path] = fid
        parent_id = dir_ids.get(parent, root_id)
        add_edge(parent_id, fid, "CONTAINS", "confirmed", f"filesystem:{rel}")
        layer = _layer_for(rel)
        if layer:
            lid = add_node("sps_layer", layer[0], label=f"{layer[0]} · {layer[1]}")
            add_edge(fid, lid, "BELONGS_TO_LAYER", "inferred", f"path heuristic: {rel}")

    cache = cache if cache is not None else FactCache()
    cache.reused = cache.extracted = 0
    digests: dict[Path, str] = {}
    facts_by_path: dict[Path, dict[str, Any]] = {}
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            data = path.read_bytes()
        except OSError as exc:
            errors.append({"path": rel, "error": f"{type(exc).__name__}: {str(exc)[:180]}"})
            continue
        digest = hashlib.sha256(data).hexdigest()
        digests[path] = digest
        if path.suffix != ".py":
            continue
        cached = cache.entries.get(rel)
        if cached is not None and cached[0] == digest:
            facts = cached[1]
            cache.reused += 1
        else:
            try:
                facts = _extract_facts(rel, ast.parse(data.decode("utf-8"), filename=rel))
            except (UnicodeError, SyntaxError) as exc:
                facts = {"error": f"{type(exc).__name__}: {str(exc)[:180]}"}
            cache.entries[rel] = (digest, facts)
            cache.extracted += 1
        if "error" in facts:
            errors.append({"path": rel, "error": facts["error"]})
            continue
        facts_by_path[path] = facts
    live = {p.relative_to(root).as_posix() for p in files}
    for stale in [k for k in cache.entries if k not in live]:
        del cache.entries[stale]

    def resolve_import(imported: str) -> Path | None:
        prefix = imported
        while prefix:
            target_path = modules.get(prefix)
            if target_path is not None:
                return target_path
            if "." in prefix and (prefix.startswith("mia.") or prefix.startswith("src.mia.")):
                suffix_matches = [c for m, c in modules.items() if m.endswith("." + prefix)]
                if len(suffix_matches) == 1:
                    return suffix_matches[0]
            if "." not in prefix:
                break
            prefix = prefix.rsplit(".", 1)[0]
        return None

    key_to_id: dict[str, str] = {}
    for path, facts in facts_by_path.items():
        rel = path.relative_to(root).as_posix()
        fid = file_ids[path]
        for decl in facts["decls"]:
            sid = add_node(decl["kind"], decl["key"], label=decl["name"], path=rel,
                           qualified_name=decl["qualified_name"],
                           line=decl["line"], end_line=decl["end_line"])
            symbol_index.setdefault(decl["name"], []).append(sid)
            key_to_id[decl["key"]] = sid
            add_edge(fid, sid, "DECLARES", "confirmed", f"ast:{rel}:{decl['line']}", decl["line"])
            for base in decl["bases"]:
                for candidate in symbol_index.get(base, []):
                    add_edge(sid, candidate, "EXTENDS", "inferred",
                             f"base expression {base}", decl["line"])
        for entry in facts["imports"]:
            for imported in entry["names"]:
                target_path = resolve_import(imported)
                if target_path is not None:
                    add_edge(fid, file_ids[target_path], "IMPORTS", "confirmed",
                             f"ast-import:{rel}:{entry['line']}", entry["line"])
        for route in facts["routes"]:
            label = f"{route['method']} {route['route']}"
            route_id = add_node("api_endpoint", label, label=label, path=rel, line=route["line"])
            function_id = key_to_id.get(route["function"]) if route["function"] else None
            if function_id is None:
                continue
            add_edge(function_id, route_id, "EXPOSES_API", "confirmed",
                     f"fastapi-decorator:{rel}:{route['line']}", route["line"])

    # Resolve literal configuration/data-file access without guessing dynamic paths.
    for path, facts in facts_by_path.items():
        rel = path.relative_to(root).as_posix()
        for access in facts["accesses"]:
            raw_path = access["raw"]
            normalized = raw_path.replace("\\", "/")
            if normalized.startswith("/") or ":" in normalized:
                continue
            resolved = (root / normalized).resolve()
            try:
                target_rel = resolved.relative_to(root).as_posix()
            except ValueError:
                continue
            target_path = root / target_rel
            if target_path not in file_ids or target_path == path:
                continue
            add_edge(file_ids[path], file_ids[target_path], access["relation"], "confirmed",
                     f"ast-file-access:{rel}:{access['line']}:{raw_path}", access["line"])

    # Resolve direct calls only when the target is unique in the same file.
    # Cross-file calls via DI, aliases and dynamic imports are intentionally unresolved.
    for node in nodes.values():
        if node["kind"] == "function":
            function_nodes_by_location.setdefault((node["path"], node.get("line")), []).append(node["id"])
    for path, facts in facts_by_path.items():
        rel = path.relative_to(root).as_posix()
        for function in facts["calls"]:
            matches = function_nodes_by_location.get((rel, function["line"]), [])
            if len(matches) != 1:
                continue
            caller_id = matches[0]
            for name, lineno in function["calls"]:
                targets = [sid for sid in symbol_index.get(name, [])
                           if nodes.get(sid, {}).get("path") == rel and sid != caller_id]
                if len(targets) == 1:
                    add_edge(caller_id, targets[0], "CALLS", "confirmed",
                             f"ast-call:{rel}:{lineno}", lineno)

    # Tests are linked only by a conservative exact-name convention and marked inferred.
    declared = {n["id"]: n for n in nodes.values() if n["kind"] == "function"}
    test_functions = [n for n in declared.values() if n["path"].startswith("tests/") and n["label"].startswith("test_")]
    for test in test_functions:
        stem = test["label"][5:]
        candidates = [n for n in declared.values() if not n["path"].startswith("tests/")
                      and n["label"] == stem]
        for candidate in candidates[:5]:
            add_edge(candidate["id"], test["id"], "TESTED_BY", "inferred",
                     f"test-name heuristic: {test['path']}::{test['label']}")

    # Static diagnostics are conservative: these are candidates, not proof of dead code.
    meaningful = {"IMPORTS", "CALLS", "EXPOSES_API", "TESTED_BY", "USES",
                  "DEPENDS_ON", "PROTECTED_BY", "READS_FROM", "WRITES_TO",
                  "ROUTES_TO", "ACTIVATES", "TRIGGERS"}
    incident: dict[str, int] = {}
    for edge in edges.values():
        if edge["type"] in meaningful:
            incident[edge["source"]] = incident.get(edge["source"], 0) + 1
            incident[edge["target"]] = incident.get(edge["target"], 0) + 1
    unreferenced = [
        {"id": node["id"], "key": node["key"], "kind": node["kind"],
         "reason": "No matching static dependency/call/test/API edge was resolved."}
        for node in nodes.values()
        if node["kind"] in {"file", "function", "class", "api_endpoint"}
        and node["id"] not in incident
    ]

    adjacency: dict[str, list[str]] = {
        ident: [] for ident, node in nodes.items() if node["kind"] == "file"
    }
    for edge in edges.values():
        if edge["type"] == "IMPORTS" and edge["source"] in adjacency:
            adjacency[edge["source"]].append(edge["target"])
    next_index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    circular_imports: list[list[str]] = []

    def visit_import(node_id: str) -> None:
        nonlocal next_index
        indices[node_id] = lowlinks[node_id] = next_index
        next_index += 1
        stack.append(node_id)
        on_stack.add(node_id)
        for target in sorted(adjacency.get(node_id, [])):
            if target not in indices:
                visit_import(target)
                lowlinks[node_id] = min(lowlinks[node_id], lowlinks[target])
            elif target in on_stack:
                lowlinks[node_id] = min(lowlinks[node_id], indices[target])
        if lowlinks[node_id] == indices[node_id]:
            component = []
            while stack:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node_id:
                    break
            if len(component) > 1 or (len(component) == 1 and component[0] in adjacency.get(component[0], [])):
                circular_imports.append(sorted(nodes[item]["key"] for item in component))

    for node_id in sorted(adjacency):
        if node_id not in indices:
            visit_import(node_id)

    fingerprint = _fingerprint(
        (path.relative_to(root).as_posix(), digests[path]) for path in files if path in digests)
    commit = _git(root, "rev-parse", "HEAD")
    result = {
        "schema_version": "1.0.0",
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
        "repository": root.name,
        "commit": commit,
        # `commit` is HEAD; when the tree is dirty the graph indexes uncommitted content
        # that HEAD does not contain, so consumers must read `tree` before trusting it.
        "tree": _tree_state(root),
        "source_digest": fingerprint.hexdigest(),
        "summary": {"nodes": len(nodes), "edges": len(edges), "files": len(files),
                    "python_files": sum(1 for p in files if p.suffix == ".py"),
                    "parse_errors": len(errors)},
        "nodes": sorted(nodes.values(), key=lambda n: (n["kind"], n["key"])),
        "edges": sorted(edges.values(), key=lambda e: (e["type"], e["source"], e["target"], e["evidence"])),
        "index_errors": errors,
        "diagnostics": {
            "unreferenced_static_candidates": sorted(unreferenced, key=lambda item: item["key"]),
            "circular_imports": sorted(circular_imports),
            "note": "Static candidates are not proof of dead code; dynamic loading and runtime wiring may create valid relationships.",
        },
        "limitations": ["Python AST relationships are source-grounded.",
                        "BELONGS_TO_LAYER and TESTED_BY are explicitly inferred heuristics.",
                        "Dynamic imports, reflection and runtime-generated calls are not resolved.",
                        "Non-Python files are indexed as files; language-specific symbol parsing is not yet enabled."],
    }
    if output is not None:
        return write_graph(result, output)
    return result


def write_graph(result: dict[str, Any], output: str | Path) -> dict[str, Any]:
    """Atomically persist a built graph; keep the existing file when nothing changed.

    Returns the graph that is now on disk (the previous one when the source digest
    and commit are unchanged), so callers never need to rebuild just to write.
    """
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        try:
            previous = json.loads(destination.read_text(encoding="utf-8"))
            if (previous.get("source_digest") == result["source_digest"]
                    and previous.get("commit") == result["commit"]):
                return previous
        except (OSError, ValueError, AttributeError):
            pass
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=".graph-", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return result
