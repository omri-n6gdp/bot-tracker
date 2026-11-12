"""Builds everything the website shows from the database. Read-only except rebuild()."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import analysis, market, stats
from .config import Settings
from .db import (
    BenchmarkPrice, BotTrade, Contribution, DailyValue, Dividend, HoldingSnapshot, InstrumentName,
    PieSnapshot, Rebalance, get_meta,
)
from .engine import DailyRow, compute_daily, live_unit_price

RANGES = {"1D": 1, "1W": 7, "1M": 31, "3M": 92, "6M": 183, "1Y": 366}


def symbol(ticker: str) -> str:
    """AAPL_US_EQ -> AAPL; VUSAl_EQ (London) -> VUSA."""
    base = ticker.split("_")[0]
    if ticker.endswith("l_EQ") and base.endswith("l"):
        base = base[:-1]
    return base


def _local_date(ts: datetime, tz: ZoneInfo) -> date:
    return ts.replace(tzinfo=timezone.utc).astimezone(tz).date()


def _iso(ts: datetime | None) -> str | None:
    return ts.replace(tzinfo=timezone.utc).isoformat() if ts else None


def latest_snapshot(s: Session) -> PieSnapshot | None:
    return s.scalars(select(PieSnapshot).order_by(PieSnapshot.ts.desc()).limit(1)).first()


def pie_tickers(s: Session) -> set[str]:
    """Every ticker that has ever been in the bot pie."""
    return set(s.scalars(select(HoldingSnapshot.ticker).distinct()))


# ---- computation -----------------------------------------------------------

def close_snapshots(s: Session, tz: ZoneInfo) -> dict[date, tuple[int, float]]:
    """date -> (snapshot id, value) for one snapshot per weekday: the 21:30 close if taken,
    else the day's last snapshot."""
    best: dict[date, tuple[bool, datetime, float, int]] = {}
    for sid, ts, value, is_close in s.execute(
        select(PieSnapshot.id, PieSnapshot.ts, PieSnapshot.value, PieSnapshot.is_daily_close).order_by(PieSnapshot.ts)
    ):
        d = _local_date(ts, tz)
        if d.weekday() >= 5:
            continue
        cand = (bool(is_close), ts, value, sid)
        if d not in best or cand[:2] >= best[d][:2]:
            best[d] = cand
    return {d: (v[3], v[2]) for d, v in sorted(best.items())}


def daily_closes(s: Session, tz: ZoneInfo) -> list[tuple[date, float]]:
    return [(d, v) for d, (_, v) in close_snapshots(s, tz).items()]


def flows(s: Session, tz: ZoneInfo | None = None) -> list[tuple[date, float]]:
    """Money into (+) / out of (-) the pie's holdings, per day: the bot pie's buys minus its
    sells (from order history), minus dividends (that money came from inside the pie)."""
    tz = tz or ZoneInfo("Europe/London")
    out: dict[date, float] = {}
    for ts, gbp in s.execute(select(BotTrade.filled_at, BotTrade.gbp)):
        d = _local_date(ts, tz)
        out[d] = out.get(d, 0.0) + gbp
    for ts, amt in s.execute(select(Dividend.paid_on, Dividend.amount)):
        d = _local_date(ts, tz)
        out[d] = out.get(d, 0.0) - amt
    return sorted(out.items())


def deposits(s: Session) -> list[tuple[date, float]]:
    """External money paid into the bot (deposits.csv). Display only, not performance."""
    return [(c.date, c.amount) for c in s.scalars(select(Contribution).order_by(Contribution.date))]


def rebuild(s: Session, cfg: Settings) -> list[DailyRow]:
    tz = ZoneInfo(cfg.timezone)
    rows = compute_daily(daily_closes(s, tz), flows(s, tz))
    s.execute(delete(DailyValue))
    for r in rows:
        s.add(DailyValue(date=r.date, value=r.value, units=r.units, unit_price=r.unit_price, flow=r.flow))
    return rows


