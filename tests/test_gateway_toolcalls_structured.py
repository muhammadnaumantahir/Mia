import json

import httpx
import pytest
import respx

from mia.gateway.contracts import (
    ModelCandidate,
    ModelRequest,
    PermanentProviderError,
    ProviderResponse,
    StreamChunk,
)
from mia.gateway.jsonschema_lite import validate_json
from mia.gateway.providers import OllamaProvider, OpenAICompatibleProvider
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.gateway.toolcalls import parse_structured, screen_tool_calls

TOOL = {"type": "function", "function": {"name": "read_note", "parameters": {
    "type": "object",
    "properties": {"title": {"type": "string", "maxLength": 20}, "limit": {"type": "integer",
                                                                            "minimum": 1,
                                                                            "maximum": 10}},
    "required": ["title"]}}}
OTHER = {"type": "function", "function": {"name": "ping", "parameters": {}}}


def call(name="read_note", arguments='{"title": "a"}', call_id="c1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def reasons(issues):
    return [i.reason for i in issues]


# ---- JSON Schema subset ---------------------------------------------------------

def test_schema_types_bounds_enum_required_and_nesting():
    schema = {"type": "object", "required": ["a"], "properties": {
        "a": {"type": "integer", "minimum": 1, "maximum": 5},
        "b": {"type": "string", "enum": ["x", "y"]},
        "c": {"type": "array", "items": {"type": "number"}, "maxItems": 2},
        "d": {"anyOf": [{"type": "string"}, {"type": "null"}]}}}
    assert validate_json({"a": 3, "b": "x", "c": [1, 2.5], "d": None}, schema) == []
    assert validate_json({"a": True}, schema)  # bool is not an integer
    assert validate_json({}, schema) == ["$: missing required 'a'"]
    assert validate_json({"a": 9}, schema) == ["$.a: above maximum"]
    assert validate_json({"a": 1, "b": "z"}, schema)
    assert validate_json({"a": 1, "c": [1, "s", 3]}, schema)
    assert validate_json({"a": 1, "d": 5}, schema)
    assert validate_json("nope", schema) == ["$: expected object"]


def test_schema_strictness_depth_and_error_cap():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    assert validate_json({"a": "x", "extra": 1}, schema) == []  # lenient by default
    assert validate_json({"a": "x", "extra": 1}, schema, strict=True)
    open_schema = {**schema, "additionalProperties": True}
    assert validate_json({"extra": 1}, open_schema, strict=True) == []
    deep = {"type": "array", "items": {}}
    node = deep
    for _ in range(12):
        node["items"] = {"type": "array", "items": {}}
        node = node["items"]
    value: list = []
    cursor = value
    for _ in range(12):
        nested: list = []
        cursor.append(nested)
        cursor = nested
    assert any("too deep" in e for e in validate_json(value, deep))
    many = {"type": "object", "required": [f"k{i}" for i in range(50)]}
    assert len(validate_json({}, many)) <= 5
    assert validate_json({}, "not a schema")


def test_pattern_keyword_is_ignored_not_executed():
    assert validate_json("anything", {"type": "string", "pattern": "(a+)+$"}) == []


# ---- tool-call screening ----------------------------------------------------------

def test_valid_call_is_normalized_and_arguments_canonicalised():
    valid, issues = screen_tool_calls(
        [call(arguments={"title": "Plan", "limit": 3})], (TOOL,))
    assert issues == ()
    assert valid[0]["function"]["arguments"] == '{"title": "Plan", "limit": 3}'
    assert valid[0]["type"] == "function"


def test_unoffered_unknown_and_similar_names_are_rejected_exactly():
    valid, issues = screen_tool_calls(
        [call("read_notes"), call("READ_NOTE"), call("ping"), call(None)], (TOOL,))
    assert valid == ()
    assert reasons(issues) == ["tool was not offered for this request"] * 3 + ["missing tool name"]


def test_bad_arguments_are_rejected_without_echoing_values():
    secret = "SECRET-VALUE-123"
    valid, issues = screen_tool_calls([
        call(arguments="not json"),
        call(arguments="[1]"),
        call(arguments=json.dumps({"title": 5})),
        call(arguments=json.dumps({"title": secret, "limit": 99})),
        call(arguments=json.dumps({"title": "a", "extra": 1})),
        call(arguments=json.dumps({})),
        call(arguments="x" * 100_001),
    ], (TOOL,))
    assert valid == () and len(issues) == 7
    assert "SECRET" not in repr([i.to_dict() for i in issues])


def test_duplicate_ids_empty_arguments_and_call_cap():
    valid, issues = screen_tool_calls(
        [call("ping", "", "same"), call("ping", "{}", "same"), call("ping", "{}", "")], (OTHER,))
    assert [c["function"]["name"] for c in valid] == ["ping", "ping"]
    assert valid[1]["id"].startswith("call_") and reasons(issues) == ["duplicate tool call id"]
    many = [call("ping", "{}", f"id{i}") for i in range(12)]
    valid, issues = screen_tool_calls(many, (OTHER,))
    assert len(valid) == 8 and all("too many" in i.reason for i in issues)


def test_name_in_issues_is_sanitised_and_no_offered_tools_means_all_rejected():
    _, issues = screen_tool_calls([call("bad\x00name\n" + "x" * 100)], (TOOL,))
    assert "\x00" not in issues[0].name and len(issues[0].name) <= 60
    valid, issues = screen_tool_calls([call()], ())
    assert valid == () and len(issues) == 1


# ---- structured output parsing ----------------------------------------------------

SCHEMA = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}


