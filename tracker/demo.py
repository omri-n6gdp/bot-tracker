"""Synthetic demo data so the dashboard can be built and shown before live data exists.
Writes to data/demo.db only; never touches the real database.
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .config import ROOT, Settings
from .db import (
    Base, BenchmarkPrice, BotTrade, Contribution, Dividend, HoldingSnapshot, InstrumentName, PieSnapshot,
    Rebalance, TargetWeights, make_engine, set_meta,
)
from .rebalance import diff_targets
from . import service

DEMO_URL = f"sqlite:///{ROOT / 'data' / 'demo.db'}"
TICKERS = {  # ticker: (name, annual drift, annual vol, start GBP price)
    "RKLB_US_EQ": ("Rocket Lab USA", 0.45, 0.70, 4.0),
    "NVDA_US_EQ": ("NVIDIA", 0.35, 0.50, 95.0),
    "ASML_US_EQ": ("ASML Holding", 0.15, 0.35, 560.0),
    "LMT_US_EQ": ("Lockheed Martin", 0.08, 0.22, 380.0),
    "IUSAl_EQ": ("iShares Core S&P 500", 0.10, 0.16, 48.0),
    "SGLNl_EQ": ("iShares Physical Gold", 0.06, 0.14, 38.0),
}


def _utc(d: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, t, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


def _random_targets(rng: random.Random, tickers: list[str]) -> dict[str, float]:
    chosen = rng.sample(tickers, k=rng.randint(4, len(tickers)))
    raw = {t: rng.uniform(0.5, 2.0) for t in chosen}
    total = sum(raw.values())
    w = {t: round(v / total, 4) for t, v in raw.items()}
    first = chosen[0]
    w[first] = round(w[first] + 1 - sum(w.values()), 4)
    return w


def seed(days: int = 200, seed_: int = 7, url: str = DEMO_URL, today: date | None = None) -> str:
    rng = random.Random(seed_)
    tz = ZoneInfo("Europe/London")
    eng = make_engine(url)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    today = today or datetime.now(tz).date()
    start = today - timedelta(days=days)
    weekdays = [start + timedelta(days=i) for i in range(days + 1) if (start + timedelta(days=i)).weekday() < 5]

    prices = {t: v[3] for t, v in TICKERS.items()}
    bench = 105.0
    targets = _random_targets(rng, list(TICKERS))
    qty = {t: 0.0 for t in TICKERS}
    invested = {t: 0.0 for t in TICKERS}
    cash = 0.0
    flows = {weekdays[0]: 1000.0, weekdays[len(weekdays) // 3]: 250.0, weekdays[2 * len(weekdays) // 3]: 500.0}
    rebal_days = set(weekdays[i] for i in range(10, len(weekdays), 15))

    trade_log: list[tuple[str, float, float]] = []  # (ticker, signed qty, signed gbp)

    def buy_to_targets(total_value: float):
        nonlocal cash
        for t in TICKERS:
            want = total_value * targets.get(t, 0.0) / prices[t]
            delta = want - qty[t]
            if abs(delta) > 1e-9:
                trade_log.append((t, delta, delta * prices[t]))
            if delta < 0 and qty[t] > 0:
                invested[t] *= want / qty[t] if qty[t] else 0
            else:
                invested[t] += delta * prices[t]
            qty[t] = want
        cash = 0.0

    with Session(eng) as s:
        set_meta(s, "pie_name", "Momentum Bot (demo)")
        for t, (name, *_rest) in TICKERS.items():
            s.add(InstrumentName(ticker=t, name=name, currency="USD" if t.endswith("US_EQ") else "GBP"))
        s.add(TargetWeights(ts=_utc(weekdays[0], time(9), tz), weights=targets))
        last_targets = dict(targets)

        fid = iter(range(1, 10**6))

        def _flush(day, at):
            ts = _utc(day, at, tz)
            for t, dq, gbp in trade_log:
                n = next(fid)
                s.add(BotTrade(fill_id=n, order_id=n, batch=ts.isoformat(), filled_at=ts, ticker=t,
                               side="BUY" if dq > 0 else "SELL", quantity=dq, gbp=gbp))
            trade_log.clear()

        def snapshot(ts: datetime, close: bool):
            hv = sum(qty[t] * prices[t] for t in TICKERS)
            snap = PieSnapshot(ts=ts, value=hv + cash, holdings_value=hv, cash=cash,
                               invested=sum(invested.values()),
                               result=hv - sum(invested.values()), is_daily_close=close, source="demo")
            for t in TICKERS:
                if qty[t] > 1e-9 or targets.get(t):
                    v = qty[t] * prices[t]
                    snap.holdings.append(HoldingSnapshot(
                        ticker=t, quantity=qty[t], value=v, invested=invested[t], result=v - invested[t],
                        current_share=v / hv if hv else 0, expected_share=targets.get(t, 0.0)))
            s.add(snap)

        for i, day in enumerate(weekdays):
            dt = 1 / 252
            for t, (_, mu, sig, _) in TICKERS.items():
                prices[t] *= math.exp((mu - sig ** 2 / 2) * dt + sig * math.sqrt(dt) * rng.gauss(0, 1))
            bench *= math.exp((0.08 - 0.15 ** 2 / 2) * dt + 0.15 * math.sqrt(dt) * rng.gauss(0, 1))
            s.add(BenchmarkPrice(ticker="VUAG.L", date=day, close=bench * 0.9 * (1 + 0.0004 * i)))
            value = sum(qty[t] * prices[t] for t in TICKERS) + cash
            if day in flows:
                s.add(Contribution(date=day, amount=flows[day], note="demo top-up" if i else "initial"))
                value += flows[day]
                buy_to_targets(value)
                _flush(day, time(14, 0))
            if day in rebal_days:
                targets = _random_targets(rng, list(TICKERS))
                changes = diff_targets(last_targets, targets)
                ts = _utc(day, time(14, 35), tz)
                if changes:
                    s.add(Rebalance(ts=ts, weights_before=last_targets, weights_after=targets, changes=changes))
                    s.add(TargetWeights(ts=ts, weights=targets))
                last_targets = dict(targets)
                buy_to_targets(value)
                _flush(day, time(14, 35))
            if i % 40 == 25:
                held = [t for t in TICKERS if qty[t] > 0]
                if held:
                    t = rng.choice(held)
                    s.add(Dividend(reference=f"demo-{i}", ticker=t, amount=round(value * 0.002, 2),
                                   paid_on=_utc(day, time(12), tz)))
            if day == weekdays[-1]:
                # intraday path for the 1D chart
                for m in range(8 * 60, 21 * 60 + 30, 5):
                    for t, (_, mu, sig, _) in TICKERS.items():
                        prices[t] *= math.exp(sig * math.sqrt(1 / (252 * 160)) * rng.gauss(0, 1))
                    snapshot(_utc(day, time(m // 60, m % 60), tz), close=(m == 21 * 60 + 25))
            else:
                snapshot(_utc(day, time(21, 30), tz), close=True)
        s.commit()
        service.rebuild(s, Settings(database_url=url))
        s.commit()
    return url
