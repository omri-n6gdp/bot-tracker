"""Builds everything the website shows from the database. Read-only except rebuild()."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import analysis, intraday, market, stats, yahoo_closes
from .config import Settings
from .db import (
    BenchmarkPrice, BotTrade, Contribution, DailyValue, Dividend, HoldingSnapshot, InstrumentName,
    PieSnapshot, Rebalance, get_meta, utcnow,
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


def closes_with_cash(s: Session, tz: ZoneInfo) -> list[tuple[date, float, float]]:
    """(date, holdings value, cash) at each close. Live snapshots know the pie's cash (money
    that couldn't be split into the holdings, sale proceeds, dividends). For rebuilt days it's
    estimated: money from sells (or dividends) that hasn't been reinvested yet. With F = running
    sum of trade flows and M = its running maximum, cash = M - F."""
    closes = close_snapshots(s, tz)
    cash_by_sid = dict(s.execute(select(PieSnapshot.id, PieSnapshot.cash)
                                 .where(PieSnapshot.source != "backfill")).all())
    fl = flows(s, tz)
    out, F, M, prev = [], 0.0, 0.0, None
    for d, (sid, v) in closes.items():
        if prev is not None:
            F += sum(a for fd, a in fl if prev < fd <= d)
            M = max(M, F)
        cash = cash_by_sid.get(sid)
        out.append((d, v, cash if cash is not None else M - F))
        prev = d
    return out


def rebuild(s: Session, cfg: Settings) -> list[DailyRow]:
    """Daily values of the whole pie, cash included, so money sitting uninvested counts.
    Money into the pie on a day = the bot's buys - sells - dividends + the change in cash."""
    tz = ZoneInfo(cfg.timezone)
    cl = closes_with_cash(s, tz)
    cash_moves = [(d, c - cl[i - 1][2]) for i, (d, _, c) in enumerate(cl) if i > 0]
    rows = compute_daily([(d, v + c) for d, v, c in cl], flows(s, tz) + cash_moves)
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
    now = snap.value + (snap.cash or 0.0)
    if _local_date(snap.ts, tz) == last.date:
        return last.unit_price if abs(now - last.value) < 1e-9 else live_unit_price(now, rows[-2] if len(rows) > 1 else last, last.flow if len(rows) > 1 else 0.0)
    cl = closes_with_cash(s, tz)
    last_cash = cl[-1][2] if cl else 0.0
    pending = sum(a for d, a in flows(s, tz) if d > last.date) + (snap.cash or 0.0) - last_cash
    return live_unit_price(now, last, pending)


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
    total_value = snap.value + (snap.cash or 0.0)
    if prev_close:
        day_change = prev_close.units * (price_now - prev_close.unit_price)
    else:  # the pie's first day: today's holdings against yesterday's market closes
        prev_val = _yesterdays_value(s, snap, tz)
        if prev_val:
            day_change = total_value - prev_val
            returns["1D"] = day_change / prev_val
    dep = deposits(s)
    if dep:
        net_deposits, dep_source = sum(a for _, a in dep), "ledger"
    else:  # no ledger: money that went into the pie since the start
        net_deposits, dep_source = rows[0].value + sum(r.flow for r in rows[1:]), "estimated"
    bench = {}
    for ticker, (name, ser) in benchmark_series(s, cfg).items():
        b0 = stats.price_on_or_before(ser, first_day)
        b1 = ser[-1][1]
        if b0 and ser[0][0] <= first_day + timedelta(days=7):
            bench[ticker] = {"name": name, "since_start": b1 / b0 - 1}
    started = None
    if len(rows) < DAILY_STATS_FROM:
        # early days: measure from the first reading with stocks in the pie, not the first
        # daily close, and the market over exactly the same minutes
        readings = reading_series(s)
        if len(readings) >= 2:
            started = readings[0][0]
            returns["ALL"] = readings[-1][1] / readings[0][1] - 1  # the latest reading is the live value
            for ticker, name in cfg.benchmarks:
                ch = intraday.change_since(intraday.gbp_series(s, ticker), started)
                if ch is not None:
                    bench[ticker] = {"name": name, "since_start": ch}
    age = (now - snap.ts).total_seconds()
    if started is None:
        early = reading_series(s)
        started = early[0][0] if early else None
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
        # the market on the same deposits, for "against the S&P" on the return on deposits
        "bench_on_deposits": _bench_on_deposits(s, cfg, dep, started, first_day, tz) if dep else {},
        "since_open": _since_open(s, snap, tz),
        "inception": first_day.isoformat(),
        "started": _iso(started) if started else None,  # early days: when the return is measured from
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
        snaps, at_open = _todays_readings(s, snap, tz)
        if not snaps:
            return {"range": rng, "intraday": True, "points": []}
        t0 = intraday.utc(snaps[0][0])
        return {"range": rng, "intraday": True, "baseline": snaps[0][1] + (snaps[0][2] or 0.0),
                "baseline_label": "Today's open" if at_open else "First reading today",
                "benchmarks": _bench_since(s, cfg, t0),
                "points": [{"t": _iso(t), "value": v + (c or 0.0)} for t, v, c in snaps]}
    if len(_daily(s)) < DAILY_STATS_FROM:
        # early days: every reading since the pie first held stocks, against the market over
        # the same minutes, for every range (they all cover the whole record so far)
        readings = reading_series(s)
        snaps = s.execute(select(PieSnapshot.value, PieSnapshot.cash).order_by(PieSnapshot.ts)).all()
        snaps = [v + (c or 0.0) for v, c in snaps][-len(readings):] if readings else []  # same readings, in £
        if len(readings) >= 2:
            t0 = readings[0][0]
            return {"range": rng, "intraday": True, "early": True, "baseline_label": "Start",
                    "benchmarks": _bench_since(s, cfg, t0),
                    # u: unit price, so money moving in or out doesn't show as a gain
                    "points": [{"t": t.isoformat(), "value": v, "u": u} for (t, u), v in zip(readings, snaps)]}
    rows = _daily(s)
    if not rows:
        return {"range": rng, "points": []}
    total_value = [r.value for r in rows]  # the whole pie, cash included
    new_money = [r.flow if i else 0.0 for i, r in enumerate(rows)]
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
            "t": r.date.isoformat(), "value": total_value[i], "unit_price": r.unit_price,
            "pie": r.unit_price / base_price - 1, "flow": r.flow or None,
            "deposits": cum_dep[i] if cum_dep else None,
            "bench": {k: v[i] for k, v in bench_pct.items()},
            "bench_value": {k: v[i] for k, v in bench_val.items()},
            # kept for older clients
            "benchmark": next(iter(bench_pct.values()))[i] if bench_pct else None,
        } for i, r in enumerate(rows)],
    }


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


