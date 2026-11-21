import json

from mia.brain.evaluation import BUILTIN_SCENARIOS, Scenario, run_all, run_scenario, summarize


async def test_builtin_scenarios_pass_and_are_recorded(tmp_path):
    log = tmp_path / "eval.jsonl"
    results = await run_all(log_path=log)
    assert [r.name for r in results] == [s.name for s in BUILTIN_SCENARIOS]
    failed = [(r.name, r.failures) for r in results if not r.passed]
    assert not failed, failed
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(rows) == len(BUILTIN_SCENARIOS) and all(r["passed"] for r in rows)


async def test_a_wrong_expectation_is_reported_as_failure():
    bad = Scenario("bad", "Explain what DNS does", "DNS maps names.", expect_codes=("empty_response",))
    result = await run_scenario(bad)
    assert not result.passed and "missing issue code empty_response" in result.failures



async def test_reasoning_quality_scenarios_cover_uncertainty_corrections_and_memory_evidence():
    selected = tuple(s for s in BUILTIN_SCENARIOS if s.name in {
        "calibrated-uncertainty", "owner-correction-acknowledged", "unsupported-memory-citation"
    })
    assert len(selected) == 3
    results = [await run_scenario(s) for s in selected]
    failed = [(r.name, r.failures, r.detail) for r in results if not r.passed]
    assert not failed, failed


async def test_scenario_set_covers_required_reasoning_quality_areas_and_languages():
    categories = {s.category for s in BUILTIN_SCENARIOS}
    assert {"memory-conflict", "tool-failure", "continuity", "uncertainty", "corrections",
            "provider-failure", "unsupported-claims"} <= categories
    assert {"en", "roman-urdu", "ur"} <= {s.language for s in BUILTIN_SCENARIOS}
    assert len({s.name for s in BUILTIN_SCENARIOS}) == len(BUILTIN_SCENARIOS)


async def test_context_assertions_can_fail_negative_controls():
    # Nothing was seeded, so the model cannot have received the fact: must be reported.
    missing = Scenario("nc-missing", "What did we decide about the billing database?",
                       expect_context=("PostgreSQL",))
    result = await run_scenario(missing)
    assert not result.passed and "model context lacks 'PostgreSQL'" in result.failures
    # The user's own prompt is part of the context, so a forbidden term from it must trip.
    leaked = Scenario("nc-leak", "Tell me about Atlas", forbid_context=("Atlas",))
    result = await run_scenario(leaked)
    assert not result.passed and "model context has 'Atlas'" in result.failures


async def test_tool_outcome_assertion_distinguishes_failed_from_refused_and_completed():
    call = ({"id": "1", "type": "function", "function": {"name": "eval.broken", "arguments": "{}"}},)
    wrong = Scenario("nc-tool", "Look up my notes", "Done.", tools=("eval.broken",),
                     tool_calls=call, expect_tool_status="completed")
    result = await run_scenario(wrong)
    assert not result.passed and result.detail["tool_statuses"] == ["failed"]
    assert result.detail["tool_stages"] == ["execution"]  # really executed, not refused


async def test_memory_correction_scenario_uses_the_real_supersede_path():
    scenario = next(s for s in BUILTIN_SCENARIOS
                    if s.name == "memory-superseded-fact-not-sent-to-model")
    result = await run_scenario(scenario)
    assert result.passed, result.failures
    assert result.detail["context_refs"] >= 1


async def test_summary_never_claims_live_model_quality_and_counts_by_bucket():
    results = await run_all()
    info = summarize(results)
    assert info["total"] == len(BUILTIN_SCENARIOS) == info["passed"] and info["failed"] == []
    assert info["live_model_quality"] == "not_measured"
    assert info["evidence"] == "deterministic_scripted_provider"
    assert sum(b["total"] for b in info["by_category"].values()) == info["total"]
    assert sum(b["total"] for b in info["by_language"].values()) == info["total"]
    assert info["by_language"]["ur"]["total"] >= 2


async def test_results_are_deterministic_across_runs():
    first = [(r.name, r.passed, r.detail) for r in await run_all()]
    second = [(r.name, r.passed, r.detail) for r in await run_all()]
    assert first == second
