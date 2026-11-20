from types import SimpleNamespace

from mia.gateway.contracts import ModelCandidate
from mia.life.reflect import Reflector
from mia.life.runner import ExplorationResult, LifeRunner
from mia.life.store import LifeStore
from mia.memory.store import MemoryStore

NOW_RESOURCES = {"cpu_percent": 5.0, "memory_percent": 12.0}


class LocalProvider:
    provider_id = "ollama"

    def __init__(self, base_url="http://127.0.0.1:11434"):
        self.base_url = base_url
        self.calls = 0

    async def list_models(self):
        self.calls += 1
        return [
            ModelCandidate(
                provider_id="ollama", model_id="local-safe", capabilities=frozenset({"chat"}),
                context_window=8192, free_tier=True, healthy=True, enabled=True, data_local=True,
            ),
            ModelCandidate(
                provider_id="ollama", model_id="not-local", capabilities=frozenset({"chat"}),
                context_window=8192, free_tier=True, healthy=True, enabled=True, data_local=False,
            ),
            ModelCandidate(
                provider_id="ollama", model_id="paid-local", capabilities=frozenset({"chat"}),
                context_window=8192, free_tier=False, healthy=True, enabled=True, data_local=True,
            ),
        ]


class FakeGateway:
    def __init__(self, provider):
        self.provider = provider
        self.providers = SimpleNamespace(get=lambda provider_id: self.provider)
        self.model_registry = None
        self.calls = []

    async def complete(self, request, candidates, policy):
        self.calls.append((request, candidates, policy))
        return SimpleNamespace(
            provider_id="ollama",
            model_id="local-safe",
            structured={
                "hypotheses": ["Retries may explain the repeated transient failures."],
                "suggested_followups": ["Compare the next three failure codes; do not change retry policy yet."],
            },
            usage=None,
            cost_usd=0.0,
            free_tier=True,
            data_local=True,
            latency_ms=5.0,
        )


class CompletedExplorer:
    name = "test"
    permission_level = 0

    def explore(self, item, context):
        return ExplorationResult(
            outcome="completed",
            item_state="done",
            summary="Reviewed existing local evidence.",
            actions=["memory.search:records"],
        )


def test_opt_in_model_reflection_uses_only_loopback_free_local_models(tmp_path):
    life = LifeStore(tmp_path / "life.sqlite3")
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    life.update_settings({"min_interval_seconds": 0})
    life.add_item(
        title="Review a recurring scheduler failure",
        why_it_matters="The same failure code has appeared several times.",
        origin="owner",
    )
    provider = LocalProvider()
    gateway = FakeGateway(provider)
    usage = []
    reflector = Reflector(
        life,
        every=1,
        gateway=gateway,
        usage_recorder=lambda *args, **kwargs: usage.append((args, kwargs)),
        model_enabled=True,
        model_timeout_seconds=5,
    )
    runner = LifeRunner(
        life,
        memory,
        CompletedExplorer(),
        resource_snapshot=lambda: dict(NOW_RESOURCES),
        reflector=reflector,
    )

    result = runner.run_cycle("owner")
    assert result["status"] == "completed"
    assert provider.calls == 1
    assert len(gateway.calls) == 1
    request, candidates, policy = gateway.calls[0]
    assert {candidate.model_id for candidate in candidates} == {"local-safe"}
    assert request.sensitive is True
    assert request.json_schema is not None
    assert request.tools == ()
    assert policy.allow_paid is False
    assert len(usage) == 1

    [reflection] = life.list_reflections()
    assert reflection["cycles_reviewed"] == 1
    assert any("AI hypothesis (unverified; local Ollama model local-safe)" in line
               for line in reflection["observations"])
    assert any("Suggested follow-up (not executed)" in line for line in reflection["observations"])
    assert reflection["evidence"] == [result["cycle_id"]]
    assert memory.list_items() == []
    assert life.list_outreach() == []


def test_model_reflection_is_disabled_by_default(tmp_path):
    life = LifeStore(tmp_path / "life.sqlite3")
    gateway = FakeGateway(LocalProvider())
    reflector = Reflector(life, gateway=gateway)

    result = reflector._model_observations([], ["No new cycles."])
    assert result == []
    assert gateway.calls == []


def test_model_reflection_refuses_a_non_loopback_ollama_endpoint(tmp_path):
    life = LifeStore(tmp_path / "life.sqlite3")
    provider = LocalProvider(base_url="https://remote-ollama.example/v1")
    gateway = FakeGateway(provider)
    reflector = Reflector(life, gateway=gateway, model_enabled=True)

    result = reflector._model_observations([], ["Only deterministic evidence."])
    assert result == []
    assert provider.calls == 0
    assert gateway.calls == []