def _prev_prices(s: Session, tz: ZoneInfo, day: date) -> dict[str, float]:
    """Each holding's price per share (GBP) at the last close before `day`: from the pie's own
    closes, else from Yahoo's daily closes (see yahoo_closes.py)."""
    out = {tk: next((p for d, p in reversed(ser) if d < day), None) for tk, ser in _per_share_history(s, tz).items()}
    out = {k: v for k, v in out.items() if v}
    for tk, ser in yahoo_closes.per_share(s).items():
        if tk not in out:
            p = next((p for d, p in reversed(ser) if d < day), None)
            if p:
                out[tk] = p
    return out


def _yesterdays_value(s: Session, snap: PieSnapshot, tz: ZoneInfo) -> float | None:
    """Today's holdings at yesterday's closing prices, plus the cash. None if a price is missing."""
    prev = _prev_prices(s, tz, _local_date(snap.ts, tz))
    total = snap.cash or 0.0
    for h in snap.holdings:
        if not h.quantity or not h.value:
            continue
        if h.ticker not in prev:
            return None
        total += h.quantity * prev[h.ticker]
    return total if total > 0 else None


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
    prev_px = _prev_prices(s, tz, snap_day)
    yahoo = yahoo_closes.per_share(s)
    since = _held_since(s, tz)
    total = sum(h.value or 0.0 for h in snap.holdings) or 1.0
    ymap = get_meta(s, "yahoo_map", {}) or {}
    out, sectors = [], {}
    for h in sorted(snap.holdings, key=lambda h: -(h.value or 0.0)):
        px_now = (h.value / h.quantity) if h.quantity and h.value else None
        series = hist.get(h.ticker, [])
        if len(series) < 2:  # too new for a sparkline of its own: Yahoo's closes
            series = yahoo.get(h.ticker, series)
        prev = prev_px.get(h.ticker)
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
        "as_of": _iso(snap.ts), "cash": snap.cash, "holdings_value": total, "holdings": out,
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
    from_yahoo = False
    if len(close_snapshots(s, tz)) <= min_obs and (yc := yahoo_closes.per_share(s)):
        # too few of the pie's own closes yet: the same stocks' Yahoo closes
        hist = {tk: pts[-(days_back + 1):] for tk, pts in yc.items()}
        from_yahoo = True
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
            "average": round(sum(off) / len(off), 3) if off else None, "days": days_back,
            "from_yahoo": from_yahoo}


