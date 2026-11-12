"""Performance statistics on a unit-price series [(date, price), ...]."""
from __future__ import annotations

import math
from bisect import bisect_right
from datetime import date
from statistics import mean, pstdev

TRADING_DAYS = 252


def _prices(series):
    return [p for _, p in series]


def daily_returns(prices: list[float]) -> list[float]:
    return [b / a - 1 for a, b in zip(prices, prices[1:]) if a]


def price_on_or_before(series: list[tuple[date, float]], day: date) -> float | None:
    dates = [d for d, _ in series]
    i = bisect_right(dates, day)
    return series[i - 1][1] if i else None


def return_since(series: list[tuple[date, float]], start: date, current: float) -> float | None:
    """Return from the close on/before `start` (or inception if earlier) to `current`."""
    if not series:
        return None
    base = price_on_or_before(series, start)
    if base is None:
        base = series[0][1]
    return current / base - 1 if base else None


def max_drawdown(prices: list[float]) -> float:
    peak, worst = -math.inf, 0.0
    for p in prices:
        peak = max(peak, p)
        if peak > 0:
            worst = min(worst, p / peak - 1)
    return worst


def drawdown_series(series):
    peak, out = -math.inf, []
    for d, p in series:
        peak = max(peak, p)
        out.append((d, p / peak - 1 if peak > 0 else 0.0))
    return out


def annualised_volatility(prices: list[float]) -> float:
    r = daily_returns(prices)
    return pstdev(r) * math.sqrt(TRADING_DAYS) if len(r) > 1 else 0.0


def sharpe(prices: list[float], risk_free: float = 0.0) -> float | None:
    r = daily_returns(prices)
    if len(r) < 2:
        return None
    vol = pstdev(r)
    if vol == 0:
        return None
    return (mean(r) * TRADING_DAYS - risk_free) / (vol * math.sqrt(TRADING_DAYS))


def cagr(series) -> float | None:
    if len(series) < 2:
        return None
    (d0, p0), (d1, p1) = series[0], series[-1]
    years = (d1 - d0).days / 365.25
    if years < 0.25 or p0 <= 0:
        return None  # annualising a few weeks is misleading
    return (p1 / p0) ** (1 / years) - 1


def best_worst_day(series):
    r = [(series[i][0], series[i][1] / series[i - 1][1] - 1) for i in range(1, len(series)) if series[i - 1][1]]
    if not r:
        return None, None
    return max(r, key=lambda x: x[1]), min(r, key=lambda x: x[1])


def monthly_returns(series) -> dict[str, float]:
    """Return per calendar month, from the previous month's last close."""
    last_by_month: dict[str, float] = {}
    for d, p in series:
        last_by_month[f"{d.year:04d}-{d.month:02d}"] = p
    keys = sorted(last_by_month)
    out = {}
    for prev, cur in zip(keys, keys[1:]):
        out[cur] = last_by_month[cur] / last_by_month[prev] - 1
    if keys:  # first month: from inception
        first_price = series[0][1]
        out[keys[0]] = last_by_month[keys[0]] / first_price - 1
    return out


def rebalance_outcomes(series, rebalance_days: list[date], current: float) -> list[float]:
    """Pie return from each rebalance to the next one (the last one: to now)."""
    days = sorted(rebalance_days)
    out = []
    for i, rd in enumerate(days):
        start = price_on_or_before(series, rd)
        if start is None:
            continue
        end = price_on_or_before(series, days[i + 1]) if i + 1 < len(days) else current
        out.append(end / start - 1)
    return out