def test_parse_structured_handles_fences_and_reports_problems():
    assert parse_structured('{"answer": "42"}', SCHEMA) == ({"answer": "42"}, None)
    assert parse_structured('```json\n{"answer": "42"}\n```', SCHEMA)[0] == {"answer": "42"}
    assert "not valid JSON" in parse_structured("Sure! here: {", SCHEMA)[1]
    assert "missing required" in parse_structured("{}", SCHEMA)[1]
    assert parse_structured("x" * 1_000_001, SCHEMA)[1] == "structured output exceeds the size limit"


# ---- gateway integration ----------------------------------------------------------

class Scripted:
    def __init__(self, provider_id, responses):
        self.provider_id = provider_id
        self.responses = list(responses)
        self.calls = 0

    async def list_models(self):
        return []

    async def complete(self, candidate, request):
        self.calls += 1
        return self.responses.pop(0)

    async def stream(self, candidate, request):
        yield StreamChunk(delta="hi")
        yield StreamChunk(finish_reason="tool_calls", tool_calls=(call("nope"), call("ping", "{}")))

    streams_tools = True


def cand(pid, rel=0.5):
    return ModelCandidate(pid, "m", frozenset({"chat", "tools"}), 8192, True, True, True,
                          reliability_score=rel)


def gw(*providers, **kw):
    registry = ProviderRegistry()
    for p in providers:
        registry.register(p)
    return ModelGateway(registry, **kw)


TOOL_REQ = ModelRequest(task_type="agent", prompt="x", tools=(TOOL, OTHER))


@pytest.mark.asyncio
async def test_annotate_policy_reports_issues_but_passes_calls_through():
    bad, good = call("ghost", "{}"), call("ping", "{}", "c2")
    p = Scripted("a", [ProviderResponse(content="", tool_calls=(bad, good))])
    result = await gw(p).complete(TOOL_REQ, [cand("a")])
    assert result.tool_calls == (bad, good)  # untouched: the broker decides
    assert [i["name"] for i in result.tool_call_issues] == ["ghost"]


@pytest.mark.asyncio
async def test_filter_policy_drops_invalid_calls_and_keeps_valid_ones():
    p = Scripted("a", [ProviderResponse(content="", tool_calls=(
        call("ghost", "{}"), call("ping", "{}", "c2")))])
    result = await gw(p, tool_call_policy="filter").complete(TOOL_REQ, [cand("a")])
    assert [c["function"]["name"] for c in result.tool_calls] == ["ping"]
    assert len(result.tool_call_issues) == 1


