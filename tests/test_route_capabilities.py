"""MIA-COMP-01: every state-changing API route must be classified; safety stops stay un-gated."""

from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore
from mia.security.capabilities import ApprovalClass, SideEffect, all_rules, classify

SAFE = {"GET", "HEAD", "OPTIONS"}


def _mutating(tmp_path):
    app = create_app(store=ObservatoryStore(tmp_path / "c.sqlite3"))
    for route in app.routes:
        for method in getattr(route, "methods", None) or {"WS"}:
            if method not in SAFE:
                yield method, route.path


def test_every_mutating_route_is_classified(tmp_path):
    missing = [f"{m} {p}" for m, p in _mutating(tmp_path) if classify(m, p) is None]
    assert missing == [], f"classify these in mia/security/capabilities.py: {missing}"
    assert len(list(_mutating(tmp_path))) > 100


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


def test_specific_rules_win_over_general():
    assert classify("POST", "/api/computer/files/ops/execute").approval is ApprovalClass.PREVIEW_THEN_EXECUTE
    assert classify("DELETE", "/api/memory/items/x").approval is ApprovalClass.OWNER_CONFIRM
