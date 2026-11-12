"""Before there are 5 daily closes, the statistics come from the pie's own readings."""
import math
from datetime import date, datetime, timedelta

import pytest

from tracker import market, service, yahoo_closes
from tracker.config import Settings
from tracker.db import BotTrade, HoldingSnapshot, PieSnapshot, make_session_factory


def _readings(tmp_path, values, cash=20.0, start=datetime(2026, 11, 12, 14, 30), buys=None):
    url = f"sqlite:///{tmp_path / 'r.db'}"
    S = make_session_factory(url)
    with S() as s:
        s.add(PieSnapshot(ts=start - timedelta(hours=10), value=0.0, holdings_value=0.0, cash=500.0))  # before investing
        for i, v in enumerate(values):
            c = cash(i) if callable(cash) else cash
            s.add(PieSnapshot(ts=start + timedelta(minutes=i), value=v, holdings_value=v, cash=c))
        for i, (minute, gbp) in enumerate(buys or []):
            s.add(BotTrade(fill_id=i, order_id=i, batch="b", filled_at=start + timedelta(minutes=minute, seconds=-20),
                           ticker="X_US_EQ", side="BUY", quantity=1.0, gbp=gbp))
        s.commit()
        service.rebuild(s, Settings(database_url=url))
        s.commit()
    return S


def test_stats_are_ready_on_day_one_from_readings(tmp_path):
    vals = [900 * (1 + 0.002 * math.sin(i / 3)) for i in range(60)]
    S = _readings(tmp_path, vals)
    with S() as s:
        a = service.analysis_payload(s, Settings())
        st = service.stats_payload(s, Settings())
    assert a["ready"] and a["readings"]["readings"] == 60
    assert a["vs"] == {}  # comparisons wait for daily closes
    assert a["risk"]["volatility"] > 0 and a["ratios"]["sharpe"] is not None
    assert st["ready"] and len(st["drawdown"]) == 60
    # the reading before any stocks were bought (cash only) is left out
    with S() as s:
        assert service.reading_series(s)[0][1] == pytest.approx(1.0)


def test_cash_spent_on_stocks_is_not_a_gain_in_readings(tmp_path):
    # minute 2: the bot spends £100 of cash on stocks; prices don't move
    vals = [900, 900, 1000, 1000]
    S = _readings(tmp_path, vals, cash=lambda i: 120.0 if i < 2 else 20.0, buys=[(2, 100.0)])
    with S() as s:
        series = service.reading_series(s)
    assert [p for _, p in series] == pytest.approx([1.0] * 4)


def test_from_five_daily_closes_the_stats_use_them(tmp_path):
    url = f"sqlite:///{tmp_path / 'd.db'}"
    S = make_session_factory(url)
    with S() as s:
        for i in range(6):
            s.add(PieSnapshot(ts=datetime(2026, 11, 2 + i, 21, 30), value=900 + i, holdings_value=900 + i,
                              cash=0.0, is_daily_close=True))
        s.commit()
        service.rebuild(s, Settings(database_url=url))
        s.commit()
        a = service.analysis_payload(s, Settings())
    assert a["ready"] and "readings" not in a and a["n_days"] == 4  # 5 weekdays, 4 returns


def test_yahoo_closes_give_yesterdays_price(tmp_path, monkeypatch):
    days = [date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12)]
    fake = {"history:AAA": {"points": [{"t": d.isoformat(), "close": c} for d, c in zip(days, [100, 110, 99])]},
            "history:GBPUSD=X": {"points": [{"t": d.isoformat(), "close": 1.25} for d in days]},
            "profile:AAA": {"currency": "USD"}}
    monkeypatch.setattr(market, "get", lambda s, kind, sym, allow_fetch=True: fake.get(f"{kind}:{sym}"))
    monkeypatch.setattr(market, "yahoo_symbol", lambda s, tk: "AAA")
    url = f"sqlite:///{tmp_path / 'y.db'}"
    S = make_session_factory(url)
    with S() as s:
        snap = PieSnapshot(ts=datetime(2026, 11, 12, 15), value=79.2, holdings_value=79.2, cash=0.0)
        snap.holdings = [HoldingSnapshot(ticker="AAA_US_EQ", quantity=1.0, value=79.2)]
        s.add(snap)
        s.commit()
        yahoo_closes.build(s, [("AAA_US_EQ", 1.0, 79.2)], today=days[-1])
        s.commit()
        h = service.holdings(s, Settings())["holdings"][0]
    assert h["day_pct"] == pytest.approx(99 / 110 - 1)  # today's price against yesterday's close, in GBP


