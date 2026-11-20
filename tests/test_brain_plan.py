import asyncio
import json

import pytest

from mia.agent.tools import ToolRegistry
from mia.brain.intent import analyze_intent
from mia.brain.plan import (
    Plan,
    PlanError,
    PlanRunner,
    PlanStep,
    PlanStore,
    RunPolicy,
    StepOutcome,
    new_plan,
    summarize_plan,
)
from mia.brain.planning import generate_plan, heuristic_plan, parse_model_plan
from mia.brain.router import BrainRouter
from mia.gateway.contracts import ModelCandidate, ProviderResponse
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway


def steps(*specs):
    return [PlanStep(i, f"step {i}", "reason", depends_on=list(d)) for i, *d in specs]


def test_validation_rejects_bad_plans():
    with pytest.raises(PlanError):
        new_plan("", steps(("1",)))
    with pytest.raises(PlanError):
        new_plan("x", [])
    with pytest.raises(PlanError):
        new_plan("x", steps(("1",), ("1",)))
    with pytest.raises(PlanError):
        new_plan("x", steps(("1", "9")))
    with pytest.raises(PlanError):
        new_plan("x", steps(("1", "2"), ("2", "1")))
    with pytest.raises(PlanError):
        new_plan("x", steps(("1", "1")))
    with pytest.raises(PlanError):
        new_plan("x", [PlanStep("1", "t", "tool")])
    with pytest.raises(PlanError):
        new_plan("x", steps(*[(str(i),) for i in range(12)]))


def test_ready_steps_follow_dependencies_and_failure_blocks_dependents():
    plan = new_plan("x", steps(("1",), ("2", "1"), ("3", "2"), ("4",)))
    assert [s.id for s in plan.ready_steps()] == ["1", "4"]
    plan.step("1").status = "failed"
    assert plan.propagate_blocks() == ["2", "3"]
    assert plan.step("3").status == "blocked"
    assert plan.recompute_status() == "planned"  # step 4 still runnable


class FakeExecutor:
    def __init__(self, script):
        self.script = script  # step id -> list of outcomes
        self.calls = []

    async def execute(self, step, plan):
        self.calls.append(step.id)
        outcomes = self.script.get(step.id)
        if outcomes is None:
            return StepOutcome(True, f"did {step.id}")
        item = outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        if item == "hang":
            await asyncio.sleep(5)
        return item


async def _nosleep(_):
    return None


async def test_runner_completes_in_order_and_checkpoints(tmp_path):
    store = PlanStore(tmp_path / "p.db")
    plan = new_plan("goal", steps(("1",), ("2", "1"), ("3", "2")), "conv-1")
    runner = PlanRunner(store, FakeExecutor({}), sleep=_nosleep)
    await runner.run(plan)
    assert plan.status == "completed" and plan.progress()["fraction"] == 1.0
    assert all(s.status == "completed" for s in plan.steps)
    assert len(store.checkpoints(plan.id)) >= 3
    assert store.load(plan.id).status == "completed"


async def test_transient_failures_retry_up_to_limit_then_fail():
    ex = FakeExecutor({"1": [StepOutcome(False, error="busy", error_class="transient")] * 5})
    plan = new_plan("g", steps(("1",), ("2", "1")))
    runner = PlanRunner(None, ex, RunPolicy(max_attempts=3), sleep=_nosleep)
    await runner.run(plan)
    assert ex.calls == ["1", "1", "1"]
    assert plan.step("1").status == "failed" and plan.step("1").attempts == 3
    assert plan.step("2").status == "blocked" and plan.status == "failed"
    assert [e["event"] for e in runner.events].count("retry") == 2


async def test_transient_then_success_and_permanent_failure_not_retried():
    ex = FakeExecutor({"1": [StepOutcome(False, error="x", error_class="transient"),
                             StepOutcome(True, "ok")],
                       "2": [StepOutcome(False, error="bad args", error_class="validation")]})
    plan = new_plan("g", steps(("1",), ("2", "1"), ("3",)))
    await PlanRunner(None, ex, sleep=_nosleep).run(plan)
    assert plan.step("1").status == "completed" and plan.step("1").attempts == 2
    assert plan.step("2").status == "failed" and plan.step("2").attempts == 1
    assert plan.step("3").status == "completed"
    assert plan.status == "partial"


