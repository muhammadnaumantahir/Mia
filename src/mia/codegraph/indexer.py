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

EXCLUDED = {".git", ".venv", "venv", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", "secrets", "credentials", "private_keys"}
SENSITIVE_FILES = {".env", ".env.local", ".env.production", "secrets.json", "credentials.json",
                   "service-account.json", "id_rsa", "id_ed25519"}
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
            if name.lower() in SENSITIVE_FILES or (
                name.lower().startswith(".env.") and name.lower() != ".env.example"
            ):
                continue
            if path.suffix.lower() in SOURCE_SUFFIXES or name in {"Dockerfile", "Makefile", ".env.example"}:
                found.append(path)
    return sorted(found, key=lambda p: p.relative_to(root).as_posix())


def source_snapshot(root: str | Path) -> tuple[tuple[str, int, int], ...]:
    """Cheap filesystem snapshot used by the watcher to detect source changes."""
    root = Path(root).resolve()
    snapshot = []
    for path in _python_files(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot.append((path.relative_to(root).as_posix(), stat.st_mtime_ns, stat.st_size))
    return tuple(snapshot)


def build_graph(root: str | Path, *, output: str | Path | None = None) -> dict[str, Any]:
    """Build a deterministic graph from repository source and documentation."""
    root = Path(root).resolve()
    files = _python_files(root)
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    modules = {_module_path(path, root): path for path in files if path.suffix == ".py"}
    symbol_index: dict[str, list[str]] = {}
    parsed: dict[Path, ast.Module] = {}
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

    for path in files:
        if path.suffix != ".py":
            continue
        rel = path.relative_to(root).as_posix()
        fid = file_ids[path]
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=rel)
            parsed[path] = tree
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append({"path": rel, "error": f"{type(exc).__name__}: {str(exc)[:180]}"})
            continue

        def visit_scope(body: list[ast.stmt], parent_key: str) -> None:
            for item in body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    kind = "class" if isinstance(item, ast.ClassDef) else "function"
                    key = f"{rel}:{parent_key + '.' if parent_key else ''}{item.name}"
                    sid = add_node(kind, key, label=item.name, path=rel,
                                   qualified_name=parent_key + "." + item.name if parent_key else item.name,
                                   line=item.lineno, end_line=getattr(item, "end_lineno", item.lineno))
                    symbol_index.setdefault(item.name, []).append(sid)
                    add_edge(fid, sid, "DECLARES", "confirmed", f"ast:{rel}:{item.lineno}", item.lineno)
                    if isinstance(item, ast.ClassDef):
                        for base in item.bases:
                            if isinstance(base, ast.Name):
                                for candidate in symbol_index.get(base.id, []):
                                    add_edge(sid, candidate, "EXTENDS", "inferred",
                                             f"base expression {base.id}", item.lineno)
                        visit_scope(item.body, key.split(":", 1)[1])
                    else:
                        visit_scope(item.body, key.split(":", 1)[1])
                elif isinstance(item, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With,
                                        ast.AsyncWith, ast.Try)):
                    for field in ("body", "orelse", "finalbody"):
                        child = getattr(item, field, None)
                        if isinstance(child, list):
                            visit_scope(child, parent_key)

        visit_scope(tree.body, "")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [(alias.name, alias.asname or alias.name.split(".")[0]) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [(f"{module}.{alias.name}".strip("."), alias.asname or alias.name) for alias in node.names]
                if node.level:
                    package = _module_path(path, root).split(".")[:-node.level]
                    module = ".".join(package + ([module] if module else []))
                    names = [(f"{module}.{alias.name}".strip("."), alias.asname or alias.name) for alias in node.names]
            else:
                continue
            for imported, alias in names:
                prefix = imported
                target_path = None
                while prefix:
                    target_path = modules.get(prefix)
                    if target_path is not None:
                        break
                    if "." in prefix and (
                        prefix.startswith("mia.") or prefix.startswith("src.mia.")
                    ):
                        suffix_matches = [
                            candidate for module, candidate in modules.items()
                            if module.endswith("." + prefix)
                        ]
                        if len(suffix_matches) == 1:
                            target_path = suffix_matches[0]
                            break
                    if "." not in prefix:
                        break
                    prefix = prefix.rsplit(".", 1)[0]
                if target_path is not None:
                    add_edge(fid, file_ids[target_path], "IMPORTS", "confirmed",
                             f"ast-import:{rel}:{node.lineno}", node.lineno)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                        if decorator.func.attr in {"get", "post", "put", "patch", "delete"}:
                            route = next((arg.value for arg in decorator.args
                                          if isinstance(arg, ast.Constant) and isinstance(arg.value, str)), None)
                            if route:
                                route_id = add_node("api_endpoint", f"{decorator.func.attr.upper()} {route}",
                                                     label=f"{decorator.func.attr.upper()} {route}",
                                                     path=rel, line=decorator.lineno)
                                function_id = _node_id("function", f"{rel}:{node.name}")
                                add_edge(function_id, route_id, "EXPOSES_API", "confirmed",
                                         f"fastapi-decorator:{rel}:{decorator.lineno}", decorator.lineno)

    # Resolve literal configuration/data-file access without guessing dynamic paths.
    for path, tree in parsed.items():
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
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
                        relation = "WRITES_TO" if isinstance(mode, str) and any(flag in mode for flag in ("w", "a", "x", "+")) else "READS_FROM"
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"read_text", "read_bytes", "write_text", "write_bytes"}:
                    receiver = node.func.value
                    if (isinstance(receiver, ast.Call) and isinstance(receiver.func, ast.Name)
                            and receiver.func.id == "Path" and receiver.args
                            and isinstance(receiver.args[0], ast.Constant)
                            and isinstance(receiver.args[0].value, str)):
                        raw_path = receiver.args[0].value
                        relation = "WRITES_TO" if node.func.attr.startswith("write") else "READS_FROM"
            if not raw_path or not relation:
                continue
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
            add_edge(file_ids[path], file_ids[target_path], relation, "confirmed",
                     f"ast-file-access:{rel}:{node.lineno}:{raw_path}", node.lineno)

    # Resolve direct calls only when the target is unique in the same file.
    # Cross-file calls via DI, aliases and dynamic imports are intentionally unresolved.
    for path, tree in parsed.items():
        rel = path.relative_to(root).as_posix()
        for function in (n for n in ast.walk(tree)
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            matches = [n for n in nodes.values() if n["kind"] == "function"
                       and n["path"] == rel and n.get("line") == function.lineno]
            if len(matches) != 1:
                continue
            caller_id = matches[0]["id"]
            class CallVisitor(ast.NodeVisitor):
                def __init__(self, root_function: ast.AST) -> None:
                    self.root_function = root_function
                    self.calls: list[ast.Call] = []
                def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                    if node is self.root_function:
                        self.generic_visit(node)
                def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                    if node is self.root_function:
                        self.generic_visit(node)
                def visit_Call(self, node: ast.Call) -> None:
                    self.calls.append(node)
                    self.generic_visit(node)
            visitor = CallVisitor(function)
            visitor.visit(function)
            for call in visitor.calls:
                if not isinstance(call.func, ast.Name):
                    continue
                targets = [sid for sid in symbol_index.get(call.func.id, [])
                           if nodes.get(sid, {}).get("path") == rel and sid != caller_id]
                if len(targets) == 1:
                    add_edge(caller_id, targets[0], "CALLS", "confirmed",
                             f"ast-call:{rel}:{call.lineno}", call.lineno)

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

    fingerprint = hashlib.sha256()
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            data = path.read_bytes()
        except OSError as exc:
            errors.append({"path": rel, "error": f"{type(exc).__name__}: {str(exc)[:180]}"})
            continue
        fingerprint.update(rel.encode("utf-8") + b"\0" + hashlib.sha256(data).digest())
    commit = _git(root, "rev-parse", "HEAD")
    result = {
        "schema_version": "1.0.0",
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
        "repository": root.name,
        "commit": commit,
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
