"""Where the pie could go: a Monte Carlo range of outcomes.

Uses the stocks the pie holds now, at today's weights, and how they've moved day to day over
the past year (Yahoo closes in GBP, so the swings and how the stocks move together are real).
Whole days of returns are drawn at random in short runs (a block bootstrap, so bad days can
cluster like they do). The average return is taken out, so there's no assumed trend up or
down: the result shows how wide the spread of outcomes is, not a forecast. Cash stays flat.
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from . import yahoo_closes
from .db import set_meta

META_KEY = "monte_carlo"
PATHS = 2000
HORIZON = 252          # trading days: a year
BLOCK = 5              # days drawn together
MIN_DAYS = 60
QUANTILES = (0.05, 0.25, 0.5, 0.75, 0.95)


def _pct(sorted_vals: list[float], q: float) -> float:
    i = q * (len(sorted_vals) - 1)
    lo = math.floor(i)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def portfolio_returns(per_share: dict[str, list[tuple[date, float]]], weights: dict[str, float]) -> list[float]:
    """Daily returns of today's mix, held at constant weights (the bot keeps rebalancing),
    over the days every included stock has a price."""
    series = {tk: dict(per_share[tk]) for tk in weights if len(per_share.get(tk, [])) > MIN_DAYS}
    if not series:
        return []
    days = sorted(set.intersection(*(set(s) for s in series.values())))
    total = sum(weights[tk] for tk in series)
    out = []
    for d0, d1 in zip(days, days[1:]):
        out.append(sum(weights[tk] / total * (series[tk][d1] / series[tk][d0] - 1) for tk in series))
    return out


def simulate(daily: list[float], start_value: float, cash: float, horizon: int = HORIZON,
             paths: int = PATHS, seed: int = 0) -> dict:
    mean = sum(daily) / len(daily)
    r = [x - mean for x in daily]            # no trend: only the swings
    n = len(r)
    rng = random.Random(seed)
    invested0 = start_value - cash
    # value at each day of each path, kept per day for the percentiles
    by_day = [[0.0] * paths for _ in range(horizon)]
    for p in range(paths):
        g, day = 1.0, 0
        while day < horizon:
            i = rng.randrange(n)
            for k in range(BLOCK):
                if day >= horizon:
                    break
                g *= 1 + r[(i + k) % n]
                by_day[day][p] = cash + invested0 * g
                day += 1
    fan = []
    for d in range(horizon):
        v = sorted(by_day[d])
        fan.append([d + 1] + [round(_pct(v, q), 2) for q in QUANTILES])
    sd = (sum(x * x for x in r) / (n - 1)) ** 0.5
    horizons = {}
    for h in (21, 63, 252):
        if h <= horizon:
            v = by_day[h - 1]
            horizons[str(h)] = {"loss": sum(x < start_value for x in v) / paths,
                                "down10": sum(x < start_value * 0.9 for x in v) / paths,
                                "up10": sum(x > start_value * 1.1 for x in v) / paths}
    return {"fan": fan, "horizons": horizons, "vol_annual": sd * math.sqrt(252) * invested0 / start_value}


def build(s: Session, holdings: list[tuple[str, float]], cash: float, today: date | None = None) -> dict | None:
    """holdings: (ticker, value in GBP). Stores and returns the simulation, or None."""
    today = today or datetime.now(timezone.utc).date()
    value = sum(v for _, v in holdings) + cash
    if value <= 0:
        return None
    per_share = yahoo_closes.per_share(s)
    weights = {tk: v / (value - cash) for tk, v in holdings if v > 0} if value > cash else {}
    daily = portfolio_returns(per_share, weights)
    if len(daily) < MIN_DAYS:
        return None
    sim = simulate(daily, value, cash, seed=today.toordinal())
    covered = [tk for tk in weights if len(per_share.get(tk, [])) > MIN_DAYS]
    data = {"built": datetime.now(timezone.utc).isoformat(), "value": value, "cash": cash,
            "days_used": len(daily), "paths": PATHS, "holdings": len(covered),
            "left_out": sorted(set(weights) - set(covered)), **sim}
    set_meta(s, META_KEY, data)
    return data