def rebalances(s: Session, cfg: Settings, limit: int = 100) -> dict:
    rows = _daily(s)
    series = [(r.date, r.unit_price) for r in rows]
    tz = ZoneInfo(cfg.timezone)
    current = _live(s, rows, latest_snapshot(s), tz) if rows else None
    events = [(e.ts, e.source, e.changes, e.id) for e in s.scalars(select(Rebalance).order_by(Rebalance.ts.desc()).limit(limit))]
    names = _names(s)
    judged = get_meta(s, "move_value", {}) or {}
    news = get_meta(s, "move_news", {}) or {}
    days_sorted = sorted(_local_date(ts, tz) for ts, _, _, _ in events)
    outcomes = dict(zip(days_sorted, stats.rebalance_outcomes(series, days_sorted, current))) if current else {}
    return {"rebalances": [{
        "id": rid,
        "ts": _iso(ts),
        "source": src,
        "return_since": outcomes.get(_local_date(ts, tz)),
        "changes": [{**c, "symbol": symbol(c["ticker"]), "name": names.get(c["ticker"])} for c in changes],
        # did it help: the new mix against keeping the old one, from the move to the next (or now)
        "judged": judged.get(str(rid)),
        "news": [{**n, "symbol": symbol(n["ticker"])} for n in (news.get(str(rid)) or {}).get("items", [])],
    } for ts, src, changes, rid in events]}


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


DAILY_STATS_FROM = 5  # daily closes needed before the statistics use them instead of every reading
YEAR_S = 365.25 * 86400


def reading_series(s: Session) -> list[tuple[datetime, float]]:
    """The pie's unit price at every reading (holdings + cash), with money in and out taken
    out, from when it first held stocks. The statistics use it until there are enough daily
    closes. (Readings are thinned to one an hour after a week.)"""
    snaps = s.execute(select(PieSnapshot.ts, PieSnapshot.value, PieSnapshot.cash).order_by(PieSnapshot.ts)).all()
    while snaps and not (snaps[0][1] or 0) > 0:
        snaps = snaps[1:]
    if len(snaps) < 2:
        return []
    moves = sorted([(ts, gbp) for ts, gbp in s.execute(select(BotTrade.filled_at, BotTrade.gbp))]
                   + [(ts, -amt) for ts, amt in s.execute(select(Dividend.paid_on, Dividend.amount))])
    closes, flows_at, j = [], [], 0
    for i, (ts, v, c) in enumerate(snaps):
        closes.append((ts.replace(tzinfo=timezone.utc), v + (c or 0.0)))
        if i:
            f = (c or 0.0) - (snaps[i - 1][2] or 0.0)
            while j < len(moves) and moves[j][0] <= ts:
                if moves[j][0] > snaps[i - 1][0]:
                    f += moves[j][1]
                j += 1
            if f:
                flows_at.append((ts.replace(tzinfo=timezone.utc), f))
    return [(r.date, r.unit_price) for r in compute_daily(closes, flows_at)]


