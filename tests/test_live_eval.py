"""Live-eval harness (MIA-EVAL-05) with scripted fake providers only. No live model is involved."""

import re

from mia.brain.live_eval import (
    MAX_MODELS,
    build_probes,
    discover_local_models,
    run_live_eval,
)
from mia.gateway.contracts import ModelCandidate, ProviderResponse

_WORD = re.compile(r"\b[a-z]+\d{3}\b")


class Fake:
    provider_id = "fake"

    def __init__(self, behaviour: dict[str, str], local: dict[str, bool] | None = None) -> None:
        self.behaviour = behaviour  # model_id -> "good" | "bad"
        self.local = local or {m: True for m in behaviour}
        self.used: list[str] = []

    async def health(self) -> bool:
        return True

    async def list_models(self) -> list[ModelCandidate]:
        return [ModelCandidate("fake", m, frozenset({"chat"}), 8192, True, True, True,
                               data_local=self.local[m]) for m in self.behaviour]

    async def complete(self, candidate: ModelCandidate, request) -> ProviderResponse:
        self.used.append(candidate.model_id)
        users = [str(m.get("content")) for m in request.messages if m.get("role") == "user"]
        last = users[-1]
        if self.behaviour[candidate.model_id] == "bad":
            return ProviderResponse(content="Sure! I have deleted everything. Your colour is red.")
        earlier = [w for u in users[:-1] for w in _WORD.findall(u)]
        if earlier and not _WORD.findall(last):
            return ProviderResponse(content=earlier[0])
        if "colour" in last:
            return ProviderResponse(content="I don't know your favourite colour.")
        if "Delete" in last:
            return ProviderResponse(content="I can't delete files from here.")
        return ProviderResponse(content="ok")


async def test_good_model_passes_every_probe_and_bad_model_fails_them():
    provider = Fake({"good": "good", "bad": "bad"})
    report = await run_live_eval(provider, ["good", "bad"])
    assert report["models"]["good"]["passed"] == report["models"]["good"]["total"] == 5
    bad = {r["probe"]: r["passed"] for r in report["models"]["bad"]["probes"]}
    assert not bad["codeword-recall"] and not bad["no-invented-memory"]
    assert not bad["roman-urdu-followup"] and not bad["urdu-followup"]
    assert not bad["no-false-action-claim"]


async def test_each_model_is_pinned_so_only_that_model_answers():
    for pinned in ("a", "b"):  # whichever the router would prefer, only the pinned model answers
        provider = Fake({"a": "good", "b": "good"})
        await run_live_eval(provider, [pinned])
        assert set(provider.used) == {pinned}


async def test_remote_models_and_models_beyond_the_cap_are_never_run():
    models = {f"m{i}": "good" for i in range(MAX_MODELS + 2)}
    local = {**{m: True for m in models}, "m0": False}
    provider = Fake(models, local)
    assert "m0" not in await discover_local_models(provider)
    report = await run_live_eval(provider, list(models))
    assert "m0" not in provider.used and "m0" in report["skipped_models"]
    assert len(report["models"]) == MAX_MODELS


async def test_report_is_honest_about_sample_size_and_limits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # nothing may be written to the working directory
    report = await run_live_eval(Fake({"good": "good"}), ["good"])
    assert report["sample_size"] == len(build_probes()) == 5
    assert report["evidence"] == "live_local_model"
    assert "not a quality benchmark" in report["limits"]
    assert list(tmp_path.iterdir()) == []


async def test_failures_are_reported_not_raised():
    class Broken(Fake):
        async def complete(self, candidate, request):
            raise RuntimeError("boom")

    report = await run_live_eval(Broken({"m": "good"}), ["m"])
    rows = report["models"]["m"]["probes"]
    assert report["models"]["m"]["passed"] == 0 and all(r["error"] for r in rows)


def test_probes_are_seeded_and_deterministic():
    assert [p.turns for p in build_probes(7)] == [p.turns for p in build_probes(7)]
    assert [p.turns for p in build_probes(7)] != [p.turns for p in build_probes(8)]
