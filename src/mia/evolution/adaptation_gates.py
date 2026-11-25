"""Go/no-go gates that must all pass before any model-adaptation experiment (MIA-EVOL-09).

``python -m mia.evolution.adaptation_gates manifest.json`` prints the result and exits 0 only when
every gate passes. Nothing here trains, downloads or spends anything; it refuses to say "ready"
on missing, vague or merely truthy evidence. See docs/evolution/FOUNDATION_MODEL_FEASIBILITY.md.
"""

from __future__ import annotations

import json
import sys
from typing import Any

MIN_HELD_OUT_ITEMS = 120  # ~118 items per condition detect 70% vs 85% (feasibility study, section 4)
GATES = ("lawful_dataset", "held_out_evaluation", "base_model_licence", "owner_hardware_or_budget",
         "rollback_and_safety_plan")


def _is_true(value: Any) -> bool:
    return value is True


def _count(section: dict[str, Any], key: str) -> int | None:
    value = section.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _text(section: dict[str, Any], key: str) -> bool:
    value = section.get(key)
    return isinstance(value, str) and bool(value.strip())


def _section(manifest: Any, name: str) -> dict[str, Any]:
    value = manifest.get(name) if isinstance(manifest, dict) else None
    return value if isinstance(value, dict) else {}


def _lawful_dataset(m: Any) -> list[str]:
    d, problems = _section(m, "dataset"), []
    items = _count(d, "items")
    if not items:
        problems.append("dataset.items must be a positive integer")
    for key, why in (("items_without_provenance", "every item needs recorded provenance"),
                     ("items_without_owner_consent", "every item needs the owner's consent"),
                     ("unapproved_third_party_items", "third-party text needs explicit approval")):
        if _count(d, key) != 0:
            problems.append(f"{key} must be 0 ({why})")
    if not _is_true(d.get("owner_may_delete")):
        problems.append("dataset.owner_may_delete must be true")
    return problems


def _held_out(m: Any) -> list[str]:
    h, problems = _section(m, "held_out"), []
    size = _count(h, "items")
    if size is None or size < MIN_HELD_OUT_ITEMS:
        problems.append(f"held_out.items must be at least {MIN_HELD_OUT_ITEMS}")
    if _count(h, "items_overlapping_training") != 0:
        problems.append("held_out.items_overlapping_training must be 0")
    if not _is_true(h.get("baseline_measured")):
        problems.append("held_out.baseline_measured must be true")
    return problems


def _licence(m: Any) -> list[str]:
    b, problems = _section(m, "base_model"), []
    for key in ("model_id", "repository_url", "licence_name", "licence_checked_on"):
        if not _text(b, key):
            problems.append(f"base_model.{key} is required")
    if not _is_true(b.get("licence_permits_fine_tuning")):
        problems.append("base_model.licence_permits_fine_tuning must be true")
    return problems


def _owner(m: Any) -> list[str]:
    o, problems = _section(m, "owner"), []
    gpu, budget = o.get("gpu_memory_gb"), o.get("budget_cap_usd")
    if not any(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in (gpu, budget)):
        problems.append("owner.gpu_memory_gb or owner.budget_cap_usd must be a positive number")
    if not _is_true(o.get("approved_training_run")):
        problems.append("owner.approved_training_run must be true")
    return problems


def _rollback(m: Any) -> list[str]:
    r = _section(m, "rollback")
    return [f"rollback.{key} must be true" for key in (
        "base_model_untouched", "adapter_is_separate_versioned_file", "switch_is_configuration",
        "safety_regression_comparison_planned") if not _is_true(r.get(key))]


_CHECKS = {"lawful_dataset": _lawful_dataset, "held_out_evaluation": _held_out,
           "base_model_licence": _licence, "owner_hardware_or_budget": _owner,
           "rollback_and_safety_plan": _rollback}


def check_adaptation_gates(manifest: Any) -> dict[str, Any]:
    gates = {}
    for name in GATES:
        problems = _CHECKS[name](manifest)
        gates[name] = {"passed": not problems, "problems": problems}
    return {"ready": all(g["passed"] for g in gates.values()), "gates": gates,
            "note": "Ready means the preconditions are documented, not that tuning will help; "
                    "any benefit must still be shown on the held-out set."}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m mia.evolution.adaptation_gates MANIFEST.json")
        return 2
    try:
        with open(args[0], encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        manifest = None
        print(f"manifest unreadable: {type(exc).__name__}")
    report = check_adaptation_gates(manifest)
    for name, gate in report["gates"].items():
        print(f"{'PASS' if gate['passed'] else 'FAIL'} {name}")
        for problem in gate["problems"]:
            print(f"     - {problem}")
    print("READY" if report["ready"] else "NOT READY: no adaptation experiment may start")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
