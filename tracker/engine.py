"""Unit-price performance engine.

The pie is priced in units, like a fund. It starts at £1.00 per unit. Money you add
buys units at that day's price and money you take out cancels units, so the unit
price moves only with the bot's performance, never with your top-ups.

    P_t = (V_t - F_t) / U_{t-1}      price from the value before today's flow F_t
    U_t = U_{t-1} + F_t / P_t
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, getcontext
from typing import Iterable

getcontext().prec = 28
START_PRICE = Decimal(1)


@dataclass(frozen=True)
class DailyRow:
    date: date
    value: float
    units: float
    unit_price: float
    flow: float = 0.0


def compute_daily(
    closes: Iterable[tuple[date, float]], flows: Iterable[tuple[date, float]]
) -> list[DailyRow]:
    """closes: one (date, pie value) per day. flows: (date, +in / -out) in GBP.

    A flow is applied at the first close on or after its date. Flows on or before the
    first close count as starting capital.
    """
    closes = sorted(closes)
    if not closes:
        return []
    close_dates = [c[0] for c in closes]
    by_close: dict[date, Decimal] = {}
    for fd, amt in flows:
        i = bisect_left(close_dates, fd)
        if i < len(close_dates):
            key = close_dates[i]
            by_close[key] = by_close.get(key, Decimal(0)) + Decimal(str(amt))
        # flows after the last close are "pending" and handled by live_unit_price

    rows: list[DailyRow] = []
    units = Decimal(0)
    price = START_PRICE
    for i, (day, value_f) in enumerate(closes):
        value = Decimal(str(value_f))
        flow = by_close.get(day, Decimal(0))
        if i == 0 or units <= 0:
            # inception (or restart after a full withdrawal) at the last known price
            units = value / price if value > 0 else Decimal(0)
        else:
            before = value - flow
            if before > 0:
                price = before / units
            units += flow / price
            if units <= 0:
                units = Decimal(0)
        if units > 0:
            price = value / units
        rows.append(DailyRow(day, float(value), float(units), float(price), float(flow)))
    return rows


def live_unit_price(live_value: float, last: DailyRow, pending_flow: float = 0.0) -> float:
    """Indicative intraday price: today's value minus any money added since the last close."""
    if last.units <= 0:
        return last.unit_price
    return (live_value - pending_flow) / last.units