def _per_year(series: list) -> float | None:
    """Readings per year, from the time they span (calendar time, nights included)."""
    span = (series[-1][0] - series[0][0]).total_seconds()
    return (len(series) - 1) / (span / YEAR_S) if span > 0 else None


def _blocks(series: list, tz: ZoneInfo, minutes: int = 15) -> list[dict]:
    """The pie's return in each 15-minute block (London time) that has readings."""
    out, prev_end, cur = [], None, None
    for ts, p in series:
        local = ts.astimezone(tz)
        slot = local.replace(minute=local.minute - local.minute % minutes, second=0, microsecond=0)
        if cur is None or slot != cur["slot"]:
            if cur:
                out.append(cur)
                prev_end = cur["end"]
            cur = {"slot": slot, "start": prev_end if prev_end is not None else p, "end": p}
        cur["end"] = p
    if cur:
        out.append(cur)
    return [{"t": b["slot"].isoformat(), "r": b["end"] / b["start"] - 1} for b in out if b["start"]]


def _todays_readings(s: Session, snap: PieSnapshot, tz: ZoneInfo) -> tuple[list, bool]:
    """Today's readings (ts, holdings value, cash) from today's open: 09:30 in New York, where
    most of the pie trades. Before the open (or with no reading after it): from the first
    reading of the day with stocks in the pie. Second value: whether they start at the open."""
    day = _local_date(snap.ts, tz)
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    snaps = s.execute(select(PieSnapshot.ts, PieSnapshot.value, PieSnapshot.cash)
                      .where(PieSnapshot.ts >= start).order_by(PieSnapshot.ts)).all()
    # readings from before the money was invested would anchor the line at the wrong level
    while snaps and not (snaps[0][1] or 0) > 0:
        snaps = snaps[1:]
    ny_open = datetime.combine(day, datetime.min.time().replace(hour=9, minute=30),
                               tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc).replace(tzinfo=None)
    if any(ts >= ny_open for ts, _, _ in snaps):
        return [x for x in snaps if x[0] >= ny_open], True
    return snaps, False


def _since_open(s: Session, snap: PieSnapshot, tz: ZoneInfo) -> dict | None:
    """The pie's change since today's open, in % and £, with money moved in or out taken out
    (unit prices of the readings)."""
    snaps, at_open = _todays_readings(s, snap, tz)
    if not snaps:
        return None
    t0 = intraday.utc(snaps[0][0])
    readings = reading_series(s)
    u0 = next((u for t, u in readings if t >= t0), None)
    if not u0 or not readings:
        return None
    pct = readings[-1][1] / u0 - 1
    return {"pct": pct, "gbp": pct * (snaps[0][1] + (snaps[0][2] or 0.0)), "at_open": at_open, "from": _iso(snaps[0][0])}


def _bench_on_deposits(s: Session, cfg: Settings, dep: list, started: datetime | None, first_day: date,
                       tz: ZoneInfo) -> dict:
    """What each benchmark would have made on the same deposits, as a return on them: money paid
    in on the pie's first day counts from its first reading (minute prices), later deposits from
    the close on the day they arrived."""
    net = sum(a for _, a in dep)
    if not dep or net <= 0:
        return {}
    out = {}
    daily = benchmark_series(s, cfg)
    for ticker, name in cfg.benchmarks:
        minute = intraday.gbp_series(s, ticker)
        ser = daily.get(ticker, (name, []))[1]
        value = 0.0
        for d, amt in dep:
            g = None
            if started and d <= started.astimezone(tz).date():
                g = intraday.change_since(minute, started)
            if g is None and ser:
                p0 = stats.price_on_or_before(ser, max(d, first_day))
                g = ser[-1][1] / p0 - 1 if p0 else None
            if g is None:
                value = None
                break
            value += amt * (1 + g)
        if value is not None:
            out[ticker] = {"name": name, "return": value / net - 1}
    return out


def _bench_since(s: Session, cfg: Settings, t0: datetime) -> list[dict]:
    """Each benchmark's minute prices from t0 on, as % change since t0."""
    out = []
    for ticker, name in cfg.benchmarks:
        ser = intraday.gbp_series(s, ticker)
        p0 = intraday.at(ser, t0)
        if p0:
            out.append({"key": ticker, "name": name,
                        "points": [{"t": t0.isoformat(), "pct": 0.0}] + [{"t": t.isoformat(), "pct": p / p0 - 1} for t, p in ser if t > t0]})
    return out