def _daily(s: Session) -> list[DailyRow]:
    return [DailyRow(r.date, r.value, r.units, r.unit_price, r.flow)
            for r in s.scalars(select(DailyValue).order_by(DailyValue.date))]


def _live(s: Session, rows: list[DailyRow], snap: PieSnapshot | None, tz: ZoneInfo) -> float | None:
    if not rows:
        return None
    if snap is None:
        return rows[-1].unit_price
    last = rows[-1]
    if _local_date(snap.ts, tz) == last.date:
        return last.unit_price if snap.value == last.value else live_unit_price(snap.value, rows[-2] if len(rows) > 1 else last, last.flow if len(rows) > 1 else 0.0)
    pending = sum(a for d, a in flows(s, tz) if d > last.date)
    return live_unit_price(snap.value, last, pending)


def benchmark_series(s: Session, cfg: Settings) -> dict[str, tuple[str, list[tuple[date, float]]]]:
    out = {}
    for ticker, name in cfg.benchmarks:
        rows = s.execute(select(BenchmarkPrice.date, BenchmarkPrice.close)
                         .where(BenchmarkPrice.ticker == ticker).order_by(BenchmarkPrice.date)).all()
        if rows:
            out[ticker] = (name, [(d, c) for d, c in rows])
    return out


def _names(s: Session) -> dict[str, str]:
    return {n.ticker: n.name for n in s.scalars(select(InstrumentName))}


# ---- API payloads ----------------------------------------------------------

def summary(s: Session, cfg: Settings, now: datetime | None = None) -> dict:
    tz = ZoneInfo(cfg.timezone)
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    snap, rows = latest_snapshot(s), _daily(s)
    if snap is None or not rows:
        return {"ready": False, "pie_name": cfg.display_name or get_meta(s, "pie_name")}
    series = [(r.date, r.unit_price) for r in rows]
    price_now = _live(s, rows, snap, tz)
    today = _local_date(snap.ts, tz)
    if today.weekday() >= 5:  # at the weekend, "today" means the last trading day
        today = rows[-1].date
    prev_close = next((r for r in reversed(rows) if r.date < today), None)
    fl = flows(s, tz)
    first_day = rows[0].date
    returns = {k: (stats.return_since(series, today - timedelta(days=v), price_now)
                   if today - timedelta(days=v) >= first_day else None)  # longer than the history
               for k, v in RANGES.items()}
    returns["1D"] = (price_now / prev_close.unit_price - 1) if prev_close else None
    returns["YTD"] = stats.return_since(series, date(today.year - 1, 12, 31), price_now)
    returns["ALL"] = price_now / rows[0].unit_price - 1
    day_change = None
    if prev_close:
        day_change = snap.value - prev_close.value - sum(a for d, a in fl if d > prev_close.date)
    total_value = snap.value + (snap.cash or 0.0)
    dep = deposits(s)
    if dep:
        net_deposits, dep_source = sum(a for _, a in dep), "ledger"
    else:  # no ledger: money that went into the holdings since the start
        net_deposits, dep_source = rows[0].value + sum(a for d, a in fl if d > first_day), "estimated"
    bench = {}
    for ticker, (name, ser) in benchmark_series(s, cfg).items():
        b0 = stats.price_on_or_before(ser, first_day)
        b1 = ser[-1][1]
        if b0 and ser[0][0] <= first_day + timedelta(days=7):
            bench[ticker] = {"name": name, "since_start": b1 / b0 - 1}
    age = (now - snap.ts).total_seconds()
    return {
        "ready": True,
        "pie_name": cfg.display_name or get_meta(s, "pie_name"),
        "value": total_value,
        "holdings_value": snap.value,
        "cash": snap.cash,
        "unit_price": price_now,
        "day_change": day_change,
        "day_change_pct": returns["1D"],
        "returns": returns,
        "net_deposits": net_deposits,
        "deposits_source": dep_source,
        "profit": total_value - net_deposits,
        "return_on_deposits": (total_value / net_deposits - 1) if net_deposits else None,
        "inception": first_day.isoformat(),
        "days_tracked": len(rows),
        "holdings_count": len(snap.holdings),
        "benchmarks": bench,
        "as_of": _iso(snap.ts),
        "stale": age > cfg.stale_after_s,
        "source": snap.source,
        "benchmark_name": cfg.benchmark_name,
        "dividends_total": sum(a for (a,) in s.execute(select(Dividend.amount))),
    }


