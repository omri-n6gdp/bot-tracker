"""The bot's moves, judged: did each one help, and what was in the news?

Did it help: from the minute a move was spotted until the next move (or now), the return of
the new target mix against the return the old mix would have had, using each stock's price in
GBP: minute prices while the move is less than 5 days old, daily closes after that. Weights
are held as they were set (an approximation: the bot may trim in between).

News: headlines for the stocks a move changed most, published in the 3 days before it to a day
after. Yahoo only serves recent news, so it's collected while the move is fresh.

Both are worked out by the poller and kept in the database; pages only read them.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import intraday, market, yahoo_closes
from .db import Rebalance, get_meta, set_meta

log = logging.getLogger(__name__)

VALUE_KEY, NEWS_KEY = "move_value", "move_news"
MINUTE_DAYS = 4.5    # Yahoo keeps 5 days of minute prices
NEWS_BEFORE, NEWS_AFTER = timedelta(days=3), timedelta(days=1)


def _utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


class Prices:
    """GBP-consistent prices for any ticker at a time; ratios only, so pence vs pounds and a
    fixed calibration don't matter."""

    def __init__(self, s: Session, fetch: bool = True):
        self.s, self.fetch = s, fetch
        self._minute: dict[str, list] = {}
        self._daily: dict[str, list] = {}

    def _min(self, sym: str) -> list:
        if sym not in self._minute:
            d = market.get(self.s, "intraday", sym, allow_fetch=self.fetch) or {}
            self._minute[sym] = [(datetime.fromisoformat(t), c) for t, c in d.get("points", [])]
        return self._minute[sym]

    def _day(self, sym: str) -> list:
        if sym not in self._daily:
            h = market.get(self.s, "history", sym, allow_fetch=self.fetch) or {}
            self._daily[sym] = [(datetime.fromisoformat(p["t"]).replace(tzinfo=timezone.utc), p["close"])
                                for p in h.get("points", []) if p.get("close")]
        return self._daily[sym]

    def at(self, ticker: str, t: datetime, minute: bool, start: bool) -> float | None:
        sym = market.yahoo_symbol(self.s, ticker) if self.fetch else (get_meta(self.s, "yahoo_map", {}) or {}).get(ticker)
        if not sym:
            return None
        ccy = yahoo_closes._currency(self.s, sym).upper()
        fx_sym = None if ccy in ("GBP", "GBX") else f"GBP{ccy}=X"
        if minute:
            px, fx = intraday.at(self._min(sym), t), intraday.at(self._min(fx_sym), t) if fx_sym else 1.0
        else:
            # a move made during a day happened near the open: start from the close before it
            day = t.replace(hour=0, minute=0, second=0, microsecond=0)
            cut = day - timedelta(seconds=1) if start else t
            px = intraday.at(self._day(sym), cut)
            fx = intraday.at(self._day(fx_sym), cut) if fx_sym else 1.0
        return px / fx if px and fx else None


def _mix_return(weights: dict[str, float], rets: dict[str, float]) -> float | None:
    w = {tk: x for tk, x in (weights or {}).items() if x and tk in rets}
    total = sum(w.values())
    return sum(x / total * rets[tk] for tk, x in w.items()) if total > 0 else None


def value_added(s: Session, now: datetime | None = None, fetch: bool = True) -> dict:
    """{rebalance id: {...}} for every move with a before and after mix; stored in the database."""
    now = _utc(now or datetime.now(timezone.utc))
    moves = list(s.scalars(select(Rebalance).order_by(Rebalance.ts)))
    stored = get_meta(s, VALUE_KEY, {}) or {}
    prices = Prices(s, fetch)
    out = {}
    for i, m in enumerate(moves):
        t0 = _utc(m.ts)
        t1 = _utc(moves[i + 1].ts) if i + 1 < len(moves) else now
        key = str(m.id)
        done = i + 1 < len(moves) and (now - t1).days > 5
        if done and key in stored:
            out[key] = stored[key]          # closed long ago: nothing changes any more
            continue
        if not m.weights_before:
            continue
        minute = (now - t0).total_seconds() < MINUTE_DAYS * 86400
        rets, missing = {}, []
        for tk in set(m.weights_before) | set(m.weights_after or {}):
            p0 = prices.at(tk, t0, minute, start=True)
            p1 = prices.at(tk, t1, minute, start=False)
            if p0 and p1:
                rets[tk] = p1 / p0 - 1
            elif max(m.weights_before.get(tk, 0), (m.weights_after or {}).get(tk, 0)) > 0:
                missing.append(tk)
        new, old = _mix_return(m.weights_after, rets), _mix_return(m.weights_before, rets)
        if new is None or old is None:
            continue
        out[key] = {"from": t0.isoformat(), "to": t1.isoformat(), "open": i + 1 == len(moves),
                    "new_mix": new, "old_mix": old, "value_added": new - old,
                    "priced": len(rets), "missing": sorted(missing), "prices": "minute" if minute else "daily"}
    set_meta(s, VALUE_KEY, out)
    return out


def collect_news(s: Session, now: datetime | None = None, per_ticker: int = 2, tickers: int = 6) -> int:
    """Headlines for recent moves that don't have them yet. Returns how many moves got news."""
    now = _utc(now or datetime.now(timezone.utc))
    have = get_meta(s, NEWS_KEY, {}) or {}
    n = 0
    for m in s.scalars(select(Rebalance).where(Rebalance.ts >= (now - timedelta(days=2)).replace(tzinfo=None))):
        key, t0 = str(m.id), _utc(m.ts)
        if have.get(key, {}).get("items") and (now - t0) > timedelta(hours=6):
            continue                       # got them; leave alone
        items = []
        top = sorted(m.changes or [], key=lambda c: -abs(c.get("delta") or 0))[:tickers]
        for c in top:
            sym = market.yahoo_symbol(s, c["ticker"])
            news = (market.get(s, "news", sym) or {}).get("items", []) if sym else []
            picked = 0
            for it in news:
                try:
                    when = datetime.fromisoformat((it.get("published") or "").replace("Z", "+00:00"))
                except ValueError:
                    continue
                if t0 - NEWS_BEFORE <= _utc(when) <= t0 + NEWS_AFTER:
                    items.append({**it, "ticker": c["ticker"], "kind": c.get("kind"), "delta": c.get("delta")})
                    picked += 1
                    if picked >= per_ticker:
                        break
        have[key] = {"items": items, "checked": now.isoformat()}
        n += 1 if items else 0
    set_meta(s, NEWS_KEY, have)
    return n
