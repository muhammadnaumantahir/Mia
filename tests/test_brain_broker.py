import asyncio
import json

import pytest

from mia.agent.tools import ToolRegistry, ToolResult
from mia.brain.broker import (
    BrokerStepExecutor,
    DefaultPermissionEvaluator,
    GateDecision,
    ToolBroker,
)
from mia.brain.intent import analyze_intent
from mia.brain.plan import PlanRunner, PlanStep, StepOutcome, new_plan


def make(handlers=None):
    reg = ToolRegistry()
    handlers = handlers or {}
    reg.register(name="files.list", description="List files in a folder", permission="fs.read",
                 mode="read", parameters={"type": "object", "additionalProperties": False,
                                          "properties": {"path": {"type": "string", "maxLength": 50}},
                                          "required": ["path"]},
                 handler=handlers.get("list", lambda a: {"ok": True, "items": ["a.py"]}))
    reg.register(name="filesystem.read_text", description="Read a text file", permission="fs.read",
                 mode="read", parameters={"type": "object", "properties": {"path": {"type": "string"}},
                                          "required": ["path"]},
                 handler=lambda a: {"ok": True, "text": "secret contents"})
    reg.register(name="app.launch", description="Prepare launching an application",
                 permission="app.launch", mode="preview",
                 parameters={"type": "object", "properties": {"app": {"type": "string"}},
                             "required": ["app"]},
                 handler=lambda a: ToolResult({"ok": True}, {"approval_id": "ap-1", "tool": "app.launch"}))
    reg.register(name="boom", description="Always raises", permission="x.read", mode="read",
                 parameters={"type": "object", "properties": {}},
                 handler=handlers.get("boom", lambda a: (_ for _ in ()).throw(RuntimeError("kaput"))))
    return reg


def broker(reg=None, **kw):
    return ToolBroker(reg or make(), **kw)


ALL = {"files.list", "filesystem.read_text", "app.launch", "boom"}


# ---- capability selection ----

def test_light_conversation_is_offered_no_tools():
    b = broker()
    assert b.select_tools("hi", analyze_intent("hi")) == []
    assert b.select_tools("tell me a joke", analyze_intent("tell me a joke")) == []


def test_tool_intent_and_complex_requests_offer_registered_tools_only():
    b = broker()
    p = "list the files in my folder"
    offered = b.select_tools(p, analyze_intent(p))
    assert set(offered) <= ALL and "files.list" in offered
    p = "Analyze my project and identify the most important issues, then prioritize them and finally suggest next actions."
    assert set(b.select_tools(p, analyze_intent(p))) == ALL
    schemas = b.schemas_for(["files.list"])
    assert [s["function"]["name"] for s in schemas] == ["files.list"]


# ---- bypass attempts ----

@pytest.mark.parametrize("name,args,stage", [
    ("shell.run", {"cmd": "rm -rf /"}, "registered"),          # invented tool
    ("files.List", {"path": "."}, "registered"),                # near-miss name is NOT mapped
    ("files.list ", {"path": "."}, "registered"),
    (None, {}, "parse"),
    ("files.list", "{not json", "parse"),
    ("files.list", "[1,2]", "parse"),
    ("files.list", {"path": 5}, "validation"),
    ("files.list", {"path": "x", "admin": True}, "validation"),  # smuggled extra argument
    ("files.list", {}, "validation"),
])
async def test_untrusted_calls_are_refused_before_execution(name, args, stage):
    ran = []
    reg = make({"list": lambda a: ran.append(a) or {"ok": True}})
    rec = await broker(reg).call(name, args, offered=ALL)
    assert rec.status == "refused" and rec.stage == stage and not ran


async def test_registered_but_not_offered_tool_is_refused():
    rec = await broker().call("filesystem.read_text", {"path": "x"}, offered={"files.list"})
    assert rec.status == "refused" and rec.stage == "offered" and rec.error_class == "permission"


async def test_owner_deny_policy_overrides_model_request():
    b = broker(evaluator=DefaultPermissionEvaluator(deny_permissions={"fs.read"}))
    rec = await b.call("files.list", {"path": "."}, offered=ALL)
    assert rec.status == "refused" and rec.stage == "permission"
    assert not rec.model_fault


async def test_non_read_preview_modes_are_never_allowed_by_default_policy():
    reg = ToolRegistry()
    reg.register(name="x", description="d", permission="p", mode="read",
                 parameters={"type": "object", "properties": {}}, handler=lambda a: {"ok": True})
    reg._tools["x"] = reg._tools["x"].__class__(  # simulate a mis-registered mutating tool
        name="x", description="d", parameters={"type": "object", "properties": {}},
        permission="p", mode="write", handler=lambda a: {"ok": True})
    rec = await ToolBroker(reg).call("x", {}, offered={"x"})
    assert rec.status == "refused" and rec.stage == "permission"


