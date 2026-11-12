"""Uninvested cash in the pie counts in the value and the return; moving it around doesn't."""
from datetime import datetime

import pytest

from tracker import service
from tracker.config import Settings
from tracker.db import BotTrade, PieSnapshot, make_session_factory


def _pie(tmp_path, days):
    """days: (holdings value, cash, bot buys that day in GBP) per weekday from Mon 2 Nov 2026."""
    url = f"sqlite:///{tmp_path / 'c.db'}"
    S = make_session_factory(url)
    with S() as s:
        for i, (v, cash, buys) in enumerate(days):
            ts = datetime(2026, 11, 2 + i, 21, 30)
            s.add(PieSnapshot(ts=ts, value=v, holdings_value=v, cash=cash, is_daily_close=True))
            if buys:
                s.add(BotTrade(fill_id=i, order_id=i, batch="b", filled_at=ts.replace(hour=15),
                               ticker="X_US_EQ", side="BUY", quantity=1.0, gbp=buys))
        s.commit()
        rows = service.rebuild(s, Settings(database_url=url))
        s.commit()
    return S, rows


def test_idle_cash_drags_the_return(tmp_path):
    # holdings +10%, but a tenth of the pie sat in cash: the pie made 9%
    _, rows = _pie(tmp_path, [(900, 100, 0), (990, 100, 0)])
    assert rows[-1].value == pytest.approx(1090)
    assert rows[-1].unit_price / rows[0].unit_price - 1 == pytest.approx(0.09)


def test_deposit_then_investing_it_is_not_a_gain(tmp_path):
    # day 2: £100 arrives as cash; day 3: the bot invests it. Prices never move.
    _, rows = _pie(tmp_path, [(900, 100, 0), (900, 200, 0), (1000, 100, 100)])
    assert [r.unit_price for r in rows] == pytest.approx([1.0, 1.0, 1.0])
    assert rows[1].flow == pytest.approx(100) and rows[2].flow == pytest.approx(0)


def test_summary_value_includes_cash_once(tmp_path):
    S, _ = _pie(tmp_path, [(900, 100, 0), (990, 100, 0)])
    with S() as s:
        sm = service.summary(s, Settings(stale_after_s=10**9))
    assert sm["value"] == pytest.approx(1090) and sm["cash"] == pytest.approx(100)
    assert sm["returns"]["ALL"] == pytest.approx(0.09)
    assert sm["day_change"] == pytest.approx(90)
