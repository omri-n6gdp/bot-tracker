from datetime import date, timedelta

import pytest
from hypothesis import given, settings, strategies as st

from tracker.engine import compute_daily, live_unit_price
from tracker import stats

D = date(2026, 1, 5)


def d(n):  # n days after D
    return D + timedelta(days=n)


def test_roadmap_worked_example():
    # Start 1,000; +10%; top up 550; -20%  ->  value 1,320 but the bot made -12%
    closes = [(d(0), 1000.0), (d(1), 1100.0), (d(2), 1650.0), (d(3), 1320.0)]
    flows = [(d(2), 550.0)]
    rows = compute_daily(closes, flows)
    assert [round(r.unit_price, 6) for r in rows] == [1.0, 1.1, 1.1, 0.88]
    assert [round(r.units, 6) for r in rows] == [1000, 1000, 1500, 1500]
    assert rows[2].flow == 550.0
    assert rows[-1].unit_price / rows[0].unit_price - 1 == pytest.approx(-0.12)


def test_withdrawal_cancels_units_and_keeps_price():
    closes = [(d(0), 1000.0), (d(1), 1200.0), (d(2), 600.0)]
    flows = [(d(2), -600.0)]  # took half out on a flat day
    rows = compute_daily(closes, flows)
    assert rows[2].unit_price == pytest.approx(1.2)
    assert rows[2].units == pytest.approx(500)


def test_two_flows_same_day_are_combined():
    closes = [(d(0), 1000.0), (d(1), 1300.0)]
    flows = [(d(1), 100.0), (d(1), 200.0)]  # no market move
    rows = compute_daily(closes, flows)
    assert rows[1].unit_price == pytest.approx(1.0)
    assert rows[1].units == pytest.approx(1300)
    assert rows[1].flow == pytest.approx(300)


def test_flow_on_non_trading_day_applies_at_next_close():
    closes = [(d(0), 1000.0), (d(3), 1500.0)]
    flows = [(d(1), 500.0)]  # weekend top-up
    rows = compute_daily(closes, flows)
    assert rows[1].unit_price == pytest.approx(1.0)
    assert rows[1].flow == 500.0


def test_flows_before_first_close_are_starting_capital():
    rows = compute_daily([(d(5), 2000.0)], [(d(0), 1500.0), (d(2), 500.0)])
    assert rows[0].unit_price == 1.0 and rows[0].units == pytest.approx(2000)


def test_dividend_raises_price_not_units():
    # a reinvested dividend is not a contribution: value rises, units stay
    rows = compute_daily([(d(0), 1000.0), (d(1), 1010.0)], [])
    assert rows[1].units == pytest.approx(1000)
    assert rows[1].unit_price == pytest.approx(1.01)


def test_live_price_excludes_pending_flows():
    rows = compute_daily([(d(0), 1000.0), (d(1), 1100.0)], [])
    # live value 1,650 includes a 550 top-up that hasn't been priced at a close yet
    assert live_unit_price(1650.0, rows[-1], pending_flow=550.0) == pytest.approx(1.1)


def test_empty_inputs():
    assert compute_daily([], []) == []


@settings(max_examples=200, deadline=None)
@given(
    st.lists(st.floats(min_value=-0.2, max_value=0.2), min_size=2, max_size=40),
    st.lists(st.tuples(st.integers(0, 39), st.floats(min_value=1, max_value=5000)), max_size=6),
)
def test_unit_price_equals_compounded_market_returns(rets, deposits):
    """Property: deposits never change the unit price; only market moves do."""
    value, expected_price = 1000.0, 1.0
    closes, flows = [(d(0), value)], []
    dep = {}
    for day, amt in deposits:
        if 0 < day < len(rets):
            dep[day] = dep.get(day, 0) + amt
    for i, r in enumerate(rets[1:], start=1):
        value *= 1 + r
        expected_price *= 1 + r
        if i in dep:
            value += dep[i]
            flows.append((d(i), dep[i]))
        closes.append((d(i), value))
    rows = compute_daily(closes, flows)
    assert rows[-1].unit_price == pytest.approx(expected_price, rel=1e-9)
    # and replaying is deterministic
    assert compute_daily(closes, flows) == rows


# ---- stats ---------------------------------------------------------------

def test_max_drawdown():
    assert stats.max_drawdown([1.0, 1.2, 0.9, 1.1, 0.6, 1.3]) == pytest.approx(0.6 / 1.2 - 1)
    assert stats.max_drawdown([1.0, 1.1, 1.2]) == 0.0


def test_period_return_uses_last_close_on_or_before_start():
    series = [(d(0), 1.0), (d(7), 1.1), (d(14), 1.21)]
    assert stats.return_since(series, d(10), current=1.21) == pytest.approx(0.1)
    assert stats.return_since(series, d(-5), current=1.21) == pytest.approx(0.21)  # before start -> inception


def test_volatility_and_sharpe_zero_for_flat_series():
    assert stats.annualised_volatility([1.0] * 10) == 0.0
    assert stats.sharpe([1.0] * 10) is None


def test_monthly_returns():
    series = [(date(2026, 1, 30), 1.0), (date(2026, 2, 27), 1.1), (date(2026, 3, 31), 0.99)]
    m = stats.monthly_returns(series)
    assert m["2026-02"] == pytest.approx(0.1)
    assert m["2026-03"] == pytest.approx(-0.1)


def test_rebalance_win_rate():
    series = [(d(0), 1.0), (d(1), 1.1), (d(2), 1.05), (d(3), 1.2)]
    # rebalances at d0 (then +5%), d2 (then +14%) -> 2/2 wins; at d1 (-4.5% to d2) -> loss
    assert stats.rebalance_outcomes(series, [d(0), d(2)], current=1.2) == [pytest.approx(0.05), pytest.approx(1.2 / 1.05 - 1)]
    assert stats.rebalance_outcomes(series, [d(1)], current=1.2)[0] == pytest.approx(1.2 / 1.1 - 1)
