"""Rebuild the bot pie's history from Trading 212 order history + Yahoo prices.

    python -m tracker backfill [--start YYYY-MM-DD]

1. Read today's pie holdings and the account's full order history.
2. Work out which orders were the bot pie's (attribution.py).
3. Walk back from today's holdings to get the pie's holdings at the end of every day.
4. Price those holdings in GBP each day (prices.py) -> daily values.
5. Store the bot's trades (money in/out) and reconstruct its past moves.

Safe to run again: it replaces previous backfilled rows and keeps live snapshots.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from . import service
from .attribution import attribute, holdings_timeline, trades_from_orders
from .config import ROOT, Settings
from .db import BotTrade, HoldingSnapshot, InstrumentName, PieSnapshot, Rebalance, set_meta
from .prices import resolve_and_price
from .rebalance import diff_targets

log = logging.getLogger(__name__)
ORDERS_CACHE = ROOT / "data" / "orders_cache.json"
MOVE_TOLERANCE = 0.01  # a weight change under 1 point isn't reported as part of a move


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def fetch_inputs(client, cfg: Settings, use_cache: bool = False) -> dict:
    """Everything the backfill needs from Trading 212 (cached to data/orders_cache.json)."""
    if use_cache and ORDERS_CACHE.exists():
        return json.loads(ORDERS_CACHE.read_text())
    pie = client.pie(cfg.pie_id)
    others = set()
    for p in client.pies():
        if p.id != cfg.pie_id:
            others |= {i.ticker for i in client.pie(p.id).instruments}
    log.info("reading order history (about a minute)…")
    orders = [o.model_dump() for o in client.orders()]
    data = {"pie": pie.model_dump(), "other_pie_tickers": sorted(others), "orders": orders,
            "fetched_at": datetime.now(timezone.utc).isoformat()}
    ORDERS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    ORDERS_CACHE.write_text(json.dumps(data))
    return data


def run(Session, cfg: Settings, data: dict, start: date | None = None, today: date | None = None,
        price_fn=resolve_and_price, benchmark_fn=None) -> dict:
    tz = ZoneInfo(cfg.timezone)
    today = today or datetime.now(tz).date()
    pie = data["pie"]
    owned = {i["ticker"]: i["ownedQuantity"] or 0.0 for i in pie["instruments"]}
    exclude = set(data.get("other_pie_tickers") or []) - set(owned)

    trades = trades_from_orders(data["orders"])
    chosen, _, problems = attribute(trades, owned, exclude)
    at_end_of = holdings_timeline(trades, chosen, owned)

    if start is None:
        if cfg.bot_start_date:
            start = date.fromisoformat(cfg.bot_start_date)
        else:
            created = (pie.get("settings") or {}).get("creationDate")
            if isinstance(created, (int, float)):
                start = datetime.fromtimestamp(created, tz=timezone.utc).date()
            elif created:
                start = _parse(created).date()
            else:
                start = min(_parse(t.filled_at).date() for t in trades if t.fill_id in chosen)

    mine = [t for t in trades if t.fill_id in chosen]
    since = [t for t in mine if _parse(t.filled_at).date() > start]

    with Session() as s:
        first_live = s.scalar(select(PieSnapshot.ts).where(PieSnapshot.source.in_(("pie", "fallback")))
                              .order_by(PieSnapshot.ts).limit(1))
    last_day = min(today - timedelta(days=1),
                   (service._local_date(first_live, tz) - timedelta(days=1)) if first_live else today)
    days = [start + timedelta(days=i) for i in range((last_day - start).days + 1)]
    days = [d for d in days if d.weekday() < 5]

    tickers = set(at_end_of(start.isoformat())) | {t.ticker for t in since}
    inst: dict[str, dict] = {}
    for t in trades:
        if t.ticker in tickers:
            i = inst.setdefault(t.ticker, {"isin": t.isin, "currency": t.currency, "name": t.name, "fills": []})
            if t.price:
                i["fills"].append((_parse(t.filled_at).date(), t.price))
    prices, report = price_fn(inst, start, today)

    # fallback price for anything Yahoo couldn't match: the GBP price of its fills
    fill_px: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for t in mine:
        if t.quantity:
            fill_px[t.ticker].append((_parse(t.filled_at).date(), abs(t.gbp / t.quantity)))

    def price_on(ticker: str, d: date) -> float | None:
        series = prices.get(ticker) or {}
        for back in range(0, 7):
            v = series.get(d - timedelta(days=back))
            if v is not None:
                return v
        pts = [p for fd, p in sorted(fill_px.get(ticker, [])) if fd <= d] or [p for _, p in sorted(fill_px.get(ticker, []))]
        return pts[-1] if pts else None

    written, weights_by_day = 0, {}
    with Session() as s:
        old = select(PieSnapshot.id).where(PieSnapshot.source == "backfill")
        s.execute(delete(HoldingSnapshot).where(HoldingSnapshot.snapshot_id.in_(old)))
        s.execute(delete(PieSnapshot).where(PieSnapshot.source == "backfill"))
        s.execute(delete(Rebalance).where(Rebalance.source == "backfill"))
        s.execute(delete(BotTrade))
        for t in mine:
            s.add(BotTrade(fill_id=t.fill_id, order_id=t.order_id, batch=t.batch, filled_at=_parse(t.filled_at),
                           ticker=t.ticker, side=t.side, quantity=t.quantity, gbp=t.gbp))
        for tk, info in inst.items():
            if info.get("name"):
                s.merge(InstrumentName(ticker=tk, name=info["name"], currency=info.get("currency")))

        for d in days:
            held = at_end_of(d.isoformat())
            vals = {tk: q * (price_on(tk, d) or 0.0) for tk, q in held.items()}
            total = sum(vals.values())
            if total <= 0:
                continue
            ts = datetime.combine(d, time(21, 30), tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
            snap = PieSnapshot(ts=ts, value=total, holdings_value=total, cash=0.0, is_daily_close=True, source="backfill")
            for tk, q in held.items():
                snap.holdings.append(HoldingSnapshot(ticker=tk, quantity=q, value=vals[tk], current_share=vals[tk] / total))
            s.add(snap)
            weights_by_day[d] = {tk: v / total for tk, v in vals.items()}
            written += 1

        # past moves: days the bot traded, comparing weights before and after
        trade_days = sorted({_parse(t.filled_at).astimezone(timezone.utc).date() for t in since})
        moves = 0
        for d in trade_days:
            prev = [x for x in weights_by_day if x < d]
            if not prev or d not in weights_by_day:
                continue
            before, after = weights_by_day[max(prev)], weights_by_day[d]
            day_trades = [t for t in since if _parse(t.filled_at).date() == d]
            new_or_sold = any(t.side == "SELL" for t in day_trades) or any(t.ticker not in before for t in day_trades)
            changes = diff_targets(before, after, tol=MOVE_TOLERANCE)
            if changes and new_or_sold:
                last_fill = max(_parse(t.filled_at) for t in day_trades)
                s.add(Rebalance(ts=last_fill, weights_before=before, weights_after=after, changes=changes, source="backfill"))
                moves += 1

        from .db import get_meta
        ymap = dict(get_meta(s, "yahoo_map", {}) or {})
        ymap.update({t: r["symbol"] for t, r in report.items() if r.get("symbol")})
        set_meta(s, "yahoo_map", ymap)
        imap = dict(get_meta(s, "isin_map", {}) or {})
        imap.update({t: i["isin"] for t, i in inst.items() if i.get("isin")})
        set_meta(s, "isin_map", imap)
        set_meta(s, "backfill", {"start": start.isoformat(), "days": written, "moves": moves,
                                 "unmatched_prices": sorted(t for t, r in report.items() if "error" in r),
                                 "ran_at": datetime.now(timezone.utc).isoformat()})
        s.commit()

    bench = "skipped"
    try:
        if benchmark_fn is None:
            from .poller import update_benchmark_prices
            benchmark_fn = update_benchmark_prices
        bench = benchmark_fn(Session, cfg)
    except Exception as exc:  # the history is still useful without the comparison line
        log.warning("benchmark update failed: %s", exc)
        bench = f"failed ({exc})"

    with Session() as s:
        service.rebuild(s, cfg)
        s.commit()

    return {"benchmark_added": bench,"start": start.isoformat(), "days": written, "moves": moves, "trades": len(mine),
            "tickers": len(tickers), "attribution_problems": problems[:3],
            "unmatched_prices": {t: r for t, r in report.items() if "error" in r}}
