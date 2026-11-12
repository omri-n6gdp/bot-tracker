from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from tracker.config import Settings
from tracker.db import InstrumentName, PieSnapshot, Rebalance, get_meta, make_session_factory
from tracker.poller import Poller, read_contributions_csv
from tracker.rebalance import diff_targets
from tracker.t212 import PieDetail, PieSummary, Position, T212Error


class FakeClient:
    """Stands in for T212Client. Holds the bot pie (id 42) + an unrelated ISA holding."""

    def __init__(self):
        self.targets = {"AAA_US_EQ": 0.6, "BBB_US_EQ": 0.4}
        self.values = {"AAA_US_EQ": 600.0, "BBB_US_EQ": 400.0}
        self.pie_broken = False

    def pies(self):
        return [PieSummary.model_validate({"id": 42, "cash": 5.0, "result": {"priceAvgValue": 1000.0}}),
                PieSummary.model_validate({"id": 99, "cash": 0.0, "result": {"priceAvgValue": 9999.0}})]

    def pie(self, pie_id):
        if self.pie_broken:
            raise T212Error(404, "/pies")
        assert pie_id == 42
        return PieDetail.model_validate({
            "settings": {"name": "Bot pie"},
            "instruments": [{"ticker": t, "ownedQuantity": self.values[t] / 10, "expectedShare": w,
                             "currentShare": self.values[t] / 1000,
                             "result": {"priceAvgValue": self.values[t], "priceAvgInvestedValue": self.values[t] - 10,
                                        "priceAvgResult": 10.0}}
                            for t, w in self.targets.items()],
        })

    def positions(self):
        rows = [("AAA_US_EQ", "Alpha Inc", 12.0), ("BBB_US_EQ", "Beta plc", 11.0),
                ("SECRET_US_EQ", "My Other Holding", 50.0)]  # not in the bot pie
        return [Position.model_validate({"instrument": {"ticker": t, "name": n}, "quantity": 100,
                                         "walletImpact": {"currentValue": 100 * px}}) for t, n, px in rows]

    def dividends(self):
        return iter([])


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 16, 15, 0)

    def __call__(self):
        return self.t


@pytest.fixture
def setup(tmp_path):
    cfg = Settings(database_url=f"sqlite:///{tmp_path/'t.db'}", pie_id=42,
                   contributions_csv=tmp_path / "contributions.csv")
    S = make_session_factory(cfg.database_url)
    client, clock = FakeClient(), Clock()
    return Poller(client, S, cfg, clock=clock), client, clock, S


def test_snapshot_values_only_the_bot_pie(setup):
    poller, _, _, S = setup
    poller.poll_pie_list()
    snap = poller.poll_pie()
    assert snap.value == pytest.approx(1000.0)  # holdings only; performance ignores pie cash
    assert snap.cash == pytest.approx(5.0)
    with S() as s:
        assert get_meta(s, "pie_name") == "Bot pie"


def test_no_data_about_other_holdings_is_stored(setup):
    poller, _, _, S = setup
    poller.poll_pie()
    poller.poll_positions()
    with S() as s:
        names = set(s.scalars(select(InstrumentName.ticker)))
        prices = get_meta(s, "prices")["gbp"]
    assert names == {"AAA_US_EQ", "BBB_US_EQ"}
    assert "SECRET_US_EQ" not in prices


def test_rebalance_detected_when_targets_change(setup):
    poller, client, clock, S = setup
    poller.poll_pie()                     # baseline, not a rebalance
    clock.t += timedelta(minutes=1)
    poller.poll_pie()                     # same targets
    client.targets = {"AAA_US_EQ": 0.3, "BBB_US_EQ": 0.3, "CCC_US_EQ": 0.4}
    client.values["CCC_US_EQ"] = 0.0
    clock.t += timedelta(minutes=1)
    poller.poll_pie()
    with S() as s:
        reb = s.scalars(select(Rebalance)).all()
    assert len(reb) == 1
    kinds = {c["ticker"]: c["kind"] for c in reb[0].changes}
    assert kinds == {"AAA_US_EQ": "decreased", "BBB_US_EQ": "decreased", "CCC_US_EQ": "added"}


