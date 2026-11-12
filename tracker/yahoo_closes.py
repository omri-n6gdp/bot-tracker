"""Yahoo daily closes (in GBP) for the stocks the pie holds now.

Used where the pie's own record has no closes yet: each holding's change today (against
yesterday's close), its sparkline, and how the holdings move together. Built by the poller
from the market cache, so pages stay fast. No Trading 212 calls. The pie's own performance
never uses these.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from . import market
from .db import get_meta, set_meta

log = logging.getLogger(__name__)

META_KEY = "yahoo_closes"
DAYS = 400  # a year of trading days, for the Monte Carlo
SUFFIX_CCY = {".L": "GBP", ".DE": "EUR", ".PA": "EUR", ".AS": "EUR", ".MI": "EUR", ".MC": "EUR",
              ".SW": "CHF", ".TO": "CAD", ".V": "CAD"}


def _closes(s: Session, sym: str) -> dict[date, float]:
    h = market.get(s, "history", sym) or {}
    return {date.fromisoformat(p["t"]): p["close"] for p in h.get("points", []) if p.get("close")}


def _currency(s: Session, sym: str) -> str:
    prof = market.get(s, "profile", sym, allow_fetch=False) or {}
    if prof.get("currency"):
        return prof["currency"]
    return next((c for suf, c in SUFFIX_CCY.items() if sym.endswith(suf)), "USD")


def _on_or_before(series: dict[date, float], days: list[date], d: date) -> float | None:
    lo, hi, best = 0, len(days) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if days[mid] <= d:
            best, lo = days[mid], mid + 1
        else:
            hi = mid - 1
    return series[best] if best is not None else None


def build(s: Session, holdings: list[tuple[str, float, float]], today: date | None = None) -> dict | None:
    """holdings: (ticker, quantity, value in GBP now). Stores and returns the closes, or None."""
    today = today or datetime.now(timezone.utc).date()
    fx_cache: dict[str, tuple[dict[date, float], list[date]]] = {}
    per_share: dict[str, dict[date, float]] = {}
    for tk, q, v in holdings:
        if not q or not v or v <= 0:
            continue
        sym = market.yahoo_symbol(s, tk)
        closes = _closes(s, sym) if sym else {}
        if not closes:
            continue
        ccy = _currency(s, sym)
        if ccy.upper() in ("GBP", "GBX"):
            scale = 0.01 if ccy in ("GBp", "GBX") else 1.0
            gbp = {d: c * scale for d, c in closes.items()}
        else:
            pair = f"GBP{ccy.upper()}=X"
            if pair not in fx_cache:
                fx = _closes(s, pair)
                fx_cache[pair] = (fx, sorted(fx))
            fx, fx_days = fx_cache[pair]
            gbp = {d: c / r for d, c in closes.items() if (r := _on_or_before(fx, fx_days, d))}
        if not gbp:
            continue
        # line the level up with what Trading 212 says a share is worth now, but only against
        # today's own bar: if Yahoo's latest bar is an earlier day (fetched before the open),
        # scaling it to today's price would wipe out today's move
        k = 1.0
        last_day = max(gbp)
        if last_day == today and gbp[last_day]:
            k = (v / q) / gbp[last_day]
            if not 0.5 < k < 2.0:
                log.warning("yahoo closes: %s price is %.2fx Trading 212's, scaling to match", tk, k)
            elif abs(k - 1) < 0.03:
                k = 1.0  # just today's move since Yahoo's bar: no scaling needed
        per_share[tk] = {d: p * k for d, p in gbp.items() if (today - d).days <= DAYS}
    if not per_share:
        return None
    data = {
        "built": datetime.now(timezone.utc).isoformat(),
        "tickers": sorted(per_share),
        "left_out": sorted({t for t, *_ in holdings} - set(per_share)),
        "per_share": {tk: [[d.isoformat(), round(p[d], 6)] for d in sorted(p)] for tk, p in per_share.items()},
    }
    set_meta(s, META_KEY, data)
    return data


def per_share(s: Session) -> dict[str, list[tuple[date, float]]]:
    data = get_meta(s, META_KEY) or {}
    return {tk: [(date.fromisoformat(d), p) for d, p in pts] for tk, pts in (data.get("per_share") or {}).items()}
