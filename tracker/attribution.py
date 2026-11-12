"""Work out which historical orders belong to the bot pie.

Trading 212's order history doesn't say which pie an order came from, so we infer it.
Facts this relies on (checked against real account data):
  * a pie rebalance creates all its orders at the same instant (identical createdAt);
    sells are usually one batch and the buys a second batch later the same day;
  * today's pie holdings are known exactly from the pie endpoint.

Walking backwards from today's holdings, an order is the pie's if the pie held that
stock right after it. A sell that closes a position rides along only when it is part of
a genuine rebalance (same batch, or a same-day sell batch next to a pie buy batch).
Very large batches are account-wide sell-offs that span several pies, so from those we
take only the orders for stocks the pie held.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass

MAX_REBALANCE_BATCH = 20
EPS = 1e-4


@dataclass(frozen=True)
class Trade:
    fill_id: int
    order_id: int
    batch: str          # order createdAt (ISO)
    filled_at: str      # ISO
    ticker: str
    side: str           # BUY | SELL
    quantity: float     # signed: + bought, - sold
    gbp: float          # signed money into the pie: + for buys, - for sells
    initiated_from: str | None = None
    price: float | None = None          # instrument currency
    currency: str | None = None
    isin: str | None = None
    name: str | None = None


def trades_from_orders(orders) -> list[Trade]:
    """Flatten Trading 212 historical orders (dicts or models) into filled TRADE fills."""
    out = []
    for o in orders:
        o = o.model_dump() if hasattr(o, "model_dump") else o
        f, od = o.get("fill"), o.get("order") or {}
        if not f or f.get("type") != "TRADE" or od.get("status") != "FILLED":
            continue
        side = od.get("side")
        q = abs(f.get("quantity") or 0.0)
        net = abs(((f.get("walletImpact") or {}).get("netValue")) or 0.0)
        inst = od.get("instrument") or {}
        out.append(Trade(
            fill_id=f.get("id"), order_id=od.get("id"), batch=od.get("createdAt") or f.get("filledAt"),
            filled_at=f.get("filledAt"), ticker=od.get("ticker"), side=side,
            quantity=q if side == "BUY" else -q, gbp=net if side == "BUY" else -net,
            initiated_from=od.get("initiatedFrom"), price=f.get("price"),
            currency=inst.get("currency") or od.get("currency"), isin=inst.get("isin"), name=inst.get("name"),
        ))
    return out


def attribute(trades: list[Trade], owned: dict[str, float], exclude: set[str] = frozenset()):
    """Return (fill ids of the pie's trades, positions left before the first trade, problems)."""
    trades = [t for t in trades if t.ticker not in exclude]
    batches: dict[str, list[Trade]] = collections.defaultdict(list)
    for t in trades:
        batches[t.batch].append(t)
    days: dict[str, list[str]] = collections.defaultdict(list)
    for k in batches:
        days[k[:10]].append(k)

    pos = collections.defaultdict(float, owned)
    chosen: set[int] = set()
    problems = []
    for day in sorted(days, reverse=True):
        day_has_pie_buy = False
        pending_close: list[list[Trade]] = []
        for k in sorted(days[day], reverse=True):
            b = batches[k]
            held = [t for t in b if pos[t.ticker] > EPS]
            closing = [t for t in b if t not in held and t.side == "SELL"]
            if held:
                day_has_pie_buy = day_has_pie_buy or any(t.side == "BUY" for t in held)
                ride = closing if len(b) <= MAX_REBALANCE_BATCH else []
                for t in held + ride:
                    chosen.add(t.fill_id)
                    pos[t.ticker] -= t.quantity
            elif closing and len(closing) == len(b):
                pending_close.append(b)
        if day_has_pie_buy:
            for b in pending_close:
                if len(b) <= MAX_REBALANCE_BATCH and all(t.initiated_from in ("AUTOINVEST", None) for t in b):
                    for t in b:
                        chosen.add(t.fill_id)
                        pos[t.ticker] -= t.quantity
        neg = {t: q for t, q in pos.items() if q < -EPS}
        if neg:
            problems.append((day, neg))
    return chosen, dict(pos), problems


def holdings_timeline(trades: list[Trade], chosen: set[int], owned: dict[str, float]):
    """Positions at the end of each trading date, walking back from today's `owned`.

    Returns a function f(date_iso) -> {ticker: qty} for the end of that date.
    """
    mine = sorted((t for t in trades if t.fill_id in chosen), key=lambda t: t.filled_at, reverse=True)

    def at_end_of(day: str) -> dict[str, float]:
        p = collections.defaultdict(float, owned)
        for t in mine:
            if t.filled_at[:10] <= day:
                break
            p[t.ticker] -= t.quantity
        return {k: v for k, v in p.items() if v > EPS}

    return at_end_of