async def test_timeout_and_executor_exceptions_are_contained():
    ex = FakeExecutor({"1": ["hang", "hang"], "2": [RuntimeError("boom")]})
    plan = new_plan("g", steps(("1",), ("2",)))
    runner = PlanRunner(None, ex, RunPolicy(max_attempts=2, step_timeout_s=0.05), sleep=_nosleep)
    await runner.run(plan)
    assert plan.step("1").status == "failed" and plan.step("1").error == "step timed out"
    assert plan.step("2").status == "failed" and "boom" in plan.step("2").error


async def test_approval_pauses_plan_and_resume_continues():
    ex = FakeExecutor({"2": [StepOutcome(False, "needs ok", error_class="needs_approval",
                                         pending_action={"approval_id": "a1"})]})
    plan = new_plan("g", steps(("1",), ("2", "1"), ("3", "2")))
    await PlanRunner(None, ex, sleep=_nosleep).run(plan)
    assert plan.status == "awaiting_approval" and plan.step("3").status == "planned"
    assert plan.step("2").evidence["pending_action"]["approval_id"] == "a1"
    plan.step("2").status = "completed"  # owner approved and the action ran
    await PlanRunner(None, ex, sleep=_nosleep).run(plan)
    assert plan.status == "completed"


async def test_run_has_hard_cap_on_total_attempts():
    ex = FakeExecutor({})
    plan = new_plan("g", steps(*[(str(i),) for i in range(8)]))
    await PlanRunner(None, ex, RunPolicy(max_total_steps=3), sleep=_nosleep).run(plan)
    assert len(ex.calls) == 3 and plan.status == "running"


def test_revision_keeps_completed_work(tmp_path):
    plan = new_plan("g", steps(("1",), ("2", "1")))
    plan.step("1").status = "completed"
    plan.step("2").status = "failed"
    plan.revise([PlanStep("2b", "alternative", "reason", depends_on=["1"])])
    assert [s.id for s in plan.steps] == ["1", "2b"] and plan.revision == 2
    with pytest.raises(PlanError):
        plan.revise([PlanStep("1", "dup", "reason")])


def test_store_roundtrip_restore_and_crash_recovery(tmp_path):
    store = PlanStore(tmp_path / "p.db")
    plan = new_plan("g", steps(("1",), ("2", "1")), "conv-9")
    store.save(plan)
    ckpt = store.checkpoint(plan, "initial")
    plan.step("1").status = "attempted"
    plan.recompute_status()
    store.save(plan)
    # simulate restart
    store2 = PlanStore(tmp_path / "p.db")
    assert store2.recover_interrupted() == [plan.id]
    loaded = store2.load(plan.id)
    assert loaded.step("1").status == "planned" and "interrupted" in loaded.step("1").error
    assert store2.restore(plan.id, ckpt).step("1").status == "planned"
    assert store2.latest_for_conversation("conv-9").id == plan.id
    assert store2.latest_for_conversation("other") is None


def test_summary_is_status_based():
    plan = new_plan("ship it", steps(("1",)))
    text = summarize_plan(plan)
    assert "ship it" in text and "[planned]" in text


# ---------- generation ----------

def make_registry():
    reg = ToolRegistry()
    reg.register(name="files.list", description="List", permission="fs.read", mode="read",
                 parameters={"type": "object", "properties": {"path": {"type": "string"}},
                             "required": ["path"]}, handler=lambda a: {"ok": True})
    reg.register(name="files.write", description="Write", permission="fs.write", mode="preview",
                 parameters={"type": "object", "properties": {}}, handler=lambda a: {"ok": True})
    return reg


def test_parse_model_plan_accepts_valid_and_rejects_untrusted_content():
    reg = make_registry()
    good = json.dumps({"steps": [
        {"id": "1", "title": "List", "kind": "tool", "tool_name": "files.list",
         "arguments": {"path": "."}, "depends_on": []},
        {"id": "2", "title": "Summarize", "kind": "reason", "depends_on": ["1"]}]})
    plan = parse_model_plan("```json\n" + good + "\n```", "obj", reg)
    assert [s.kind for s in plan.steps] == ["tool", "reason"]
    bad_cases = [
        "not json",
        json.dumps({"steps": [{"title": "x", "kind": "tool", "tool_name": "shell.run",
                               "arguments": {}}]}),            # invented tool
        json.dumps({"steps": [{"title": "x", "kind": "tool", "tool_name": "files.write",
                               "arguments": {}}]}),            # not read-only
        json.dumps({"steps": [{"title": "x", "kind": "tool", "tool_name": "files.list",
                               "arguments": {"path": 3}}]}),   # bad argument
        json.dumps({"steps": [{"title": "x", "kind": "reason", "approve": True}]}),  # extra key
        json.dumps({"steps": [{"id": "1", "title": "a", "kind": "reason", "depends_on": ["1"]}]}),
        json.dumps({"steps": [], "approved": True}),
    ]
    for raw in bad_cases:
        with pytest.raises(PlanError):
            parse_model_plan(raw, "obj", reg)