def _reading_info(series: list) -> dict:
    return {"from": _iso(series[0][0]), "to": _iso(series[-1][0]), "readings": len(series)}


def stats_payload(s: Session, cfg: Settings) -> dict:
    rows = _daily(s)
    series = [(r.date, r.unit_price) for r in rows]
    readings = reading_series(s) if len(rows) < DAILY_STATS_FROM else []
    if len(series) < 2 and len(readings) < 2:
        return {"ready": False}
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
        "drawdown": [{"t": _iso(d) if readings else d.isoformat(), "dd": v}
                     for d, v in stats.drawdown_series(readings or series)],
        "dividends_total": sum(d.amount for d in divs),
        "dividends": [{"date": d.paid_on.date().isoformat(), "ticker": symbol(d.ticker), "amount": d.amount} for d in divs[:20]],
        "readings": _reading_info(readings) if readings else None,
    }


def analysis_payload(s: Session, cfg: Settings) -> dict:
    rows = _daily(s)
    series = [(r.date, r.unit_price) for r in rows]
    tz = ZoneInfo(cfg.timezone)
    move_days = [_local_date(ts, tz) for (ts,) in s.execute(select(Rebalance.ts))]
    benches = benchmark_series(s, cfg)
    readings = reading_series(s) if len(rows) < DAILY_STATS_FROM else []
    ppy = _per_year(readings) if len(readings) >= 5 else None
    if ppy:
        # too few daily closes: every reading so far, annualised by the time it spans, against
        # the market's minute prices; moves measured from the minute they were spotted
        benches_min = {t: (n, ser) for t, n in cfg.benchmarks if (ser := intraday.gbp_series(s, t))}
        move_times = sorted(intraday.utc(ts) for (ts,) in s.execute(select(Rebalance.ts)))
        a = analysis.analyse(readings, benches_min, move_times, cfg.risk_free_rate, periods_per_year=ppy)
        if a.get("ready"):
            a["readings"] = _reading_info(readings)
            a["blocks"] = _blocks(readings, tz)
            a["verdict"] = []
    else:
        a = analysis.analyse(series, benches, move_days, cfg.risk_free_rate)
    # turnover per move: half the sum of absolute weight changes
    turns = []
    for (ch,) in s.execute(select(Rebalance.changes)):
        turns.append(sum(abs(c.get("delta") or 0) for c in ch) / 2)
    if a.get("ready"):
        a["moves"]["avg_turnover"] = (sum(turns) / len(turns)) if turns else None
        a["risk_free_rate"] = cfg.risk_free_rate
    return a


RECAPS_KEY = "recaps"


