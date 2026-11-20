from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from mia.gateway.contracts import ModelCandidate, ModelRequest, RoutePolicy
from mia.gateway.ledger import UsageLedger
from mia.gateway.providers import PermanentProviderError
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway


def test_atomic_budget_reservations_do_not_overbook_concurrent_requests(tmp_path):
    ledger = UsageLedger(tmp_path / "reservations.sqlite3")
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

    def reserve(_):
        return ledger.reserve_budget(
            provider_id="cloud",
            model_id="model",
            reserved_usd=0.01,
            per_request_limit_usd=0.01,
            daily_limit_usd=0.05,
            monthly_limit_usd=0.05,
            now=now,
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(reserve, range(20)))

    accepted = [item for item in results if item]
    assert len(accepted) == 5
    state = ledger.reservation_summary()
    assert state["active_count"] == 5
    assert state["active_reserved_usd"] == pytest.approx(0.05)


def test_usage_record_consumes_its_reservation_atomically(tmp_path):
    ledger = UsageLedger(tmp_path / "record-reservation.sqlite3")
    reservation = ledger.reserve_budget(
        provider_id="cloud", model_id="model", reserved_usd=0.02,
        per_request_limit_usd=0.05, daily_limit_usd=1.0, monthly_limit_usd=5.0,
    )
    assert reservation
    assert ledger.reservation_summary()["active_count"] == 1

    ledger.record(
        provider_id="cloud", model_id="model", task_type="chat",
        input_tokens=100, output_tokens=200, estimated_input_tokens=100,
        estimated_output_tokens=200, cost_usd=0.015, free_tier=False,
        data_local=False, reservation_id=reservation,
    )
    assert ledger.reservation_summary()["active_count"] == 0
    assert ledger.spend_today_utc() == pytest.approx(0.015)


def test_releasing_failed_attempt_frees_reserved_budget(tmp_path):
    ledger = UsageLedger(tmp_path / "release-reservation.sqlite3")
    reservation = ledger.reserve_budget(
        provider_id="cloud", model_id="model", reserved_usd=0.02,
        per_request_limit_usd=0.05, daily_limit_usd=0.02, monthly_limit_usd=0.02,
    )
    assert reservation
    assert ledger.release_reservation(reservation)
    second = ledger.reserve_budget(
        provider_id="cloud", model_id="model", reserved_usd=0.02,
        per_request_limit_usd=0.05, daily_limit_usd=0.02, monthly_limit_usd=0.02,
    )
    assert second


def test_gateway_keeps_reservation_until_usage_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_MAX_DAILY_SPEND_USD", "1")
    monkeypatch.setenv("MIA_MAX_MONTHLY_SPEND_USD", "5")

    class PaidProvider:
        provider_id = "cloud"

        async def health(self):
            return True

        async def list_models(self):
            return [candidate]

        async def complete(self, candidate, request):
            return "completed"

    candidate = ModelCandidate(
        provider_id="cloud", model_id="model", capabilities=frozenset({"chat"}),
        context_window=4096, free_tier=False, healthy=True, enabled=True,
        input_cost_per_million_usd=1.0, output_cost_per_million_usd=1.0,
    )
    registry = ProviderRegistry()
    registry.register(PaidProvider())
    ledger = UsageLedger(tmp_path / "gateway-reservations.sqlite3")
    gateway = ModelGateway(registry)
    gateway.usage_ledger = ledger
    gateway.require_budget_reservations = True
    request = ModelRequest(
        task_type="chat", prompt="hi", estimated_input_tokens=100,
        max_output_tokens=100,
    )

    import asyncio
    result = asyncio.run(gateway.complete(
        request, [candidate], RoutePolicy(allow_paid=True, max_request_cost_usd=0.001)
    ))
    assert result.reservation_id
    assert ledger.reservation_summary()["active_count"] == 1
    ledger.record(
        provider_id=result.provider_id, model_id=result.model_id, task_type="chat",
        input_tokens=None, output_tokens=None, estimated_input_tokens=100,
        estimated_output_tokens=100, cost_usd=result.cost_usd,
        free_tier=False, data_local=False, reservation_id=result.reservation_id,
    )
    assert ledger.reservation_summary()["active_count"] == 0