class JsonProvider:
    provider_id = "plan-model"

    def __init__(self, reply, fail=False):
        self.reply, self.fail = reply, fail

    async def health(self):
        return True

    async def list_models(self):
        return []

    async def complete(self, candidate, request):
        if self.fail:
            from mia.gateway.contracts import TransientProviderError
            raise TransientProviderError("down")
        return ProviderResponse(content=self.reply)


def _setup(reply, fail=False):
    registry = ProviderRegistry()
    registry.register(JsonProvider(reply, fail))
    cand = ModelCandidate("plan-model", "m", frozenset({"chat"}), 32768, True, True, True,
                          data_local=True)
    return ModelGateway(registry), [cand]


async def test_generate_plan_uses_model_then_falls_back():
    reg = make_registry()
    analysis = analyze_intent("Analyze my project and prioritize issues, then suggest next steps.")
    ok = json.dumps({"steps": [{"id": "1", "title": "Inspect", "kind": "reason"},
                               {"id": "2", "title": "Prioritize", "kind": "reason",
                                "depends_on": ["1"]}]})
    gw, cands = _setup(ok)
    plan, info = await generate_plan(objective="o", analysis=analysis, gateway=gw,
                                     router=BrainRouter(), candidates=cands, tools=reg)
    assert info["source"] == "model" and len(plan.steps) == 2

    gw, cands = _setup("I cannot do JSON")
    plan, info = await generate_plan(objective="o", analysis=analysis, gateway=gw,
                                     router=BrainRouter(), candidates=cands, tools=reg)
    assert info["source"] == "deterministic_fallback" and len(plan.steps) == 5

    gw, cands = _setup("", fail=True)
    plan, info = await generate_plan(objective="o", analysis=analysis, gateway=gw,
                                     router=BrainRouter(), candidates=cands, tools=reg)
    assert info["source"] == "deterministic_fallback"

    plan, info = await generate_plan(objective="o", analysis=analysis, gateway=gw,
                                     router=BrainRouter(), candidates=[], tools=reg)
    assert info["source"] == "deterministic_fallback" and "No suitable model" in info["reason"]


def test_heuristic_plan_is_valid_and_linear():
    plan = heuristic_plan("do things")
    plan.validate()
    assert isinstance(plan, Plan) and plan.ready_steps()[0].id == "1"


async def test_resume_follows_approval_outcomes(tmp_path):
    from mia.agent.tools import ToolRegistry
    from mia.brain.orchestrator import Brain
    from mia.gateway.registry import ProviderRegistry
    from mia.gateway.service import ModelGateway
    from mia.memory.store import MemoryStore

    states = {"a1": "pending"}
    store = PlanStore(tmp_path / "p.db")
    brain = Brain(gateway=ModelGateway(ProviderRegistry()), memory=MemoryStore(tmp_path / "m.db"),
                  tools=ToolRegistry(), identity=lambda: ("x", 1), plan_store=store,
                  approval_status=lambda a: states.get(a))

    def make():
        plan = new_plan("g", steps(("1",), ("2", "1")))
        plan.step("1").status = "completed"
        plan.step("2").status = "awaiting_approval"
        plan.step("2").evidence = {"pending_action": {"approval_id": "a1"}}
        plan.recompute_status()
        store.save(plan)
        return plan.id

    pid = make()
    assert (await brain.resume_plan(pid))["status"] == "awaiting_approval"  # still pending
    states["a1"] = "consumed"
    done = await brain.resume_plan(pid)
    assert done["status"] == "completed" and "not observed" in done["steps"][1]["result_summary"]
    pid = make()
    states["a1"] = "rejected"
    rejected = await brain.resume_plan(pid)
    assert rejected["steps"][1]["status"] == "failed" and rejected["status"] == "partial"
