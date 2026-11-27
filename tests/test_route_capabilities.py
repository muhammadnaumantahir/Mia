"""MIA-COMP-01: every state-changing API route must be classified; safety stops stay un-gated."""

import pytest

from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore
from mia.security.capabilities import (
    ApprovalClass,
    CapabilityInventoryError,
    SideEffect,
    all_rules,
    bind_route_capabilities,
    bound_route_capability,
    classify,
)

SAFE = {"GET", "HEAD", "OPTIONS"}


def _flatten(routes, prefix=""):
    """Yield (route, full_path); descends into routers added with include_router."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:  # newer FastAPI wraps included routers; never skip them silently
            yield from _flatten(inner.routes, prefix + getattr(route.include_context, "prefix", ""))
        else:
            yield route, prefix + route.path


def _mutating(tmp_path):
    app = create_app(store=ObservatoryStore(tmp_path / "c.sqlite3"))
    for route, path in _flatten(app.routes):
        for method in getattr(route, "methods", None) or {"WS"}:
            if method not in SAFE:
                yield method, path


def test_every_mutating_route_is_classified(tmp_path):
    missing = [f"{m} {p}" for m, p in _mutating(tmp_path) if classify(m, p) is None]
    assert missing == [], f"classify these in mia/security/capabilities.py: {missing}"
    assert len(list(_mutating(tmp_path))) > 100


def test_every_mutating_route_is_bound_to_its_live_handler(tmp_path):
    app = create_app(store=ObservatoryStore(tmp_path / "handlers.sqlite3"))
    seen = 0
    for route, path in _flatten(app.routes):
        endpoint = getattr(route, "endpoint", None)
        for method in getattr(route, "methods", None) or {"WS"}:
            if method in SAFE:
                continue
            binding = bound_route_capability(route, method)
            assert binding is not None, f"unbound handler: {method} {path}"
            assert binding.method == method
            assert binding.path == path
            assert binding.capability == classify(method, path)
            assert binding.handler_identity == (
                f"{endpoint.__module__}.{endpoint.__qualname__}"
            )
            assert callable(endpoint)
            seen += 1
    assert seen == len(app.state.route_capability_bindings)


def test_unclassified_mutating_handler_fails_closed():
    class Route:
        path = "/api/unclassified/write"
        methods = {"POST"}
        endpoint = lambda _request: {"ok": True}

    with pytest.raises(CapabilityInventoryError, match="unclassified mutating route"):
        bind_route_capabilities([Route()])


def test_non_mia_handler_cannot_be_bound_to_a_mutating_capability():
    class Route:
        path = "/api/computer/stop-all"
        methods = {"POST"}
        endpoint = lambda _request: {"ok": True}

    with pytest.raises(CapabilityInventoryError, match="no Mia handler"):
        bind_route_capabilities([Route()])


def test_no_dead_rules(tmp_path):
    routes = list(_mutating(tmp_path))
    from fnmatch import fnmatchcase
    dead = [r.pattern for r in all_rules() if not any(fnmatchcase(f"{m} {p}", r.pattern) for m, p in routes)]
    assert dead == [], f"rules matching no route: {dead}"


def test_device_execution_is_never_unapproved(tmp_path):
    for m, p in _mutating(tmp_path):
        cap = classify(m, p)
        if cap.side_effect is SideEffect.DEVICE_CONTROL:
            assert cap.approval is not ApprovalClass.NONE, f"{m} {p}"


def test_stop_controls_are_always_allowed():
    for path in ("/api/computer/stop-all", "/api/life/emergency-stop", "/api/voice/wake/stop", "/api/autonomy/pause"):
        assert classify("POST", path).approval is ApprovalClass.ALWAYS_ALLOWED


def test_browser_fill_is_grant_gated_but_click_needs_preview():
    assert classify("POST", "/api/browser/fill").approval is ApprovalClass.GRANT_GATED
    assert classify("POST", "/api/browser/click/execute").approval is ApprovalClass.PREVIEW_THEN_EXECUTE
    assert classify("POST", "/api/browser/close").approval is ApprovalClass.ALWAYS_ALLOWED


def test_specific_rules_win_over_general():
    assert classify("POST", "/api/computer/files/ops/execute").approval is ApprovalClass.PREVIEW_THEN_EXECUTE
    assert classify("DELETE", "/api/memory/items/x").approval is ApprovalClass.OWNER_CONFIRM


def test_workflow_routes_are_enumerated_and_classified(tmp_path):
    seen = {f"{m} {p}" for m, p in _mutating(tmp_path)}
    assert "POST /api/workflows" in seen and "POST /api/workflows/{workflow_id}/run" in seen
    assert classify("POST", "/api/workflows/example/run").approval is ApprovalClass.PREVIEW_THEN_EXECUTE
    assert classify("POST", "/api/workflows/example/retry-step").approval is ApprovalClass.OWNER_CONFIRM
    assert classify("POST", "/api/workflows/example/reconcile-step").approval is ApprovalClass.OWNER_CONFIRM
    assert classify("POST", "/api/workflows/example/pause").approval is ApprovalClass.ALWAYS_ALLOWED
    assert classify("POST", "/api/workflows/example/cancel").approval is ApprovalClass.ALWAYS_ALLOWED
