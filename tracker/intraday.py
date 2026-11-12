"""Minute prices for the benchmarks (Yahoo, last 5 days), in GBP.

The daily benchmarks (VUAG.L, VWRL.L) trade in London and stop at 16:30, while most of the
pie trades in New York until 21:00 UK time, so for minute-by-minute comparisons each one
uses a US-listed fund that tracks the same index, converted with the minute GBP/USD rate.
Refreshed by the poller every 2 minutes; pages only read the cache.
"""
from __future__ import annotations

import logging
from bisect import bisect_right
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from . import market

log = logging.getLogger(__name__)

# daily benchmark -> (minute proxy tracking the same index, its currency)
PROXY = {"VUAG.L": ("SPY", "USD"), "VWRL.L": ("VT", "USD"), "CSPX.L": ("SPY", "USD"), "VUSA.L": ("SPY", "USD")}


def _fetch(sym: str) -> dict:
    import yfinance as yf
    df = yf.Ticker(sym).history(period="5d", interval="1m")
    if df is None or df.empty:
        return {"points": []}
    return {"points": [[ts.tz_convert("UTC").isoformat(), float(c)] for ts, c in df["Close"].items() if c == c]}


market.FETCHERS["intraday"] = _fetch
market.TTL["intraday"] = timedelta(minutes=2)


def _symbols(ticker: str) -> list[str]:
    proxy, ccy = PROXY.get(ticker, (ticker, None))
    return [proxy] + ([f"GBP{ccy}=X"] if ccy and ccy != "GBP" else [])


def refresh(s: Session, tickers: list[str]) -> int:
    n = 0
    for t in tickers:
        for sym in _symbols(t):
            if market.get(s, "intraday", sym):
                n += 1
    return n


def _series(s: Session, sym: str) -> list[tuple[datetime, float]]:
    d = market.get(s, "intraday", sym, allow_fetch=False) or {}
    return [(datetime.fromisoformat(t), c) for t, c in d.get("points", [])]


def gbp_series(s: Session, ticker: str) -> list[tuple[datetime, float]]:
    """Minute prices (UTC, GBP) for a daily benchmark's minute proxy. Empty if not cached."""
    proxy, ccy = PROXY.get(ticker, (ticker, None))
    px = _series(s, proxy)
    if not px or not ccy or ccy == "GBP":
        return px
    fx = _series(s, f"GBP{ccy}=X")
    if not fx:
        return []
    fx_t = [t for t, _ in fx]
    out = []
    for t, p in px:
        i = bisect_right(fx_t, t)
        if i:
            out.append((t, p / fx[i - 1][1]))
    return out


def at(series: list[tuple[datetime, float]], t: datetime) -> float | None:
    """Last price at or before t (UTC aware)."""
    i = bisect_right([x for x, _ in series], t)
    return series[i - 1][1] if i else None


def change_since(series: list[tuple[datetime, float]], t0: datetime) -> float | None:
    p0 = at(series, t0)
    return series[-1][1] / p0 - 1 if p0 and series else None


def utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