def build_recap(s: Session, cfg: Settings, now: datetime | None = None) -> dict | None:
    """The day in a few lines: value, the move since today's open against the S&P over the same
    minutes, best and worst stock, any move by the bot, and the prediction game."""
    from . import game
    tz = ZoneInfo(cfg.timezone)
    now = now or utcnow()
    sm = summary(s, cfg, now)
    snap = latest_snapshot(s)
    if not sm.get("ready") or snap is None:
        return None
    day = _local_date(snap.ts, tz)
    o = sm.get("since_open") or {}
    bench = {}
    if o.get("from"):
        t0 = datetime.fromisoformat(o["from"])
        for ticker, name in cfg.benchmarks:
            ch = intraday.change_since(intraday.gbp_series(s, ticker), t0)
            if ch is not None:
                bench[ticker] = {"name": name, "change": ch}
    hs = [h for h in holdings(s, cfg).get("holdings", []) if h.get("day_pct") is not None and (h.get("value") or 0) > 0]
    hs.sort(key=lambda h: h["day_pct"])
    pick = lambda h: {"symbol": h["symbol"], "name": h["name"], "pct": h["day_pct"], "gbp": h["day_gbp"]}
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    todays = [r for r in rebalances(s, cfg)["rebalances"] if r["ts"] >= _iso(start)]
    g = game.state(s, cfg, now) if cfg.players else None
    out = {
        "date": day.isoformat(), "built": _iso(now), "final": False,
        "value": sm["value"], "profit": sm["profit"], "return_on_deposits": sm["return_on_deposits"],
        "net_deposits": sm["net_deposits"],
        "since_open": o or None, "bench_since_open": bench,
        "best": pick(hs[-1]) if hs else None, "worst": pick(hs[0]) if hs else None,
        "up": sum(h["day_pct"] > 0 for h in hs), "down": sum(h["day_pct"] < 0 for h in hs),
        "moves": [{"ts": r["ts"], "count": len(r["changes"]),
                   "added": [c["symbol"] for c in r["changes"] if c.get("kind") == "added"],
                   "removed": [c["symbol"] for c in r["changes"] if c.get("kind") == "removed"],
                   "judged": r.get("judged")} for r in todays],
        "game": {"open_week": g["open"]["week"], "guessed": len(g["open"]["guessed"]), "players": len(g["players"]),
                 "current": g["current"], "just_scored": g["past"][0] if g["past"] and g["past"][0]["friday"] == day.isoformat() else None} if g else None,
    }
    out["text"] = recap_text(out)
    return out


def recap_text(r: dict) -> str:
    """The recap as a short message (Telegram)."""
    pc = lambda x: f"{x * 100:+.2f}%" if x is not None else "n/a"
    d = date.fromisoformat(r["date"])
    lines = [f"{d.strftime('%a %d %b')}: the pie is worth £{r['value']:,.2f}, {pc(r['return_on_deposits'])} on £{r['net_deposits']:,.0f} paid in."]
    o = r.get("since_open")
    if o:
        b = next(iter(r["bench_since_open"].values()), None)
        lines.append(f"Since the open: {pc(o['pct'])} ({'+' if o['gbp'] >= 0 else '-'}£{abs(o['gbp']):,.2f})" + (f", {b['name']} {pc(b['change'])}." if b else "."))
    if r.get("best") and r.get("worst"):
        lines.append(f"Best: {r['best']['symbol']} {pc(r['best']['pct'])}. Worst: {r['worst']['symbol']} {pc(r['worst']['pct'])}. "
                     f"{r['up']} up, {r['down']} down.")
    for m in r.get("moves", []):
        bits = [f"The bot moved ({m['count']} changes"]
        if m["added"]:
            bits.append(f", bought {', '.join(m['added'])}")
        if m["removed"]:
            bits.append(f", sold {', '.join(m['removed'])}")
        line = "".join(bits) + ")."
        if m.get("judged"):
            line += f" So far it's {m['judged']['value_added'] * 100:+.2f} pts against keeping the old mix."
        lines.append(line)
    g = r.get("game")
    if g:
        js = g.get("just_scored")
        if js:
            win = [x for x in js["guesses"] if x.get("winner")]
            lines.append(f"Guess the week: the pie did {pc(js['return'])}. "
                         + (f"Won by {' and '.join(w['player'] + ' (' + pc(w['guess']) + ')' for w in win)}." if win else "Nobody guessed."))
        elif g.get("current") and g["current"].get("guesses"):
            lead = [x for x in g["current"]["guesses"] if x.get("winner")]
            if lead:
                lines.append(f"Guess the week: {pc(g['current']['return'])} so far, {' and '.join(x['player'] for x in lead)} closest.")
        lines.append(f"Guesses for the week of {date.fromisoformat(g['open_week']).strftime('%d %b')}: {g['guessed']} of {g['players']} in.")
    return "\n".join(lines)


def recap(s: Session, cfg: Settings) -> dict:
    """Today's recap: the one saved at the daily close, else today so far."""
    tz = ZoneInfo(cfg.timezone)
    snap = latest_snapshot(s)
    saved = get_meta(s, RECAPS_KEY, []) or []
    if snap and saved and saved[-1]["date"] == _local_date(snap.ts, tz).isoformat():
        return saved[-1]
    return build_recap(s, cfg) or {"ready": False}


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