def test_gateway_releases_reservation_when_provider_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_MAX_DAILY_SPEND_USD", "1")
    monkeypatch.setenv("MIA_MAX_MONTHLY_SPEND_USD", "5")

    class FailingProvider:
        provider_id = "cloud"

        async def health(self):
            return True

        async def list_models(self):
            return [candidate]

        async def complete(self, candidate, request):
            raise PermanentProviderError("failed")

    candidate = ModelCandidate(
        provider_id="cloud", model_id="model", capabilities=frozenset({"chat"}),
        context_window=4096, free_tier=False, healthy=True, enabled=True,
        input_cost_per_million_usd=1.0, output_cost_per_million_usd=1.0,
    )
    registry = ProviderRegistry()
    registry.register(FailingProvider())
    ledger = UsageLedger(tmp_path / "failed-gateway-reservations.sqlite3")
    gateway = ModelGateway(registry)
    gateway.usage_ledger = ledger
    gateway.require_budget_reservations = True
    request = ModelRequest(task_type="chat", prompt="hi", estimated_input_tokens=100, max_output_tokens=100)

    import asyncio
    with pytest.raises(PermanentProviderError):
        asyncio.run(gateway.complete(
            request, [candidate], RoutePolicy(allow_paid=True, max_request_cost_usd=0.001)
        ))
    assert ledger.reservation_summary()["active_count"] == 0


def test_budget_reservation_rejects_unknown_or_insufficient_caps(tmp_path):
    ledger = UsageLedger(tmp_path / "invalid-reservations.sqlite3")
    assert ledger.reserve_budget(
        provider_id="p", model_id="m", reserved_usd=0.01,
        per_request_limit_usd=0.01, daily_limit_usd=float("nan"), monthly_limit_usd=1,
    ) is None
    assert ledger.reserve_budget(
        provider_id="p", model_id="m", reserved_usd=0.02,
        per_request_limit_usd=0.01, daily_limit_usd=1, monthly_limit_usd=1,
    ) is None


def test_unknown_paid_cost_does_not_consume_reservation(tmp_path):
    ledger = UsageLedger(tmp_path / "unknown-cost.sqlite3")
    reservation = ledger.reserve_budget(
        provider_id="cloud", model_id="model", reserved_usd=0.02,
        per_request_limit_usd=0.05, daily_limit_usd=1.0, monthly_limit_usd=5.0,
    )
    assert reservation
    with pytest.raises(ValueError, match="unknown cost"):
        ledger.record(
            provider_id="cloud", model_id="model", task_type="chat",
            input_tokens=10, output_tokens=10, estimated_input_tokens=10,
            estimated_output_tokens=10, cost_usd=None, free_tier=False,
            data_local=False, reservation_id=reservation,
        )
    assert ledger.reservation_summary()["active_count"] == 1


def test_token_usage_rejects_negative_or_boolean_provider_counts():
    from mia.gateway.contracts import TokenUsage

    with pytest.raises(ValueError):
        TokenUsage(input_tokens=-1, output_tokens=10)
    with pytest.raises(ValueError):
        TokenUsage(input_tokens=True, output_tokens=10)


def test_cancelled_paid_stream_retains_budget_reservation(tmp_path, monkeypatch):
    import asyncio

    from mia.gateway.contracts import StreamChunk

    monkeypatch.setenv("MIA_MAX_DAILY_SPEND_USD", "1")
    monkeypatch.setenv("MIA_MAX_MONTHLY_SPEND_USD", "5")

    candidate = ModelCandidate(
        provider_id="cloud", model_id="model", capabilities=frozenset({"chat"}),
        context_window=4096, free_tier=False, healthy=True, enabled=True,
        input_cost_per_million_usd=1.0, output_cost_per_million_usd=1.0,
    )

    class StreamingProvider:
        provider_id = "cloud"

        async def health(self):
            return True

        async def list_models(self):
            return [candidate]

        async def complete(self, candidate, request):
            return "unused"

        async def stream(self, candidate, request):
            yield StreamChunk(delta="partial")
            await asyncio.Event().wait()

    async def scenario():
        registry = ProviderRegistry()
        registry.register(StreamingProvider())
        ledger = UsageLedger(tmp_path / "cancelled-stream.sqlite3")
        gateway = ModelGateway(registry)
        gateway.usage_ledger = ledger
        gateway.require_budget_reservations = True
        request = ModelRequest(task_type="chat", prompt="hello", estimated_input_tokens=100,
                               max_output_tokens=100)
        stream = gateway.stream(
            request, [candidate], RoutePolicy(allow_paid=True, max_request_cost_usd=0.001)
        )
        first = await anext(stream)
        assert first.delta == "partial"
        await stream.aclose()
        assert ledger.reservation_summary()["active_count"] == 1

    asyncio.run(scenario())
