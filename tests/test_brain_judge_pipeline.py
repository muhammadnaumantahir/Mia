"""Advisory judges inside the Brain turn pipeline (MIA-EVAL-03). Fake providers only.

These prove wiring and authority limits. They say nothing about live judge-model quality.
"""
import asyncio
import json

from fastapi.testclient import TestClient

from mia.agent.tools import ToolRegistry
from mia.brain.judges import JudgePolicy
from mia.brain.orchestrator import Brain, TurnRequest
from mia.gateway.contracts import ModelCandidate, ProviderResponse, StreamChunk
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

MEDIUM = "I think it is probably the cache, possibly, but I am not sure at all."
ASK = ("Compare the trade-offs of SQLite and PostgreSQL for a small billing service "
       "and explain your reasoning step by step.")


class Fake:
    def __init__(self, pid, *, reply=MEDIUM, verdict="supported", fail=False, local=True, hang=False):
        self.provider_id, self.reply, self.verdict, self.fail, self.local = pid, reply, verdict, fail, local
        self.hang = hang
        self.calls = self.judge_calls = 0

    async def health(self):
        return True

    async def list_models(self):
        return [ModelCandidate(self.provider_id, f"{self.provider_id}-model", frozenset({"chat"}),
                               16384, True, True, True, data_local=self.local)]

    async def complete(self, candidate, request):
        if request.task_type == "judge":
            self.judge_calls += 1
            if self.hang:
                await asyncio.sleep(30)
            if self.fail:
                raise RuntimeError("judge down")
            return ProviderResponse(content=json.dumps({"verdict": self.verdict, "reason": "r"}))
        self.calls += 1
        return ProviderResponse(content=self.reply)

    async def stream(self, candidate, request):
        self.calls += 1
        yield StreamChunk(delta=self.reply)
        yield StreamChunk(finish_reason="stop")


def client_for(tmp_path, monkeypatch, *providers, enable=True, **env):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    for name in ("MIA_JUDGES_ENABLED", "MIA_JUDGES_MAX", "MIA_JUDGES_ALLOW_REMOTE", "MIA_BRAIN_LOCAL_ONLY"):
        monkeypatch.delenv(name, raising=False)
    if enable:
        monkeypatch.setenv("MIA_JUDGES_ENABLED", "true")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    registry = ProviderRegistry()
    for p in providers:
        registry.register(p)
    app = create_app(store=ObservatoryStore(tmp_path / "obs.sqlite3"), gateway=ModelGateway(registry),
                     memory=MemoryStore(tmp_path / "memory.sqlite3"))
    return TestClient(app)


def chat(client, conv, prompt=ASK):
    r = client.post("/api/gateway/chat", json={"prompt": prompt, "use_tools": False,
                                               "conversation_id": conv})
    assert r.status_code == 200, r.text
    return r.json()


def judge_events(client):
    return [e for t in client.get("/api/brain/diagnostics").json()["turns"]
            for e in t.get("events", []) if e.get("event") == "judges"]


def trio(**kw):
    return [Fake("a", **kw), Fake("b", **kw), Fake("c", **kw)]


def test_off_by_default_no_judge_is_ever_called(tmp_path, monkeypatch):
    fakes = trio()
    client = client_for(tmp_path, monkeypatch, *fakes, enable=False)
    result = chat(client, "conv-j-off-1")
    assert sum(f.judge_calls for f in fakes) == 0
    assert "judges" not in result["verification"] and result["verification"]["confidence"] == "medium"
    assert client.get("/api/brain/judges").json()["stats"]["ran"] == 0


def test_unsupported_consensus_lowers_confidence_but_never_changes_the_reply(tmp_path, monkeypatch):
    fakes = trio(verdict="unsupported")
    client = client_for(tmp_path, monkeypatch, *fakes)
    result = chat(client, "conv-j-low-1")
    generator = next(f for f in fakes if f.provider_id == result["provider_id"])
    assert result["content"] == MEDIUM  # reply text untouched
    assert result["verification"]["confidence"] == "low"
    j = result["verification"]["judges"]
    assert j["consensus"] == "unsupported" and j["advisory"] is True and j["overrides_policy"] is False
    assert generator.judge_calls == 0  # the answering model never judges itself
    assert sorted(f.judge_calls for f in fakes) == [0, 1, 1]
    assert judge_events(client)[0]["consensus"] == "unsupported"
    stats = client.get("/api/brain/judges").json()["stats"]
    assert stats["ran"] == 1 and stats["lowered_confidence"] == 1


def test_supported_consensus_never_raises_confidence(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch, *trio(verdict="supported"))
    result = chat(client, "conv-j-keep-1")
    assert result["verification"]["judges"]["consensus"] == "supported"
    assert result["verification"]["confidence"] == "medium"  # the verifier's value stands


def test_failing_judges_do_not_break_the_turn_and_fall_back(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch, *trio(fail=True))
    result = chat(client, "conv-j-fail-1")
    assert result["content"] == MEDIUM and result["verification"]["confidence"] == "medium"
    j = result["verification"]["judges"]
    assert j["consensus"] == "insufficient" and j["fallback"] == "deterministic_verifier"


