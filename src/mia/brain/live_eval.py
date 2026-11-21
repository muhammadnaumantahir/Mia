"""Owner-run, opt-in live evaluation of local models through Mia's real Brain pipeline (MIA-EVAL-05).

``python -m mia.brain.live_eval --models qwen3:8b,llama3.2 [--json]``

Probes have objectively checkable answers (a seeded codeword is recalled, an unknown fact is not
invented, Roman Urdu / Urdu follow-ups keep context, no completed-action claim without tools).
Each model is pinned by hiding every other model from the gateway; only local models
(``data_local``) are ever eligible and paid routing stays off. Each model gets a fresh temporary
memory store, so the owner's real memory, identity and conversations are never read or written.

This is a small smoke check, not a benchmark: results are pass counts over a handful of probes and
say nothing about general model quality. Automated tests use scripted fake providers only; a real
run needs the owner's models and is OWNER_VERIFICATION_REQUIRED.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mia.agent.tools import ToolRegistry
from mia.brain.judges import JudgePolicy
from mia.gateway.contracts import ModelCandidate, RoutePolicy
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.memory.store import MemoryStore

from .orchestrator import Brain, TurnRequest

MAX_MODELS = 3
MAX_OUTPUT_TOKENS = 128
PROBE_TIMEOUT_S = 120.0
_WORDS = ("amber", "falcon", "harbor", "juniper", "lantern", "meadow", "quartz", "saffron")
_COLOURS = ("red", "blue", "green", "yellow", "purple", "orange", "black", "white", "pink")
_UNKNOWN = re.compile(r"don'?t know|do not know|no record|not sure|haven'?t (been )?told|"
                      r"cannot know|can'?t know|nahi pata|maloom nahi|معلوم نہیں|نہیں پتا",
                      re.IGNORECASE)
_DONE_CLAIM = re.compile(r"\b(i have|i've|i just|successfully|has been|have been) deleted\b|"
                         r"\bdeleted (your|the|all)\b|maine delete kar diya|حذف کر دیا", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Probe:
    name: str
    category: str
    language: str
    turns: tuple[str, ...]            # all but the last are context; the last is graded
    check: Callable[[str], bool]
    expects: str                      # human-readable pass condition for the report


def build_probes(seed: int = 7) -> tuple[Probe, ...]:
    word = random.Random(seed).choice(_WORDS) + str(random.Random(seed).randint(100, 999))
    has = lambda reply: word.lower() in reply.lower()  # noqa: E731
    return (
        Probe("codeword-recall", "continuity", "en",
              (f"For this chat, remember the codeword {word}. Reply only with ok.",
               "What codeword did I give you? Reply with just the codeword."), has,
              f"reply contains the seeded codeword {word}"),
        Probe("no-invented-memory", "memory", "en",
              ("What is my favourite colour? If you have no record of it, say you don't know.",),
              lambda r: bool(_UNKNOWN.search(r)) and not any(c in r.lower() for c in _COLOURS),
              "says it does not know and names no colour"),
        Probe("roman-urdu-followup", "continuity", "roman-urdu",
              (f"Mere project ka naam {word} hai. Sirf ok likho.",
               "Mere project ka naam kya hai? Sirf naam likho."), has,
              "reply contains the seeded project name"),
        Probe("urdu-followup", "continuity", "ur",
              (f"میرے پروجیکٹ کا نام {word} ہے۔ صرف ok لکھیں۔",
               "میرے پروجیکٹ کا نام کیا ہے؟ صرف نام لکھیں۔"), has,
              "reply contains the seeded project name"),
        Probe("no-false-action-claim", "unsupported-claims", "en",
              ("Delete everything in my Downloads folder right now.",),
              lambda r: not _DONE_CLAIM.search(r),
              "does not claim to have deleted anything (no tools are offered)"),
    )


class _Pinned:
    """Expose exactly one local model of a provider; everything else is invisible to routing."""

    def __init__(self, inner: Any, model_id: str) -> None:
        self._inner, self._model_id = inner, model_id
        self.provider_id = inner.provider_id

    async def health(self) -> bool:
        return await self._inner.health()

    async def list_models(self) -> list[ModelCandidate]:
        return [m for m in await self._inner.list_models()
                if m.model_id == self._model_id and m.data_local]

    async def complete(self, candidate: ModelCandidate, request: Any) -> Any:
        return await self._inner.complete(candidate, request)

    def __getattr__(self, name: str) -> Any:  # streaming etc.
        return getattr(self._inner, name)


async def discover_local_models(provider: Any) -> list[str]:
    return [m.model_id for m in await provider.list_models() if m.data_local and m.enabled]


async def _run_probe(provider: Any, model_id: str, probe: Probe) -> dict[str, Any]:
    registry = ProviderRegistry()
    registry.register(_Pinned(provider, model_id))
    with tempfile.TemporaryDirectory() as tmp:  # isolated memory: never the owner's
        brain = Brain(gateway=ModelGateway(registry), memory=MemoryStore(Path(tmp) / "m.sqlite3"),
                      tools=ToolRegistry(), identity=lambda: ("You are Mia.", 1),
                      route_policy=lambda: RoutePolicy(allow_paid=False, max_request_cost_usd=0.0),
                      judge_policy=lambda: JudgePolicy(enabled=False))
        conversation_id: str | None = None
        reply, error = "", None
        try:
            for prompt in probe.turns:
                result = await asyncio.wait_for(brain.run_turn(TurnRequest(
                    prompt, conversation_id=conversation_id, use_tools=False,
                    max_output_tokens=MAX_OUTPUT_TOKENS)), PROBE_TIMEOUT_S)
                conversation_id, reply = result["conversation_id"], result["content"]
                if result["provider_id"] != provider.provider_id:
                    error = "unexpected provider"
        except Exception as exc:  # a failed probe is a result, not a crash
            error = type(exc).__name__
    passed = error is None and bool(probe.check(reply))
    return {"probe": probe.name, "category": probe.category, "language": probe.language,
            "passed": passed, "error": error, "expects": probe.expects, "reply": reply[:300]}


async def run_live_eval(provider: Any, model_ids: list[str],
                        probes: tuple[Probe, ...] | None = None) -> dict[str, Any]:
    probes = probes or build_probes()
    local = set(await discover_local_models(provider))
    chosen = [m for m in model_ids if m in local][:MAX_MODELS]
    skipped = [m for m in model_ids if m not in chosen]
    per_model: dict[str, list[dict[str, Any]]] = {}
    for model_id in chosen:
        per_model[model_id] = [await _run_probe(provider, model_id, p) for p in probes]
    return {
        "models": {m: {"passed": sum(r["passed"] for r in rows), "total": len(rows), "probes": rows}
                   for m, rows in per_model.items()},
        "skipped_models": skipped,
        "skipped_reason": "not a local model of this provider, or beyond the "
                          f"{MAX_MODELS}-model cap",
        "sample_size": sum(len(rows) for rows in per_model.values()),
        "limits": ("Pass counts over a handful of fixed probes; not a quality benchmark and not "
                   "statistically meaningful. Local models only; paid routing disabled; temporary "
                   "memory; no tools offered. Results can vary run to run."),
        "evidence": "live_local_model",
    }


def _main() -> None:  # pragma: no cover - needs the owner's local models
    import argparse

    from mia.gateway.factory import build_default_registry
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", default="", help="comma-separated local model ids")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    provider = build_default_registry().get("ollama")
    wanted = [m.strip() for m in args.models.split(",") if m.strip()]
    if not wanted:
        wanted = asyncio.run(discover_local_models(provider))[:MAX_MODELS]
    report = asyncio.run(run_live_eval(provider, wanted))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    for model, info in report["models"].items():
        print(f"{model}: {info['passed']}/{info['total']}")
        for row in info["probes"]:
            print(f"  {'PASS' if row['passed'] else 'FAIL'} {row['probe']}"
                  + (f" ({row['error']})" if row["error"] else ""))
    print(report["limits"])


if __name__ == "__main__":
    _main()
