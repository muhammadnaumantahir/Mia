import pytest

from mia.gateway.factory import paid_route_policy


@pytest.mark.parametrize("cap", ["nan", "inf", "-inf", "-0.1", "not-a-number"])
def test_invalid_spend_cap_disables_paid_routes(monkeypatch, cap):
    monkeypatch.setenv("MIA_ALLOW_PAID", "true")
    monkeypatch.setenv("MIA_MAX_REQUEST_SPEND_USD", cap)

    policy = paid_route_policy()

    assert policy.allow_paid is False
    assert policy.max_request_cost_usd == 0.0


def test_valid_spend_cap_preserves_explicit_paid_opt_in(monkeypatch):
    monkeypatch.setenv("MIA_ALLOW_PAID", "true")
    monkeypatch.setenv("MIA_MAX_REQUEST_SPEND_USD", "0.025")

    policy = paid_route_policy()

    assert policy.allow_paid is True
    assert policy.max_request_cost_usd == 0.025