def test_falls_back_after_repeated_pie_failures(setup):
    poller, client, clock, S = setup
    poller.poll_pie()
    client.pie_broken = True
    for _ in range(3):
        clock.t += timedelta(minutes=1)
        poller.snapshot()
    assert poller.mode == "fallback"
    clock.t += timedelta(minutes=1)
    poller.snapshot()
    with S() as s:
        last = s.scalars(select(PieSnapshot).order_by(PieSnapshot.ts.desc())).first()
    assert last.source == "fallback"
    # 60 x £12 + 40 x £11 from /positions, cash 0 (pie list never polled)
    assert last.value == pytest.approx(60 * 12 + 40 * 11)


def test_diff_targets_tolerance_and_first_run():
    assert diff_targets(None, {"A": 1.0}) == []
    assert diff_targets({"A": 0.5, "B": 0.5}, {"A": 0.5003, "B": 0.4997}) == []
    ch = diff_targets({"A": 0.5, "B": 0.5}, {"A": 1.0})
    assert {c["ticker"]: c["kind"] for c in ch} == {"A": "increased", "B": "removed"}


def test_contributions_csv(tmp_path):
    f = tmp_path / "c.csv"
    f.write_text("date,amount,note\n2026-01-05,\"£1,000.00\",start\n2026-02-01,-200,withdrew\n,,\n", encoding="utf-8")
    rows = read_contributions_csv(f)
    assert [(str(d), a) for d, a, _ in rows] == [("2026-01-05", 1000.0), ("2026-02-01", -200.0)]
    assert read_contributions_csv(tmp_path / "missing.csv") == []


def test_daily_close_due_only_on_weekdays_after_close_time(setup):
    poller, _, clock, _ = setup
    clock.t = datetime(2026, 11, 16, 21, 29)   # Mon 21:29 UTC = 21:29 London in winter
    assert poller._is_close_due(None)[0] is False
    clock.t = datetime(2026, 11, 16, 21, 31)
    due, day = poller._is_close_due(None)
    assert due and poller._is_close_due(day)[0] is False
    clock.t = datetime(2026, 11, 21, 22, 0)    # Saturday
    assert poller._is_close_due(None)[0] is False


def test_alerts_fire_after_three_failures_and_on_recovery():
    from tracker.alerts import FailureTracker
    sent = []
    ft = FailureTracker(sender=sent.append)
    ft.failed("snapshot", "boom"); ft.failed("snapshot", "boom")
    assert sent == []
    ft.failed("snapshot", "boom"); ft.failed("snapshot", "boom")
    assert len(sent) == 1 and "3 times" in sent[0]
    ft.ok("snapshot")
    assert len(sent) == 2 and "working again" in sent[1]
    ft.ok("snapshot")
    assert len(sent) == 2


def test_contributions_csv_skips_comment_rows(tmp_path):
    f = tmp_path / "c.csv"
    f.write_text("date,amount,note\n# 2026-01-01,100,example\n2026-01-05,250,real\n", encoding="utf-8")
    assert [a for _, a, _ in read_contributions_csv(f)] == [250.0]


# ---- trades: telling this pie's orders apart from another pie holding the same stocks ----

from tracker.db import BotTrade, HoldingSnapshot  # noqa: E402
from tracker.t212 import HistoricalOrder  # noqa: E402


def _order(fid, ticker, side, qty, gbp, created, filled, source="AUTOINVEST"):
    return HistoricalOrder.model_validate({
        "order": {"id": fid, "ticker": ticker, "side": side, "status": "FILLED", "createdAt": created,
                  "initiatedFrom": source, "instrument": {"currency": "USD"}},
        "fill": {"id": fid, "filledAt": filled, "quantity": qty, "type": "TRADE", "price": 10.0,
                 "walletImpact": {"netValue": gbp}},
    })


def _snap(S, ts, qty):
    with S() as s:
        sn = PieSnapshot(ts=ts, value=sum(qty.values()) * 10, holdings_value=sum(qty.values()) * 10, source="pie")
        for t, q in qty.items():
            sn.holdings.append(HoldingSnapshot(ticker=t, quantity=q, value=q * 10))
        s.add(sn)
        s.commit()