@pytest.mark.asyncio
async def test_filter_policy_falls_back_when_model_returns_only_invalid_calls():
    a = Scripted("a", [ProviderResponse(content="", tool_calls=(call("ghost", "{}"),))])
    b = Scripted("b", [ProviderResponse(content="", tool_calls=(call("ping", "{}"),))])
    g = gw(a, b, tool_call_policy="filter")
    result = await g.complete(TOOL_REQ, [cand("a", 0.9), cand("b", 0.1)])
    assert result.provider_id == "b" and result.attempted_candidates == ("a/m", "b/m")
    assert g.breaker.state("a") == "closed"  # model output failed, not the provider
    assert "invalid_output" in [e.kind for e in g.events.recent()]
    only_bad = Scripted("c", [ProviderResponse(content="", tool_calls=(call("ghost", "{}"),))])
    with pytest.raises(PermanentProviderError, match="No model produced a valid response"):
        await gw(only_bad, tool_call_policy="filter").complete(TOOL_REQ, [cand("c")])


def test_tool_call_policy_validation():
    with pytest.raises(ValueError):
        gw(tool_call_policy="trust")


@pytest.mark.asyncio
async def test_structured_output_validated_and_returned_parsed():
    p = Scripted("a", [ProviderResponse(content='```json\n{"answer": "yes"}\n```')])
    req = ModelRequest(task_type="extract", prompt="x", json_schema=SCHEMA)
    result = await gw(p).complete(req, [cand("a")])
    assert result.structured == {"answer": "yes"}


@pytest.mark.asyncio
async def test_invalid_structured_output_falls_back_to_next_model_then_fails_clearly():
    a = Scripted("a", [ProviderResponse(content="I think the answer is yes")])
    b = Scripted("b", [ProviderResponse(content='{"answer": "yes"}')])
    req = ModelRequest(task_type="extract", prompt="x", json_schema=SCHEMA)
    result = await gw(a, b).complete(req, [cand("a", 0.9), cand("b", 0.1)])
    assert result.provider_id == "b" and result.structured == {"answer": "yes"}
    c = Scripted("c", [ProviderResponse(content="{}")])
    with pytest.raises(PermanentProviderError, match="missing required"):
        await gw(c).complete(req, [cand("c")])


@pytest.mark.asyncio
async def test_stream_rejects_json_schema_and_screens_streamed_tool_calls():
    p = Scripted("a", [])
    g = gw(p)
    with pytest.raises(ValueError, match="complete"):
        async for _ in g.stream(ModelRequest(task_type="x", prompt="y", json_schema=SCHEMA),
                                [cand("a")]):
            pass
    events = [e async for e in g.stream(TOOL_REQ, [cand("a")])]
    assert [i["name"] for i in events[-1].tool_call_issues] == ["nope"]
    events = [e async for e in gw(p, tool_call_policy="filter").stream(TOOL_REQ, [cand("a")])]
    assert [c["function"]["name"] for c in events[-1].tool_calls] == ["ping"]


# ---- providers send the schema where supported -----------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_ollama_and_openai_providers_send_structured_output_request():
    route = respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": "{}"}, "done": True}))
    req = ModelRequest(task_type="x", prompt="y", json_schema=SCHEMA)
    c = ModelCandidate("ollama", "m", frozenset({"chat"}), 8192, True, True, True)
    await OllamaProvider().complete(c, req)
    assert json.loads(route.calls[0].request.content)["format"] == SCHEMA

    oai = respx.post("https://r.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]}))
    await OpenAICompatibleProvider("r", "https://r.example.com/v1").complete(c, req)
    assert "response_format" not in json.loads(oai.calls[0].request.content)  # off by default
    await OpenAICompatibleProvider("r", "https://r.example.com/v1",
                                   supports_json_schema=True).complete(c, req)
    sent = json.loads(oai.calls[1].request.content)["response_format"]
    assert sent["type"] == "json_schema" and sent["json_schema"]["schema"] == SCHEMA
