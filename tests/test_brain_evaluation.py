import json

from mia.brain.evaluation import BUILTIN_SCENARIOS, Scenario, run_all, run_scenario


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
