"""Minute prices for the benchmarks (Yahoo, last 5 days, extended hours included), in GBP.

The daily benchmark (VUAG.L) trades in London and stops at 16:30, while most of the
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
PROXY = {"VUAG.L": ("SPY", "USD"), "CSPX.L": ("SPY", "USD"), "VUSA.L": ("SPY", "USD")}
# every minute source for a benchmark, in order of preference: (Yahoo symbol, currency, kind).
# "spot": a fund tracking the index (the US listing trades about 09:00-01:00 UK, the London
# listing 08:00-16:30); "fut": index futures, which trade nearly around the clock (Sunday 23:00
# to Friday 22:00 UK, a break 22:00-23:00), used only when no fund is trading; the page dashes
# those stretches.
SOURCES = {
    "VUAG.L": [("SPY", "USD", "spot"), ("VUAG.L", "GBP", "spot"), ("ES=F", "USD", "fut")],
    "CSPX.L": [("SPY", "USD", "spot"), ("CSPX.L", "USD", "spot"), ("ES=F", "USD", "fut")],
    "VUSA.L": [("SPY", "USD", "spot"), ("VUSA.L", "GBP", "spot"), ("ES=F", "USD", "fut")],
}
ACTIVE_GAP_S = 15 * 60   # the proxy counts as trading while its prices are less than this apart


def _fetch(sym: str) -> dict:
    import yfinance as yf
    df = yf.Ticker(sym).history(period="5d", interval="1m", prepost=True)   # with extended hours
    if df is None or df.empty:
        return {"points": []}
    return {"points": [[ts.tz_convert("UTC").isoformat(), float(c)] for ts, c in df["Close"].items() if c == c]}


market.FETCHERS["intraday"] = _fetch
market.TTL["intraday"] = timedelta(minutes=2)


def _sources(ticker: str) -> list[tuple[str, str | None, str]]:
    if ticker in SOURCES:
        return SOURCES[ticker]
    proxy, ccy = PROXY.get(ticker, (ticker, None))
    return [(proxy, ccy, "spot")]


def _symbols(ticker: str) -> list[str]:
    out = []
    for sym, ccy, _ in _sources(ticker):
        out.append(sym)
        if ccy and ccy != "GBP":
            out.append(f"GBP{ccy}=X")
    return list(dict.fromkeys(out))


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


def _to_gbp(s: Session, px: list, ccy: str | None) -> list[tuple[datetime, float]]:
    if not px or not ccy or ccy == "GBP":
        return px
    fx = _series(s, f"GBP{ccy}=X")
    fx_t = [t for t, _ in fx]
    out = []
    for t, p in px:
        i = bisect_right(fx_t, t)
        if i:
            out.append((t, p / fx[i - 1][1]))
    return out


def gbp_series_src(s: Session, ticker: str) -> list[tuple[datetime, float, str]]:
    """Minute prices (UTC, GBP) for a benchmark, with where each came from ("spot": a fund that
    tracks it; "fut": index futures). At each moment the first source in SOURCES that is trading
    is used. The pieces are chained so the level never jumps at a switch: the move across a
    switch is measured by the futures (they trade across every hand-over, while a fund's last
    price goes stale once it stops), so a move is never counted twice."""
    srcs = [(kind, _to_gbp(s, _series(s, sym), ccy)) for sym, ccy, kind in _sources(ticker)]
    srcs = [(kind, ser) for kind, ser in srcs if ser]
    if not srcs:
        return []
    times = [[t for t, _ in ser] for _, ser in srcs]
    raw = [dict(ser) for _, ser in srcs]
    bridge = next((ser for kind, ser in reversed(srcs) if kind == "fut"), None)

    def active(k: int, t: datetime) -> bool:   # source k has a price at t, or close on both sides
        if t in raw[k]:
            return True
        ts = times[k]
        i = bisect_right(ts, t)
        return bool(i and i < len(ts) and (ts[i] - ts[i - 1]).total_seconds() <= ACTIVE_GAP_S)

    chosen: dict[datetime, int] = {}
    for k, ts in enumerate(times):
        for t in ts:
            if t not in chosen and not any(active(j, t) for j in range(k)):
                chosen[t] = k
    out, last_v, last_t, scale, cur = [], None, None, 1.0, None
    for t in sorted(chosen):
        k = chosen[t]
        p = raw[k][t]
        if k != cur:
            if last_v is None:
                v = p
            else:
                a, b = (at(bridge, last_t), at(bridge, t)) if bridge else (None, None)
                v = last_v * (b / a) if a and b else last_v
            scale, cur = v / p, k
        v = p * scale
        out.append((t, v, srcs[k][0]))
        last_v, last_t = v, t
    return out


def gbp_series(s: Session, ticker: str) -> list[tuple[datetime, float]]:
    """Minute prices (UTC, GBP) for a benchmark (see gbp_series_src). Empty if not cached."""
    return [(t, p) for t, p, _ in gbp_series_src(s, ticker)]


def at(series: list[tuple[datetime, float]], t: datetime) -> float | None:
    """Last price at or before t (UTC aware)."""
    i = bisect_right([x for x, _ in series], t)
    return series[i - 1][1] if i else None


def change_since(series: list[tuple[datetime, float]], t0: datetime) -> float | None:
    p0 = at(series, t0)
    return series[-1][1] / p0 - 1 if p0 and series else None


def utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
