from datetime import UTC, datetime, timedelta, timezone

import pytest

from mia.gateway.contracts import RoutePolicy
from mia.gateway.factory import apply_monthly_cap
from mia.gateway.ledger import UsageLedger


@pytest.mark.parametrize("raw", [None, "", "nan", "inf", "-1", "invalid"])
def test_monthly_cap_missing_or_invalid_fails_closed(monkeypatch, raw):
    monkeypatch.delenv("MIA_MAX_MONTHLY_SPEND_USD", raising=False)
    if raw is not None:
        monkeypatch.setenv("MIA_MAX_MONTHLY_SPEND_USD", raw)

    policy = apply_monthly_cap(RoutePolicy(allow_paid=True, max_request_cost_usd=2.0), 0.0)
    assert policy.allow_paid is False
    assert policy.max_request_cost_usd == 0.0


def test_monthly_cap_reduces_request_limit_to_remaining_monthly_budget(monkeypatch):
    monkeypatch.setenv("MIA_MAX_MONTHLY_SPEND_USD", "10")
    policy = apply_monthly_cap(RoutePolicy(allow_paid=True, max_request_cost_usd=4.0), 7.5)
    assert policy.allow_paid is True
    assert policy.max_request_cost_usd == pytest.approx(2.5)


@pytest.mark.parametrize("spent", [-1.0, float("nan"), float("inf")])
def test_invalid_monthly_spend_fails_closed(monkeypatch, spent):
    monkeypatch.setenv("MIA_MAX_MONTHLY_SPEND_USD", "10")
    policy = apply_monthly_cap(RoutePolicy(allow_paid=True, max_request_cost_usd=4.0), spent)
    assert policy.allow_paid is False


def test_monthly_cap_at_or_over_limit_disables_paid_routing(monkeypatch):
    monkeypatch.setenv("MIA_MAX_MONTHLY_SPEND_USD", "10")
    for spent in (10.0, 10.01):
        policy = apply_monthly_cap(RoutePolicy(allow_paid=True, max_request_cost_usd=4.0), spent)
        assert policy.allow_paid is False


def test_monthly_cap_does_not_change_free_only_policy(monkeypatch):
    monkeypatch.delenv("MIA_MAX_MONTHLY_SPEND_USD", raising=False)
    original = RoutePolicy(allow_paid=False, max_request_cost_usd=0.0)
    assert apply_monthly_cap(original, 0.0) is original


def test_month_spend_uses_utc_calendar_month_start(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.sqlite3")
    observed = []
    ledger.spend_since = lambda since: observed.append(since) or 3.25

    now = datetime(2026, 12, 1, 2, 0, tzinfo=timezone(timedelta(hours=4)))
    assert ledger.spend_month_utc(now) == pytest.approx(3.25)
    assert observed == [datetime(2026, 11, 1, 0, 0, tzinfo=UTC)]