def _trades_setup(setup, orders):
    poller, client, clock, S = setup
    client.orders = lambda max_pages=5: iter(sorted(orders, key=lambda o: -o.fill.id))  # newest first
    return poller, S


def test_only_batches_that_match_this_pies_share_counts_count(setup):
    t = "2026-11-16T14:{:02d}:00Z"
    other_pie = [_order(1, "AAA_US_EQ", "BUY", 3.0, 30.0, t.format(10), t.format(10)),
                 _order(2, "BBB_US_EQ", "BUY", 2.0, 20.0, t.format(10), t.format(10))]
    this_pie = [_order(3, "AAA_US_EQ", "BUY", 1.0, 10.0, t.format(20), t.format(20)),
                _order(4, "BBB_US_EQ", "BUY", 0.5, 5.0, t.format(20), t.format(21))]
    poller, S = _trades_setup(setup, other_pie + this_pie)
    _snap(S, datetime(2026, 11, 16, 14, 0), {"AAA_US_EQ": 0.0, "BBB_US_EQ": 0.0})
    _snap(S, datetime(2026, 11, 16, 14, 15), {"AAA_US_EQ": 0.0, "BBB_US_EQ": 0.0})  # other pie's buy: no change here
    _snap(S, datetime(2026, 11, 16, 14, 25), {"AAA_US_EQ": 1.0, "BBB_US_EQ": 0.5})
    assert poller.poll_trades() == 2
    with S() as s:
        assert sorted(s.scalars(select(BotTrade.fill_id))) == [3, 4]


def test_orders_from_before_tracking_began_are_ignored(setup):
    old = [_order(1, "AAA_US_EQ", "BUY", 1.0, 10.0, "2026-11-10T14:00:00Z", "2026-11-10T14:00:00Z")]
    poller, S = _trades_setup(setup, old)
    _snap(S, datetime(2026, 11, 16, 14, 0), {"AAA_US_EQ": 1.0, "BBB_US_EQ": 0.0})
    assert poller.poll_trades() == 0


def test_a_batch_waits_for_the_next_snapshot(setup):
    o = [_order(5, "AAA_US_EQ", "SELL", 0.5, 6.0, "2026-11-16T14:30:00Z", "2026-11-16T14:30:00Z")]
    poller, S = _trades_setup(setup, o)
    _snap(S, datetime(2026, 11, 16, 14, 29), {"AAA_US_EQ": 1.0, "BBB_US_EQ": 0.0})
    assert poller.poll_trades() == 0           # no snapshot after the fill yet
    _snap(S, datetime(2026, 11, 16, 14, 31), {"AAA_US_EQ": 0.5, "BBB_US_EQ": 0.0})
    assert poller.poll_trades() == 1
    with S() as s:
        assert s.scalars(select(BotTrade.gbp)).one() == pytest.approx(-6.0)


def test_manual_orders_never_count(setup):
    o = [_order(6, "AAA_US_EQ", "BUY", 1.0, 10.0, "2026-11-16T14:30:00Z", "2026-11-16T14:30:00Z", source="MANUAL")]
    poller, S = _trades_setup(setup, o)
    _snap(S, datetime(2026, 11, 16, 14, 29), {"AAA_US_EQ": 0.0})
    _snap(S, datetime(2026, 11, 16, 14, 31), {"AAA_US_EQ": 1.0})
    assert poller.poll_trades() == 0


def test_dividends_are_this_pies_share_and_only_since_tracking_began(setup):
    from tracker.db import Dividend as DivRow
    from tracker.t212 import Dividend as Div
    poller, client, clock, S = setup
    _snap(S, datetime(2026, 11, 16, 14, 0), {"AAA_US_EQ": 50.0, "BBB_US_EQ": 10.0})  # account holds 100 AAA
    client.dividends = lambda: iter([
        Div.model_validate({"reference": "new", "ticker": "AAA_US_EQ", "amount": 2.0, "paidOn": "2026-11-16T15:00:00Z"}),
        Div.model_validate({"reference": "old", "ticker": "AAA_US_EQ", "amount": 9.0, "paidOn": "2026-10-01T15:00:00Z"}),
    ])
    assert poller.poll_dividends() == 1
    with S() as s:
        assert s.scalars(select(DivRow.amount)).one() == pytest.approx(1.0)
