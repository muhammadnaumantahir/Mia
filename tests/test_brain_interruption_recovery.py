"""MIA-M1-12: interruption / failure recovery for streamed turns.

Fake providers only. This is not evidence about real voice barge-in, real microphones
or real provider latency (OWNER_VERIFICATION_REQUIRED).
"""

import asyncio

import pytest

from mia.agent.tools import ToolRegistry
from mia.brain.orchestrator import Brain, TurnRequest
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.memory.store import MemoryStore
from tests.test_brain_pipeline import Scripted


def _brain(tmp_path, provider):
    memory = MemoryStore(tmp_path / "m.sqlite3")
    registry = ProviderRegistry()
    registry.register(provider)
    brain = Brain(gateway=ModelGateway(registry), memory=memory, tools=ToolRegistry(),
                  identity=lambda: ("You are Mia.", 1))
    return brain, memory


def _assistant_messages(memory, conv):
    return [m["content"] for m in memory.get_messages(conv, limit=50) if m["role"] == "assistant"]


async def _drain(gen):
    return [event async for event in gen]


async def test_failure_after_reply_was_stored_does_not_store_a_second_interrupted_copy(tmp_path, monkeypatch):
    brain, memory = _brain(tmp_path, Scripted(stream_parts=["Hello ", "there"]))

    def boom(*args, **kwargs):
        raise RuntimeError("bookkeeping failed after the reply was stored")

    monkeypatch.setattr(brain, "_complete_event", boom)
    events = await _drain(brain.stream_turn(TurnRequest(
        "Tell me about the weather today", conversation_id="conv-post-store-1", use_tools=False)))
    replies = _assistant_messages(memory, "conv-post-store-1")
    assert len(replies) == 1, replies
    assert "[response interrupted]" not in replies[0]
    error = [data for name, data in events if name == "error"]
    assert error and error[0]["partial"] is False  # the owner already has a complete, stored reply


async def test_cancel_while_waiting_after_store_keeps_exactly_one_complete_reply(tmp_path, monkeypatch):
    brain, memory = _brain(tmp_path, Scripted(stream_parts=["Done ", "text"]))
    gate = asyncio.Event()

    async def slow_judge(p, final, provider_id, model_id, report):
        await gate.wait()
        return report

    monkeypatch.setattr(brain, "_maybe_judge", slow_judge)
    gen = brain.stream_turn(TurnRequest(
        "Tell me about the weather today", conversation_id="conv-judge-wait-1", use_tools=False))
    task = asyncio.ensure_future(_drain(gen))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if _assistant_messages(memory, "conv-judge-wait-1"):
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    replies = _assistant_messages(memory, "conv-judge-wait-1")
    assert replies == ["Done text"]


async def test_interrupt_before_any_text_stores_no_assistant_message(tmp_path):
    brain, memory = _brain(tmp_path, Scripted(stream_parts=["a ", "b "]))
    gen = brain.stream_turn(TurnRequest(
        "Tell me about the weather today", conversation_id="conv-early-1", use_tools=False))
    await gen.aclose()  # closed before the first delta was ever requested
    assert _assistant_messages(memory, "conv-early-1") == []


async def test_second_interruption_of_same_conversation_stays_usable_and_ordered(tmp_path):
    brain, memory = _brain(tmp_path, Scripted(stream_parts=["x ", "y ", "z "]))
    for turn in range(2):
        gen = brain.stream_turn(TurnRequest(
            f"Tell me about topic number {turn}", conversation_id="conv-twice-1", use_tools=False,
            interface="voice"))
        assert (await gen.__anext__())[0] == "delta"
        await gen.aclose()
    replies = _assistant_messages(memory, "conv-twice-1")
    assert len(replies) == 2 and all(r.endswith("[response interrupted]") for r in replies)
    again = await brain.run_turn(TurnRequest(
        "Okay, continue please", conversation_id="conv-twice-1", use_tools=False))
    assert again["content"]