def history(s: Session, cfg: Settings, rng: str = "3M") -> dict:
    tz = ZoneInfo(cfg.timezone)
    rng = rng.upper()
    if rng == "1D":
        snap = latest_snapshot(s)
        if snap is None:
            return {"range": rng, "points": []}
        day = _local_date(snap.ts, tz)
        rows = _daily(s)
        prev = next((r for r in reversed(rows) if r.date < day), None)
        start = datetime.combine(day, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
        snaps = s.execute(select(PieSnapshot.ts, PieSnapshot.value).where(PieSnapshot.ts >= start).order_by(PieSnapshot.ts)).all()
        return {"range": rng, "intraday": True, "baseline": prev.value if prev else None,
                "points": [{"t": _iso(t), "value": v} for t, v in snaps]}
    rows = _daily(s)
    if not rows:
        return {"range": rng, "points": []}
    total_value, new_money = _value_with_cash(s, tz, rows)
    last = rows[-1].date
    if rng == "YTD":
        start = date(last.year, 1, 1)
    elif rng in RANGES:
        start = last - timedelta(days=RANGES[rng])
    else:
        start = rows[0].date
    keep = [i for i, r in enumerate(rows) if r.date >= start] or [len(rows) - 1]
    rows = [rows[i] for i in keep]
    total_value = [total_value[i] for i in keep]
    new_money = [new_money[i] for i in keep]
    base_price, base_value = rows[0].unit_price, total_value[0]
    days = [r.date for r in rows]

    # benchmarks: % rebased to the range start, and "same money" in GBP (only new money buys in)
    bench_meta, bench_pct, bench_val = [], {}, {}
    for ticker, (name, ser) in benchmark_series(s, cfg).items():
        if ser[0][0] > days[0] + timedelta(days=7):
            continue  # doesn't cover the start: better no line than a wrong one
        al = [stats.price_on_or_before(ser, d) for d in days]
        if not al[0]:
            continue
        bench_meta.append({"key": ticker, "name": name})
        bench_pct[ticker] = [(x / al[0] - 1) if x else None for x in al]
        units, vals = base_value / al[0], []
        for i, px in enumerate(al):
            if i > 0 and px:
                units += new_money[i] / px
            vals.append(units * px if px else None)
        bench_val[ticker] = vals

    dep = deposits(s)
    cum_dep = None
    if dep:
        cum_dep, total, j = [], 0.0, 0
        dep_sorted = sorted(dep)
        for d in days:
            while j < len(dep_sorted) and dep_sorted[j][0] <= d:
                total += dep_sorted[j][1]
                j += 1
            cum_dep.append(total)

    return {
        "range": rng,
        "intraday": False,
        "benchmarks": bench_meta,
        "benchmark_name": cfg.benchmark_name,
        "has_deposits": cum_dep is not None,
        "points": [{
            "t": r.date.isoformat(), "value": total_value[i], "holdings_value": r.value, "unit_price": r.unit_price,
            "pie": r.unit_price / base_price - 1, "flow": r.flow or None,
            "deposits": cum_dep[i] if cum_dep else None,
            "bench": {k: v[i] for k, v in bench_pct.items()},
            "bench_value": {k: v[i] for k, v in bench_val.items()},
            # kept for older clients
            "benchmark": next(iter(bench_pct.values()))[i] if bench_pct else None,
        } for i, r in enumerate(rows)],
    }


def _value_with_cash(s: Session, tz: ZoneInfo, rows: list[DailyRow]) -> tuple[list[float], list[float]]:
    """Pie value including cash at each close, and the new outside money per day.

    Live snapshots know the pie's cash. For rebuilt days it's estimated: money from sells
    (or dividends) that hasn't been reinvested yet. With F = running sum of trade flows and
    M = its running maximum, cash = M - F, and new money on a day = the rise in M.
    """
    closes = close_snapshots(s, tz)
    sid_by_day = {d: sid for d, (sid, _) in closes.items()}
    cash_by_sid = dict(s.execute(select(PieSnapshot.id, PieSnapshot.cash)
                                 .where(PieSnapshot.source != "backfill")).all())
    totals, new_money = [], []
    F = M = 0.0
    for i, r in enumerate(rows):
        f = r.flow if i > 0 else 0.0
        F += f
        prev_M = M
        M = max(M, F)
        new_money.append(M - prev_M)
        live_cash = cash_by_sid.get(sid_by_day.get(r.date))
        cash = live_cash if live_cash is not None else (M - F)
        totals.append(r.value + (cash or 0.0))
    return totals, new_money


def _per_share_history(s: Session, tz: ZoneInfo, days_back: int = 45) -> dict[str, list[tuple[date, float]]]:
    """Price per share (GBP) at each daily close for every ticker, from the snapshots."""
    closes = close_snapshots(s, tz)
    keep = list(closes.items())[-days_back:]
    ids = {sid: d for d, (sid, _) in keep}
    out: dict[str, list[tuple[date, float]]] = {}
    if not ids:
        return out
    for sid, tk, q, v in s.execute(select(HoldingSnapshot.snapshot_id, HoldingSnapshot.ticker,
                                          HoldingSnapshot.quantity, HoldingSnapshot.value)
                                   .where(HoldingSnapshot.snapshot_id.in_(list(ids)))):
        if q and v:
            out.setdefault(tk, []).append((ids[sid], v / q))
    for tk in out:
        out[tk].sort()
    return out


def _held_since(s: Session, tz: ZoneInfo) -> dict[str, date]:
    """For each ticker, the first close of its current unbroken run in the pie."""
    closes = close_snapshots(s, tz)
    order = list(closes.items())
    ids = {sid: i for i, (_, (sid, _)) in enumerate(order)}
    present: dict[str, set[int]] = {}
    for sid, tk in s.execute(select(HoldingSnapshot.snapshot_id, HoldingSnapshot.ticker)
                             .where(HoldingSnapshot.snapshot_id.in_(list(ids)))):
        present.setdefault(tk, set()).add(ids[sid])
    out = {}
    last = len(order) - 1
    for tk, idx in present.items():
        if last not in idx:
            continue
        i = last
        while i - 1 in idx:
            i -= 1
        out[tk] = order[i][0]
    return out


def holdings(s: Session, cfg: Settings | None = None) -> dict:
    cfg = cfg or Settings()
    tz = ZoneInfo(cfg.timezone)
    snap = latest_snapshot(s)
    if snap is None:
        return {"holdings": []}
    names = _names(s)
    snap_day = _local_date(snap.ts, tz)
    hist = _per_share_history(s, tz)
    since = _held_since(s, tz)
    total = sum(h.value or 0.0 for h in snap.holdings) or 1.0
    ymap = get_meta(s, "yahoo_map", {}) or {}
    out, sectors = [], {}
    for h in sorted(snap.holdings, key=lambda h: -(h.value or 0.0)):
        px_now = (h.value / h.quantity) if h.quantity and h.value else None
        series = hist.get(h.ticker, [])
        prev = next((p for d, p in reversed(series) if d < snap_day), None)
        day_pct = (px_now / prev - 1) if px_now and prev else None
        held_since = since[h.ticker].isoformat() if h.ticker in since else None
        profile = None
        sym = ymap.get(h.ticker)
        if sym:
            profile = market.get(s, "profile", sym, allow_fetch=False)
        sector = (profile or {}).get("sector") or ("Fund" if (profile or {}).get("quoteType") == "ETF" else None)
        weight = (h.value or 0.0) / total
        sectors[sector or "Other"] = sectors.get(sector or "Other", 0.0) + weight
        out.append({
            "ticker": h.ticker,
            "symbol": symbol(h.ticker),
            "name": names.get(h.ticker) or symbol(h.ticker),
            "quantity": h.quantity,
            "value": h.value,
            "price": px_now,
            "weight": weight,
            "target": h.expected_share,
            "invested": h.invested,
            "pl": h.result,
            "pl_pct": (h.result / h.invested) if h.result is not None and h.invested else None,
            "day_pct": day_pct,
            "day_gbp": (h.quantity * (px_now - prev)) if px_now and prev and h.quantity else None,
            "spark": [round(p, 6) for _, p in series[-30:]],
            "held_since": held_since,
            "sector": sector,
            "analyst": _analyst(profile),
        })
    movers = sorted((x for x in out if x["day_pct"] is not None), key=lambda x: x["day_pct"])
    return {
        "as_of": _iso(snap.ts), "cash": snap.cash, "holdings": out,
        "sectors": sorted(({"sector": k, "weight": v} for k, v in sectors.items()), key=lambda x: -x["weight"]),
        "movers": {"up": [m["ticker"] for m in reversed(movers[-3:]) if m["day_pct"] > 0],
                   "down": [m["ticker"] for m in movers[:3] if m["day_pct"] < 0]},
    }


RATING_LABELS = {"strong_buy": "Strong buy", "buy": "Buy", "hold": "Hold", "underperform": "Underperform", "sell": "Sell"}


def _rating_from_score(score: float | None) -> str | None:
    """Yahoo's 1-5 scale: 1 = strong buy, 3 = hold, 5 = sell."""
    if score is None:
        return None
    return ("Strong buy" if score <= 1.5 else "Buy" if score <= 2.5 else "Hold" if score <= 3.5
            else "Underperform" if score <= 4.5 else "Sell")


def _analyst(p: dict | None) -> dict | None:
    """Analyst consensus from a cached Yahoo profile. Prices are in the stock's own currency;
    the *_pct fields are each target against the price now. Yahoo sometimes reports the rating
    as "none" while still giving a score, so the label falls back to the score."""
    p = p or {}
    price = p.get("currentPrice") or p.get("regularMarketPrice")
    score, mean, n = p.get("recommendationMean"), p.get("targetMeanPrice"), p.get("numberOfAnalystOpinions")
    rating = RATING_LABELS.get(p.get("recommendationKey") or "") or _rating_from_score(score)
    if not rating and not mean:
        return None
    rel = (lambda t: (t / price - 1) if t and price else None)
    return {
        "rating": rating, "score": score, "analysts": n, "thin": bool(n is not None and n < 3),
        "low": p.get("targetLowPrice"), "mean": mean, "high": p.get("targetHighPrice"),
        "price": price, "currency": p.get("currency"),
        "upside": rel(mean), "low_pct": rel(p.get("targetLowPrice")), "high_pct": rel(p.get("targetHighPrice")),
    }


def correlations(s: Session, cfg: Settings, days_back: int = 60, min_obs: int = 15) -> dict:
    """How the holdings' daily moves (in pounds) line up with each other over the last ~3 months."""
    tz = ZoneInfo(cfg.timezone)
    held = [h for h in holdings(s, cfg).get("holdings", []) if (h.get("value") or 0) > 0]
    held.sort(key=lambda h: (h.get("sector") or "~", -(h.get("weight") or 0)))
    hist = _per_share_history(s, tz, days_back=days_back)
    rets: dict[str, dict] = {}
    for h in held:
        series = hist.get(h["ticker"], [])
        rets[h["ticker"]] = {d1: p1 / p0 - 1 for (d0, p0), (d1, p1) in zip(series, series[1:]) if p0}

    def corr(a: dict, b: dict) -> float | None:
        days = sorted(set(a) & set(b))
        if len(days) < min_obs:
            return None
        x, y = [a[d] for d in days], [b[d] for d in days]
        mx, my = sum(x) / len(x), sum(y) / len(y)
        sxx, syy = sum((v - mx) ** 2 for v in x), sum((v - my) ** 2 for v in y)
        if sxx <= 0 or syy <= 0:
            return None
        return round(max(-1.0, min(1.0, sum((u - mx) * (v - my) for u, v in zip(x, y)) / (sxx * syy) ** 0.5)), 3)

    tickers = [h["ticker"] for h in held]
    m = [[1.0 if i == j else None for j in range(len(tickers))] for i in range(len(tickers))]
    for i in range(len(tickers)):
        for j in range(i + 1, len(tickers)):
            m[i][j] = m[j][i] = corr(rets[tickers[i]], rets[tickers[j]])
    off = [m[i][j] for i in range(len(tickers)) for j in range(i + 1, len(tickers)) if m[i][j] is not None]
    return {"tickers": tickers, "symbols": [h["symbol"] for h in held], "names": [h["name"] for h in held],
            "sectors": [h.get("sector") for h in held], "matrix": m,
            "average": round(sum(off) / len(off), 3) if off else None, "days": days_back}


def rebalances(s: Session, cfg: Settings, limit: int = 100) -> dict:
    rows = _daily(s)
    series = [(r.date, r.unit_price) for r in rows]
    tz = ZoneInfo(cfg.timezone)
    current = _live(s, rows, latest_snapshot(s), tz) if rows else None
    events = list(s.scalars(select(Rebalance).order_by(Rebalance.ts.desc()).limit(limit)))
    names = _names(s)
    days_sorted = sorted(_local_date(e.ts, tz) for e in events)
    outcomes = dict(zip(days_sorted, stats.rebalance_outcomes(series, days_sorted, current))) if current else {}
    return {"rebalances": [{
        "ts": _iso(e.ts),
        "source": e.source,
        "return_since": outcomes.get(_local_date(e.ts, tz)),
        "changes": [{**c, "symbol": symbol(c["ticker"]), "name": names.get(c["ticker"])} for c in e.changes],
    } for e in events]}


def activity(s: Session, cfg: Settings, limit: int = 40) -> dict:
    """Recent bot trades (grouped per day), dividends and moves, newest first."""
    tz = ZoneInfo(cfg.timezone)
    names = _names(s)
    by_day: dict[date, dict] = {}
    for t in s.scalars(select(BotTrade).order_by(BotTrade.filled_at.desc()).limit(400)):
        d = _local_date(t.filled_at, tz)
        e = by_day.setdefault(d, {"date": d.isoformat(), "kind": "trades", "bought": 0.0, "sold": 0.0, "buys": [], "sells": []})
        if t.gbp >= 0:
            e["bought"] += t.gbp
            e["buys"].append(symbol(t.ticker))
        else:
            e["sold"] += -t.gbp
            e["sells"].append(symbol(t.ticker))
    events = []
    for e in by_day.values():
        e["buys"], e["sells"] = sorted(set(e["buys"])), sorted(set(e["sells"]))
        events.append(e)
    for dv in s.scalars(select(Dividend).order_by(Dividend.paid_on.desc()).limit(50)):
        events.append({"date": _local_date(dv.paid_on, tz).isoformat(), "kind": "dividend", "amount": dv.amount,
                       "symbol": symbol(dv.ticker), "name": names.get(dv.ticker)})
    events.sort(key=lambda e: (e["date"], e["kind"] == "trades"), reverse=True)
    return {"events": events[:limit]}


def stats_payload(s: Session, cfg: Settings) -> dict:
    rows = _daily(s)
    if len(rows) < 2:
        return {"ready": False}
    series = [(r.date, r.unit_price) for r in rows]
    prices = [p for _, p in series]
    best, worst = stats.best_worst_day(series)
    reb = rebalances(s, cfg)["rebalances"]
    outcomes = [r["return_since"] for r in reb if r["return_since"] is not None]
    divs = list(s.scalars(select(Dividend).order_by(Dividend.paid_on.desc())))
    return {
        "ready": True,
        "max_drawdown": stats.max_drawdown(prices),
        "volatility": stats.annualised_volatility(prices),
        "sharpe": stats.sharpe(prices, cfg.risk_free_rate),
        "cagr": stats.cagr(series),
        "best_day": {"date": best[0].isoformat(), "return": best[1]} if best else None,
        "worst_day": {"date": worst[0].isoformat(), "return": worst[1]} if worst else None,
        "days_tracked": len(rows),
        "rebalance_count": len(reb),
        "rebalance_win_rate": (sum(o > 0 for o in outcomes) / len(outcomes)) if outcomes else None,
        "monthly": stats.monthly_returns(series),
        "drawdown": [{"t": d.isoformat(), "dd": v} for d, v in stats.drawdown_series(series)],
        "dividends_total": sum(d.amount for d in divs),
        "dividends": [{"date": d.paid_on.date().isoformat(), "ticker": symbol(d.ticker), "amount": d.amount} for d in divs[:20]],
    }


def analysis_payload(s: Session, cfg: Settings) -> dict:
    rows = _daily(s)
    series = [(r.date, r.unit_price) for r in rows]
    tz = ZoneInfo(cfg.timezone)
    move_days = [_local_date(ts, tz) for (ts,) in s.execute(select(Rebalance.ts))]
    a = analysis.analyse(series, benchmark_series(s, cfg), move_days, cfg.risk_free_rate)
    # turnover per move: half the sum of absolute weight changes
    turns = []
    for (ch,) in s.execute(select(Rebalance.changes)):
        turns.append(sum(abs(c.get("delta") or 0) for c in ch) / 2)
    if a.get("ready"):
        a["moves"]["avg_turnover"] = (sum(turns) / len(turns)) if turns else None
        a["risk_free_rate"] = cfg.risk_free_rate
    return a


def stock_detail(s: Session, cfg: Settings, ticker: str) -> dict | None:
    """Everything about one stock the bot has held. None if it was never in the pie."""
    if ticker not in pie_tickers(s):
        return None
    tz = ZoneInfo(cfg.timezone)
    names = _names(s)
    snap = latest_snapshot(s)
    held = next((h for h in (snap.holdings if snap else []) if h.ticker == ticker), None)
    total = sum(h.value or 0.0 for h in snap.holdings) if snap else 0.0
    closes = close_snapshots(s, tz)
    ids = {sid: d for d, (sid, _) in closes.items()}
    weight_hist, pie_prices = [], []
    for sid, q, v in s.execute(select(HoldingSnapshot.snapshot_id, HoldingSnapshot.quantity, HoldingSnapshot.value)
                               .where(HoldingSnapshot.ticker == ticker, HoldingSnapshot.snapshot_id.in_(list(ids)))):
        weight_hist.append((ids[sid], q, v))
        if q and v:
            pie_prices.append({"t": ids[sid].isoformat(), "close": v / q})
    weight_hist.sort()
    pie_prices.sort(key=lambda p: p["t"])
    totals = {d: val for d, (_, val) in closes.items()}
    trades = [{"t": _iso(t.filled_at), "side": t.side, "quantity": abs(t.quantity), "gbp": abs(t.gbp),
               "price_gbp": abs(t.gbp / t.quantity) if t.quantity else None}
              for t in s.scalars(select(BotTrade).where(BotTrade.ticker == ticker).order_by(BotTrade.filled_at))]
    divs = [{"date": _local_date(d.paid_on, tz).isoformat(), "amount": d.amount}
            for d in s.scalars(select(Dividend).where(Dividend.ticker == ticker).order_by(Dividend.paid_on))]
    bought = sum(t["gbp"] for t in trades if t["side"] == "BUY")
    sold = sum(t["gbp"] for t in trades if t["side"] == "SELL")
    now_val = held.value if held and held.value else 0.0
    sym = market.yahoo_symbol(s, ticker)
    profile = market.get(s, "profile", sym) if sym else None
    news = market.get(s, "news", sym) if sym else None
    hist = market.get(s, "history", sym) if sym else None
    return {
        "ticker": ticker,
        "symbol": symbol(ticker),
        "yahoo": sym,
        "name": names.get(ticker) or (profile or {}).get("longName") or symbol(ticker),
        "held_now": held is not None,
        "position": {
            "quantity": held.quantity if held else 0.0,
            "value": now_val,
            "weight": (now_val / total) if total and held else 0.0,
            "target": held.expected_share if held else None,
            "invested": held.invested if held else None,
            "unrealised_pl": held.result if held else None,
            # lifetime in this pie: what it's worth + what it returned, against what went in
            "lifetime_pl": now_val + sold + sum(d["amount"] for d in divs) - bought,
            "bought_gbp": bought, "sold_gbp": sold,
        },
        "weight_history": [{"t": d.isoformat(), "weight": (v / totals[d]) if totals.get(d) else None,
                            "quantity": q} for d, q, v in weight_hist],
        "trades": trades,
        "dividends": divs,
        "profile": profile or {},
        "analyst": _analyst(profile),
        "news": (news or {}).get("items", []),
        "prices": (hist or {}).get("points", []),
        "pie_prices": pie_prices,  # GBP per share at each close, from the pie itself
    }


def health(s: Session, cfg: Settings, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    snap = latest_snapshot(s)
    return {
        "last_snapshot": _iso(snap.ts) if snap else None,
        "stale": (snap is None) or (now - snap.ts).total_seconds() > cfg.stale_after_s,
        "source": snap.source if snap else None,
        "backfill": get_meta(s, "backfill"),
    }


def news(s: Session, cfg: Settings, limit: int = 30) -> dict:
    """Recent headlines across the stocks held now (from the poller's cache), newest first.

    The same story often appears under several of the pie's stocks; it's shown once,
    tagged with each of them."""
    from . import market
    snap = latest_snapshot(s)
    if snap is None:
        return {"items": []}
    held = [h for h in snap.holdings if (h.value or 0) > 0] or list(snap.holdings)
    names = _names(s)
    by_ticker = market.cached_news(s, [h.ticker for h in held])
    merged: dict[str, dict] = {}
    for t, items in by_ticker.items():
        sym = symbol(t)
        for it in items:
            key = (it.get("url") or it.get("title") or "").split("?")[0]
            if not key:
                continue
            row = merged.setdefault(key, {**it, "symbols": [], "tickers": []})
            if sym not in row["symbols"]:
                row["symbols"].append(sym)
                row["tickers"].append(t)
    items = sorted(merged.values(), key=lambda x: x.get("published") or "", reverse=True)[:limit]
    for it in items:
        it["names"] = [names.get(t, symbol(t)) for t in it["tickers"]]
    return {"items": items, "stocks_covered": len(by_ticker), "stocks_held": len(held)}


def vol_surface(s: Session, ticker: str) -> dict | None:
    """Implied volatility surface for a stock the pie holds (None if it never has)."""
    from . import market
    if ticker not in pie_tickers(s):
        return None
    sym = market.yahoo_symbol(s, ticker)
    data = market.get(s, "vol", sym) if sym else None
    return {"ticker": ticker, "symbol": symbol(ticker), "yahoo": sym, **(data or {"available": False})}
