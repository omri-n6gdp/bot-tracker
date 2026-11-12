from datetime import date

import pytest
from sqlalchemy import select

from tracker.attribution import attribute, holdings_timeline, trades_from_orders
from tracker.backfill import run
from tracker.config import Settings
from tracker.db import BotTrade, PieSnapshot, Rebalance, make_session_factory
from tracker import service

FID = iter(range(1, 10_000))


def order(ticker, side, qty, gbp, created, filled=None, src="AUTOINVEST", price=10.0):
    fid = next(FID)
    return {"order": {"id": fid, "ticker": ticker, "side": side, "status": "FILLED", "createdAt": created,
                      "initiatedFrom": src, "instrument": {"ticker": ticker, "currency": "GBP", "isin": "X", "name": ticker}},
            "fill": {"id": fid, "type": "TRADE", "quantity": qty if side == "BUY" else -qty, "price": price,
                     "filledAt": filled or created, "walletImpact": {"netValue": gbp}}}


def scenario():
    o = [
        # bot pie: start 1 Jun with A + B
        order("A", "BUY", 10, 100, "2026-06-01T13:00:00Z"),
        order("B", "BUY", 10, 100, "2026-06-01T13:00:00Z"),
        # another pie buys C the same second as a scheduled deposit (must not leak in)
        order("C", "BUY", 5, 50, "2026-06-01T09:00:00Z"),
        # rebalance 10 Jun: sell all of B (separate sell batch), buy D with A
        order("B", "SELL", 10, 110, "2026-06-10T09:00:00Z", "2026-06-10T13:30:00Z"),
        order("A", "BUY", 1, 10, "2026-06-10T13:31:00Z"),
        order("D", "BUY", 10, 100, "2026-06-10T13:31:00Z"),
        # account-wide sell-off of the other pie (big batch) incl. a slice of A
        *[order(f"X{i}", "SELL", 1, 1, "2026-06-15T11:00:00Z") for i in range(25)],
        order("A", "SELL", 1, 11, "2026-06-15T11:00:00Z"),
        order("C", "SELL", 5, 55, "2026-06-16T11:00:00Z"),
        # manual app order of something else
        order("Z", "BUY", 1, 5, "2026-06-17T10:00:00Z", src="IOS"),
    ]
    owned = {"A": 10.0, "D": 10.0}
    return o, owned


def test_attribution_follows_the_pie_only():
    orders, owned = scenario()
    trades = trades_from_orders(orders)
    chosen, _, problems = attribute(trades, owned)
    mine = {t.ticker for t in trades if t.fill_id in chosen}
    assert mine == {"A", "B", "D"} and problems == []
    at = holdings_timeline(trades, chosen, owned)
    assert at("2026-06-01") == pytest.approx({"A": 10.0, "B": 10.0})
    assert at("2026-06-10") == pytest.approx({"A": 11.0, "D": 10.0})
    assert at("2026-06-15") == pytest.approx({"A": 10.0, "D": 10.0})


def flat_prices(inst, start, today):
    from datetime import timedelta
    days = [start + timedelta(days=i) for i in range((today - start).days + 1)]
    px = {"A": 10.0, "B": 11.0, "D": 10.0}
    return ({t: {d: px.get(t, 10.0) for d in days} for t in inst}, {t: {"symbol": t} for t in inst})


def test_backfill_writes_history_flows_and_moves(tmp_path):
    orders, owned = scenario()
    url = f"sqlite:///{tmp_path/'b.db'}"
    cfg = Settings(database_url=url, pie_id=1)
    S = make_session_factory(url)
    data = {"pie": {"instruments": [{"ticker": t, "ownedQuantity": q} for t, q in owned.items()],
                    "settings": {"creationDate": None}},
            "other_pie_tickers": ["C"], "orders": orders}
    res = run(S, cfg, data, start=date(2026, 6, 1), today=date(2026, 6, 20), price_fn=flat_prices, benchmark_fn=lambda S, c: 0)
    assert res["days"] == 15 and res["moves"] >= 1
    with S() as s:
        snaps = s.scalars(select(PieSnapshot).order_by(PieSnapshot.ts)).all()
        assert all(x.source == "backfill" for x in snaps)
        assert snaps[0].value == pytest.approx(10 * 10 + 10 * 11)       # A + B on 1 Jun
        assert {t.ticker for t in s.scalars(select(BotTrade))} == {"A", "B", "D"}
        assert s.scalars(select(Rebalance)).first().source == "backfill"
        rows = service.rebuild(s, cfg)
    # prices flat apart from B's +10% (already in at day one), so the bot's return is ~0
    assert rows[-1].unit_price == pytest.approx(rows[0].unit_price, rel=0.01)
    # running it again replaces, not duplicates
    run(S, cfg, data, start=date(2026, 6, 1), today=date(2026, 6, 20), price_fn=flat_prices, benchmark_fn=lambda S, c: 0)
    with S() as s:
        assert len(s.scalars(select(PieSnapshot)).all()) == 15


def test_flows_combine_trades_minus_dividends(tmp_path):
    from datetime import datetime
    from tracker.db import Dividend
    url = f"sqlite:///{tmp_path/'f.db'}"
    S = make_session_factory(url)
    with S() as s:
        s.add(BotTrade(fill_id=1, order_id=1, batch="b", filled_at=datetime(2026, 6, 2, 13), ticker="A",
                       side="BUY", quantity=1, gbp=50.0))
        s.add(BotTrade(fill_id=2, order_id=2, batch="b", filled_at=datetime(2026, 6, 2, 14), ticker="B",
                       side="SELL", quantity=-1, gbp=-20.0))
        s.add(Dividend(reference="d1", ticker="A", amount=3.0, paid_on=datetime(2026, 6, 3, 12)))
        s.commit()
        assert service.flows(s) == [(date(2026, 6, 2), 30.0), (date(2026, 6, 3), -3.0)]