def test_early_return_is_measured_from_the_first_reading(tmp_path):
    S = _readings(tmp_path, [900, 910, 918])
    with S() as s:
        sm = service.summary(s, Settings(stale_after_s=10**9))
    assert sm["started"] and sm["returns"]["ALL"] == pytest.approx(938 / 920 - 1)  # cash £20 included


def test_1d_chart_is_measured_from_todays_open(tmp_path):
    # readings from 14:20 (before the 14:30 London / 09:30 New York open) to 14:39
    S = _readings(tmp_path, [900 + i for i in range(20)], start=datetime(2026, 11, 12, 14, 20))
    with S() as s:
        h = service.history(s, Settings(stale_after_s=10**9), "1D")
    assert h["baseline_label"] == "Today's open"
    assert h["points"][0]["t"].startswith("2026-11-12T14:30")
    assert h["baseline"] == pytest.approx(910 + 20)  # first reading at the open, cash included


def test_early_days_every_range_shows_the_readings(tmp_path):
    S = _readings(tmp_path, [900, 909, 918])
    with S() as s:
        for rng in ("1W", "ALL"):
            h = service.history(s, Settings(stale_after_s=10**9), rng)
            assert h["early"] and [round(p["u"], 4) for p in h["points"]] == [1.0, round(929 / 920, 4), round(938 / 920, 4)]
            assert h["points"][-1]["value"] == pytest.approx(938)


def test_early_moves_and_blocks(tmp_path):
    from tracker.db import Rebalance
    S = _readings(tmp_path, [900 * (1 + 0.001 * i) for i in range(40)])
    with S() as s:
        s.add(Rebalance(ts=datetime(2026, 11, 12, 14, 40), weights_before={}, weights_after={}, changes=[], source="pie"))
        s.commit()
        a = service.analysis_payload(s, Settings())
    assert a["moves"]["count"] == 1 and a["moves"]["windows"][0]["bot"] > 0
    assert len(a["blocks"]) == 3 and all(b["r"] > 0 for b in a["blocks"])  # 14:30, 14:45, 15:00
    assert a["rolling20"]  # 20-reading rolling return


def test_headline_change_is_from_todays_open(tmp_path):
    # 14:20..14:39; the open is 14:30 (09:30 New York)
    S = _readings(tmp_path, [900 + i for i in range(20)], start=datetime(2026, 11, 12, 14, 20))
    with S() as s:
        o = service.summary(s, Settings(stale_after_s=10**9))["since_open"]
    assert o["at_open"] and o["pct"] == pytest.approx(939 / 930 - 1) and o["gbp"] == pytest.approx(9)


def test_market_on_the_same_deposits(tmp_path):
    from datetime import date as d_
    from tracker.db import BenchmarkPrice, Contribution
    S = _readings(tmp_path, [900, 905], start=datetime(2026, 11, 12, 14, 30))
    with S() as s:
        s.add(Contribution(date=d_(2026, 11, 12), amount=1000.0, note="first"))
        for day, px in [(d_(2026, 11, 11), 100.0), (d_(2026, 11, 12), 104.0)]:
            s.add(BenchmarkPrice(ticker="VUAG.L", date=day, close=px))
        s.commit()
        sm = service.summary(s, Settings(stale_after_s=10**9, benchmarks=(("VUAG.L", "S&P 500"),)))
    assert sm["return_on_deposits"] == pytest.approx(925 / 1000 - 1)
    # no minute prices cached here: from the close on the deposit day (104) to the latest (104)
    assert sm["bench_on_deposits"]["VUAG.L"]["return"] == pytest.approx(0.0)