async def test_data_egress_blocks_indexed_file_search_for_remote_providers():
    reg = make()
    reg.register(
        name="knowledge.search_files",
        description="Search indexed files",
        permission="filesystem.search",
        mode="read",
        parameters={"type": "object", "properties": {"query": {"type": "string"}},
                    "required": ["query"]},
        handler=lambda _args: {"ok": True, "items": [{"content": "private"}]},
    )
    b = ToolBroker(reg)
    rec = await b.call("knowledge.search_files", {"query": "notes"}, offered={"knowledge.search_files"},
                       provider_local=False)
    assert rec.status == "refused" and rec.stage == "egress"
    rec = await b.call("knowledge.search_files", {"query": "notes"}, offered={"knowledge.search_files"},
                       provider_local=True)
    assert rec.status == "completed"


async def test_data_egress_blocks_file_tools_for_remote_providers():
    b = broker()
    rec = await b.call("filesystem.read_text", {"path": "p"}, offered=ALL, provider_local=False)
    assert rec.status == "refused" and rec.stage == "egress"
    rec = await b.call("filesystem.read_text", {"path": "p"}, offered=ALL, provider_local=True)
    assert rec.status == "completed"
    rec = await b.call("filesystem.read_text", {"path": "p"}, offered=ALL, provider_local=False,
                       allow_remote_file_context=True)
    assert rec.status == "completed"


class AllowAll:
    def evaluate(self, tool, arguments):
        return GateDecision(True, "ok")


async def test_workflow_tools_need_their_own_remote_egress_opt_in():
    reg = ToolRegistry()
    reg.register(
        name="workflow.preview_internal",
        description="Preview internal workflow",
        permission="workflow.preview",
        mode="read",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        handler=lambda _args: {"ok": True},
    )
    b = broker(reg, evaluator=AllowAll())
    refused = await b.call(
        "workflow.preview_internal", {}, offered={"workflow.preview_internal"},
        provider_local=False, allow_remote_file_context=True,
    )
    assert refused.status == "refused" and refused.stage == "egress"
    allowed = await b.call(
        "workflow.preview_internal", {}, offered={"workflow.preview_internal"},
        provider_local=False, allow_remote_workflow_tools=True,
    )
    assert allowed.status == "completed"


async def test_custom_evaluator_is_consulted_for_every_call():
    seen = []

    class Spy(AllowAll):
        def evaluate(self, tool, arguments):
            seen.append(tool.name)
            return GateDecision(False, "permission", "spy says no")

    rec = await broker(evaluator=Spy()).call("files.list", {"path": "."}, offered=ALL)
    assert seen == ["files.list"] and rec.status == "refused"


# ---- execution and result interpretation ----

async def test_successful_call_is_completed_not_verified():
    rec = await broker().call("files.list", {"path": "."}, offered=ALL)
    assert rec.status == "completed" and rec.output["items"] == ["a.py"] and not rec.verified
    assert "tool" in rec.diagnostic() and "output" not in rec.diagnostic()


async def test_preview_tool_returns_pending_approval_without_executing_action():
    rec = await broker().call("app.launch", {"app": "calc"}, offered=ALL)
    assert rec.status == "approval_required" and rec.pending_action["approval_id"] == "ap-1"


async def test_handler_exception_is_classified_failure():
    rec = await broker().call("boom", {}, offered=ALL)
    assert rec.status == "failed" and rec.error_class == "permanent" and "kaput" in rec.reason


async def test_timeout_is_transient():
    async def slow(a):
        await asyncio.sleep(2)
        return {"ok": True}
    rec = await broker(make({"list": slow}), timeout_s=0.05).call(
        "files.list", {"path": "."}, offered=ALL)
    assert rec.status == "failed" and rec.error_class == "transient"


def test_failure_classification():
    c = ToolBroker.classify_failure
    assert c({"error_type": "TimeoutError"}) == "transient"
    assert c({"error_type": "PermissionError"}) == "permission"
    assert c({"error_type": "ValueError"}) == "validation"
    assert c({"error_type": "FileNotFoundError"}) == "permanent"


# ---- plan integration ----

async def test_plan_tool_steps_run_through_broker_and_pause_for_approval():
    b = broker()
    plan = new_plan("g", [
        PlanStep("1", "list", "tool", tool_name="files.list", arguments={"path": "."}),
        PlanStep("2", "launch", "tool", tool_name="app.launch", arguments={"app": "calc"},
                 depends_on=["1"]),
        PlanStep("3", "report", "reason", depends_on=["2"]),
    ])

    async def reason(step, plan):
        return StepOutcome(True, "reported")

    ex = BrokerStepExecutor(b, reason)
    await PlanRunner(None, ex).run(plan)
    assert plan.step("1").status == "completed"
    assert plan.step("2").status == "awaiting_approval" and plan.status == "awaiting_approval"
    assert plan.step("3").status == "planned"
    assert json.dumps(plan.to_dict())  # serialisable


async def test_plan_step_with_forged_tool_fails_and_blocks_dependents():
    plan = new_plan("g", [PlanStep("1", "bad", "tool", tool_name="shell.run", arguments={}),
                          PlanStep("2", "after", "reason", depends_on=["1"])])
    await PlanRunner(None, BrokerStepExecutor(broker())).run(plan)
    assert plan.step("1").status == "failed" and plan.step("2").status == "blocked"


async def test_ask_step_requests_input():
    plan = new_plan("g", [PlanStep("1", "Which project do you mean?", "ask")])
    await PlanRunner(None, BrokerStepExecutor(broker())).run(plan)
    assert plan.status == "awaiting_input"
