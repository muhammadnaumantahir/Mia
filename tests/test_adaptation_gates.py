"""Adaptation go/no-go gates (MIA-EVOL-09): pure checks, nothing is trained or downloaded."""

import copy
import json

import pytest

from mia.evolution.adaptation_gates import GATES, MIN_HELD_OUT_ITEMS, check_adaptation_gates, main

GOOD = {
    "dataset": {"items": 500, "items_without_provenance": 0, "items_without_owner_consent": 0,
                "unapproved_third_party_items": 0, "owner_may_delete": True},
    "held_out": {"items": 120, "items_overlapping_training": 0, "baseline_measured": True},
    "base_model": {"model_id": "org/model-8b", "repository_url": "https://example.invalid/org/model-8b",
                   "licence_name": "Apache-2.0", "licence_checked_on": "2026-11-24",
                   "licence_permits_fine_tuning": True},
    "owner": {"gpu_memory_gb": 24, "approved_training_run": True},
    "rollback": {"base_model_untouched": True, "adapter_is_separate_versioned_file": True,
                 "switch_is_configuration": True, "safety_regression_comparison_planned": True},
}


def _with(section, key, value):
    manifest = copy.deepcopy(GOOD)
    manifest[section][key] = value
    return manifest


def test_a_complete_manifest_is_ready_and_every_gate_is_reported():
    report = check_adaptation_gates(GOOD)
    assert report["ready"] is True and list(report["gates"]) == list(GATES)
    assert all(g["passed"] and g["problems"] == [] for g in report["gates"].values())


@pytest.mark.parametrize("manifest", [None, {}, [], "x", {"dataset": "nope"}])
def test_missing_or_malformed_evidence_is_never_ready(manifest):
    report = check_adaptation_gates(manifest)
    assert report["ready"] is False and not any(g["passed"] for g in report["gates"].values())


@pytest.mark.parametrize("section, key, value, gate", [
    ("dataset", "items", 0, "lawful_dataset"),
    ("dataset", "items_without_provenance", 1, "lawful_dataset"),
    ("dataset", "items_without_owner_consent", 3, "lawful_dataset"),
    ("dataset", "unapproved_third_party_items", 1, "lawful_dataset"),
    ("dataset", "owner_may_delete", False, "lawful_dataset"),
    ("held_out", "items", MIN_HELD_OUT_ITEMS - 1, "held_out_evaluation"),
    ("held_out", "items_overlapping_training", 1, "held_out_evaluation"),
    ("held_out", "baseline_measured", False, "held_out_evaluation"),
    ("base_model", "licence_name", " ", "base_model_licence"),
    ("base_model", "licence_permits_fine_tuning", False, "base_model_licence"),
    ("owner", "gpu_memory_gb", 0, "owner_hardware_or_budget"),
    ("owner", "approved_training_run", False, "owner_hardware_or_budget"),
    ("rollback", "base_model_untouched", False, "rollback_and_safety_plan"),
    ("rollback", "safety_regression_comparison_planned", False, "rollback_and_safety_plan"),
])
def test_each_unmet_condition_fails_only_its_own_gate(section, key, value, gate):
    report = check_adaptation_gates(_with(section, key, value))
    assert report["ready"] is False
    assert [n for n, g in report["gates"].items() if not g["passed"]] == [gate]
    assert key in " ".join(report["gates"][gate]["problems"])


def test_exactly_the_minimum_held_out_size_passes():
    assert check_adaptation_gates(_with("held_out", "items", MIN_HELD_OUT_ITEMS))["ready"] is True


def test_truthy_strings_and_bools_masquerading_as_numbers_do_not_count():
    for manifest in (_with("owner", "approved_training_run", "yes"),
                     _with("dataset", "owner_may_delete", 1),
                     _with("held_out", "baseline_measured", "true"),
                     _with("held_out", "items", True),
                     _with("dataset", "items_without_provenance", False),
                     _with("owner", "gpu_memory_gb", True)):
        assert check_adaptation_gates(manifest)["ready"] is False


def test_a_budget_cap_can_stand_in_for_gpu_memory():
    manifest = copy.deepcopy(GOOD)
    del manifest["owner"]["gpu_memory_gb"]
    manifest["owner"]["budget_cap_usd"] = 25
    assert check_adaptation_gates(manifest)["ready"] is True


def test_command_line_exit_codes_and_unreadable_manifests(tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(GOOD), encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_with("held_out", "items", 5)), encoding="utf-8")
    assert main([str(good)]) == 0 and "READY" in capsys.readouterr().out
    assert main([str(bad)]) == 1 and "NOT READY" in capsys.readouterr().out
    assert main([str(tmp_path / "missing.json")]) == 1 and "unreadable" in capsys.readouterr().out
    assert main([]) == 2


def test_the_held_out_minimum_matches_the_feasibility_study():
    assert MIN_HELD_OUT_ITEMS == 120  # docs/evolution/FOUNDATION_MODEL_FEASIBILITY.md, section 4