def test_simple_turns_are_not_judged(tmp_path, monkeypatch):
    fakes = trio(reply="Hello!")
    client = client_for(tmp_path, monkeypatch, *fakes)
    result = chat(client, "conv-j-simple-1", prompt="hi")
    assert sum(f.judge_calls for f in fakes) == 0 and "judges" not in result["verification"]
    assert client.get("/api/brain/judges").json()["stats"]["skipped"] >= 1


def test_private_turns_never_use_remote_judges_but_public_turns_may_when_opted_in(tmp_path, monkeypatch):
    remote = Fake("r", local=False)
    locals_ = [Fake("a"), Fake("b"), Fake("c")]
    client = client_for(tmp_path, monkeypatch, *locals_, remote, MIA_JUDGES_ALLOW_REMOTE="true",
                        MIA_JUDGES_MAX="3", MIA_BRAIN_LOCAL_ONLY="true")
    chat(client, "conv-j-priv-1")
    assert remote.judge_calls == 0  # owner is local-only: judges inherit that

    remote2 = Fake("r", local=False)
    fresh = tmp_path / "fresh"  # no stored memory, so the turn is not private
    fresh.mkdir()
    client = client_for(fresh, monkeypatch, Fake("a"), Fake("b"), remote2,
                        MIA_JUDGES_ALLOW_REMOTE="true", MIA_JUDGES_MAX="3")
    result = chat(client, "conv-j-pub-1")
    votes = [v["judge"] for v in result["verification"]["judges"]["votes"]]
    assert "r/r-model" in votes or result["provider_id"] == "r"  # opted-in remote judge may vote
    assert result["provider_id"] not in {v.split("/")[0] for v in votes}  # never self-judging


def test_remote_judges_are_refused_without_explicit_opt_in(tmp_path, monkeypatch):
    remote = Fake("r", local=False)
    client = client_for(tmp_path, monkeypatch, Fake("a"), Fake("b"), remote, MIA_JUDGES_MAX="3")
    chat(client, "conv-j-noremote-1")
    assert remote.judge_calls == 0


def test_judges_endpoint_is_read_only_and_reflects_policy(tmp_path, monkeypatch):
    client = client_for(tmp_path, monkeypatch, *trio(), enable=False)
    off = client.get("/api/brain/judges").json()
    assert off["policy"]["enabled"] is False and off["policy"]["allow_paid"] is False
    assert off["policy"]["hard_max_judges"] == 3 and "advisory" in off["policy"]["authority"]
    assert client.post("/api/brain/judges", json={}).status_code == 405
    on = client_for(tmp_path, monkeypatch, *trio(), MIA_JUDGES_MAX="9").get("/api/brain/judges").json()
    assert on["policy"]["enabled"] is True and on["policy"]["max_judges"] == 3


def brain_for(tmp_path, *providers):
    registry = ProviderRegistry()
    for p in providers:
        registry.register(p)
    memory = MemoryStore(tmp_path / "m.sqlite3")
    brain = Brain(gateway=ModelGateway(registry), memory=memory, tools=ToolRegistry(),
                  identity=lambda: ("You are Mia.", 1), judge_policy=lambda: JudgePolicy(enabled=True))
    return brain, memory


async def test_streamed_text_turn_is_judged_after_the_reply_is_stored(tmp_path):
    brain, memory = brain_for(tmp_path, *trio(verdict="unsupported"))
    events = [e async for e in brain.stream_turn(
        TurnRequest(ASK, conversation_id="conv-j-stream-1", use_tools=False))]
    done = next(payload for name, payload in events if name == "done")
    assert done["verification"]["judges"]["consensus"] == "unsupported"
    assert done["verification"]["confidence"] == "low"
    replies = [m for m in memory.get_messages("conv-j-stream-1", limit=10) if m["role"] == "assistant"]
    assert [m["content"] for m in replies] == [MEDIUM]


async def test_disconnect_while_judging_keeps_the_stored_reply_without_a_duplicate(tmp_path):
    brain, memory = brain_for(tmp_path, *[Fake(x, hang=True) for x in "abc"])
    gen = brain.stream_turn(TurnRequest(ASK, conversation_id="conv-j-cancel-1", use_tools=False))
    assert (await gen.__anext__())[0] == "delta"
    waiting = asyncio.ensure_future(gen.__anext__())  # now inside the judge wait
    await asyncio.sleep(0.2)
    waiting.cancel()
    try:
        await waiting
    except asyncio.CancelledError:
        pass
    replies = [m["content"] for m in memory.get_messages("conv-j-cancel-1", limit=10)
               if m["role"] == "assistant"]
    assert replies == [MEDIUM]  # stored once, never "[response interrupted]"


def test_voice_turns_are_never_judged(tmp_path, monkeypatch):
    fakes = trio(verdict="unsupported")
    client = client_for(tmp_path, monkeypatch, *fakes)
    r = client.post("/api/gateway/chat", json={"prompt": ASK, "use_tools": False,
                                               "conversation_id": "conv-j-voice-1", "interface": "voice"})
    assert r.status_code == 200
    assert sum(f.judge_calls for f in fakes) == 0 and "judges" not in r.json()["verification"]
    assert "voice turn: latency" in client.get("/api/brain/judges").json()["stats"]["top_skip_reasons"]
