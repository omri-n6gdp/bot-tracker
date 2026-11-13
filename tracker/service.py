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


def gbp_text(x: float) -> str:
    return f"£{x:,.0f}"


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
        readings = reading_series(s, cfg=cfg)
        if len(readings) >= 2:
            started = readings[0][0]
            returns["ALL"] = readings[-1][1] / readings[0][1] - 1  # the latest reading is the live value
            for ticker, name in cfg.benchmarks:
                ch = intraday.change_since(intraday.gbp_series(s, ticker), started)
                if ch is not None:
                    bench[ticker] = {"name": name, "since_start": ch}
    age = (now - snap.ts).total_seconds()
    if started is None:
        early = reading_series(s, cfg=cfg)
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
        "since_open": _since_open(s, snap, tz, cfg),
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
        rows, at_open = _today_rows(s, snap, tz, cfg)
        if not rows:
            return {"range": rng, "intraday": True, "points": []}
        # u: unit price, so money paid in during the day isn't drawn as a gain
        return {"range": rng, "intraday": True, "baseline": rows[0].value,
                "baseline_label": ("Today's open" if rows[0].date.astimezone(tz).date() == _local_date(snap.ts, tz)
                                   else f"{rows[0].date.astimezone(tz).strftime('%a')}'s open") if at_open else "First reading today",
                "benchmarks": _bench_since(s, cfg, rows[0].date),
                # the session these readings start from (the chart's left edge is its open)
                "session": _session_bounds(rows[0].date.astimezone(NY).date()) if at_open else None,
                "points": hold_flat([{"t": r.date.isoformat(), "value": r.value, "u": r.unit_price} for r in rows])}
    if len(_daily(s)) < DAILY_STATS_FROM:
        # early days: every reading since the pie first held stocks, against the market over
        # the same minutes, for every range (they all cover the whole record so far)
        # the charts show every reading, around the clock (statistics use trading hours only)
        readings = reading_series(s, with_value=True, cfg=cfg, all_hours=True)
        if len(readings) >= 2:
            t0, dep = readings[0][0], deposits(s)
            if dep:
                # 0% = break-even: worth what's been paid in; the market on the same money
                at = lambda t: deposits_at(dep, t.astimezone(tz).date())
                bench = [{"key": tk, "name": dict(cfg.benchmarks)[tk],
                          "points": hold_flat([{"t": t.isoformat(), "pct": v / at(t) - 1, **({"f": 1} if k == "fut" else {})} for t, v, k in path if at(t)])}
                         for tk, path in _bench_paths(s, cfg, dep, t0, tz).items()]
                # where the money started: break-even at New York's open on the first day
                op = _session_bounds(t0.astimezone(NY).date())["open"]
                return {"range": rng, "intraday": True, "early": True, "even": True,
                        "origin": op if datetime.fromisoformat(op) < t0 else None,
                        "baseline_label": f"Break-even ({gbp_text(deposits_at(dep, readings[-1][0].astimezone(tz).date()))} paid in)",
                        "benchmarks": bench,
                        "points": hold_flat([{"t": t.isoformat(), "value": v, "u": u, "dep": at(t)} for t, u, v in readings])}
            return {"range": rng, "intraday": True, "early": True, "baseline_label": "Start",
                    "benchmarks": _bench_since(s, cfg, t0),
                    # u: unit price, so money moving in or out doesn't show as a gain
                    "points": hold_flat([{"t": t.isoformat(), "value": v, "u": u} for t, u, v in readings])}
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
    dep_all = deposits(s)
    cum_dep_all = [deposits_at(dep_all, r.date) for r in rows] if dep_all else None
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
        whole = keep[0] == 0 and cum_dep_all is not None
        units, vals = (cum_dep_all[0] if whole else base_value) / al[0], []
        for i, px in enumerate(al):
            if i > 0 and px:
                # over the whole record, money goes in as it was deposited; else as it reached the pie
                units += ((cum_dep_all[keep[i]] - cum_dep_all[keep[i] - 1]) if whole else new_money[i]) / px
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

    first_open = _session_bounds(rows[0].date)["open"] if keep[0] == 0 and cum_dep_all else None
    return {
        "range": rng,
        "intraday": False,
        "origin": first_open,   # the whole record: break-even at the first day's open
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


def _open_prices(s: Session, snap: PieSnapshot, tz: ZoneInfo, cfg: Settings | None = None) -> dict[str, float] | None:
    """Each holding's price per share (GBP) at today's New York open: from the first reading at
    or after it; a stock bought later in the day, from the first reading that holds it. None
    before the open."""
    ny_open = last_ny_open(snap.ts).replace(tzinfo=None)   # the most recent open (yesterday's before today's)
    ids = list(s.scalars(select(PieSnapshot.id).where(PieSnapshot.ts >= ny_open).order_by(PieSnapshot.ts)))
    out: dict[str, float] = {}
    wanted = {h.ticker for h in snap.holdings if h.quantity and h.value}
    for start in range(0, len(ids), 200):
        for sid, tk, q, v in s.execute(select(HoldingSnapshot.snapshot_id, HoldingSnapshot.ticker,
                                              HoldingSnapshot.quantity, HoldingSnapshot.value)
                                       .where(HoldingSnapshot.snapshot_id.in_(ids[start:start + 200]))
                                       .order_by(HoldingSnapshot.snapshot_id)):
            if tk not in out and q and v:
                out[tk] = v / q
        if wanted <= set(out):
            break
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
    # "today" for each stock: from today's open once New York has opened (stocks bought later
    # in the day: from the first reading after they were bought); before the open, from
    # yesterday's close
    open_px = _open_prices(s, snap, tz, cfg) or {}
    # a stock with no reading since the open falls back to yesterday's close
    prev_px = {**_prev_prices(s, tz, snap_day), **open_px}
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
        "day_from": "open" if open_px else "close",
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


NY = ZoneInfo("America/New_York")


def in_session(ts: datetime) -> bool:
    """Is the pie moving: from New York's open (09:30) through its after-hours session (to 20:00,
    01:00 UK) on a weekday? Trading 212 keeps pricing US stocks after the close. Before the open
    the pie's value barely changes, so those readings are left out."""
    et = (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(NY)
    return et.weekday() < 5 and (9, 30) <= (et.hour, et.minute) <= (20, 0)


def record_start(cfg: Settings | None) -> datetime | None:
    """BOT_START_DATE + BOT_START_TIME (local time) as naive UTC: readings before it don't count."""
    if not cfg or not cfg.bot_start_date or not cfg.bot_start_time:
        return None
    hh, mm = map(int, cfg.bot_start_time.split(":"))
    local = datetime.combine(date.fromisoformat(cfg.bot_start_date), datetime.min.time().replace(hour=hh, minute=mm),
                             tzinfo=ZoneInfo(cfg.timezone))
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _all_readings(s: Session, cfg: Settings | None = None) -> tuple[list[DailyRow], list[tuple[datetime, float]]]:
    """Every reading from when the pie first held stocks, as unit-price rows (value, units,
    unit price; times UTC-aware), and the money that came in (+) or went out (-) at each one:
    the bot's buys - sells - dividends + the change in the pie's cash."""
    q = select(PieSnapshot.ts, PieSnapshot.value, PieSnapshot.cash).order_by(PieSnapshot.ts)
    if (start := record_start(cfg)):
        q = q.where(PieSnapshot.ts >= start)
    snaps = s.execute(q).all()
    while snaps and not (snaps[0][1] or 0) > 0:
        snaps = snaps[1:]
    if len(snaps) < 2:
        return [], []
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
            if abs(f) > 0.005:
                flows_at.append((ts.replace(tzinfo=timezone.utc), f))
    return compute_daily(closes, flows_at), flows_at


def reading_series(s: Session, with_value: bool = False, cfg: Settings | None = None,
                   all_hours: bool = False) -> list[tuple]:
    """The pie's unit price at every reading taken while New York is open (holdings + cash),
    with money in and out taken out, from the first open after the pie held stocks. Readings
    outside the session are still used to track money moving, but not shown: the pie barely
    moves then, and hours of flat readings would drown the statistics. The statistics use this
    until there are enough daily closes. (Readings are thinned to one an hour after a week.)
    with_value: (time, unit price, value in GBP) instead of (time, unit price).
    all_hours: every reading, around the clock (the charts); otherwise trading hours only."""
    rows, _ = _all_readings(s, cfg)
    rows = rows if all_hours else [r for r in rows if in_session(r.date)]
    return [(r.date, r.unit_price, r.value) if with_value else (r.date, r.unit_price) for r in rows]


SESSION_S = 390 * 60  # a New York session: 09:30 to 16:00


def _per_year(series: list) -> float | None:
    """Readings per year of trading: 252 sessions, at the usual spacing between readings."""
    gaps = sorted((b[0] - a[0]).total_seconds() for a, b in zip(series, series[1:]))
    gaps = [g for g in gaps if g < 3600] or gaps     # within a session, not overnight
    if not gaps or gaps[len(gaps) // 2] <= 0:
        return None
    return 252 * SESSION_S / gaps[len(gaps) // 2]


def _today_rows(s: Session, snap: PieSnapshot, tz: ZoneInfo, cfg: Settings | None = None) -> tuple[list[DailyRow], bool]:
    """The readings since the most recent New York open (where most of the pie trades), as
    unit-price rows, through after hours and the night until the next open. Before today's open
    that's yesterday's session; at the weekend, Friday's. ("Today" always runs from a market
    open, even on a day the record itself starts later.) Second value: whether they start at an
    open (False only if the pie has no readings since one)."""
    o = last_ny_open(snap.ts)
    rows = [r for r in _all_readings(s)[0] if r.date >= o]
    if rows:
        return rows, True
    day = _local_date(snap.ts, tz)
    return [r for r in _all_readings(s)[0] if r.date.astimezone(tz).date() == day], False


HOLD_GAP_S = 360   # readings further apart than 6 minutes: the line holds flat through the gap


def hold_flat(points: list[dict]) -> list[dict]:
    """Where points are more than HOLD_GAP_S apart (the tracker was off, the market shut, or
    Trading 212 didn't reprice), repeat the last value just before the next point, so the chart
    runs flat through the gap and then moves, instead of slanting or breaking."""
    out = []
    for i, p in enumerate(points):
        if i:
            t0, t1 = datetime.fromisoformat(points[i - 1]["t"]), datetime.fromisoformat(p["t"])
            if (t1 - t0).total_seconds() > HOLD_GAP_S:
                out.append({**points[i - 1], "t": (t1 - timedelta(seconds=1)).isoformat(), "held": 1})
        out.append(p)
    return out


def last_ny_open(ts: datetime) -> datetime:
    """The most recent New York open (09:30 on a weekday) at or before ts, as aware UTC. Before
    today's open, that's the previous weekday's; at the weekend, Friday's."""
    ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    d = ts.astimezone(NY).date()
    while True:
        o = datetime.combine(d, datetime.min.time().replace(hour=9, minute=30), tzinfo=NY).astimezone(timezone.utc)
        if d.weekday() < 5 and o <= ts:
            return o
        d -= timedelta(days=1)


def _session_bounds(day: date) -> dict:
    at = lambda h, m: datetime.combine(day, datetime.min.time().replace(hour=h, minute=m), tzinfo=NY).astimezone(timezone.utc).isoformat()
    return {"open": at(9, 30), "close": at(16, 0)}


def _since_open(s: Session, snap: PieSnapshot, tz: ZoneInfo, cfg: Settings | None = None) -> dict | None:
    """The pie's change since today's open: % from the unit price (money moved in or out taken
    out), and £ as the gain on the money that was in the pie at each step."""
    rows, at_open = _today_rows(s, snap, tz, cfg)
    if len(rows) < 1:
        return None
    pct = rows[-1].unit_price / rows[0].unit_price - 1
    gbp = sum(a.units * (b.unit_price - a.unit_price) for a, b in zip(rows, rows[1:]))
    return {"pct": pct, "gbp": gbp, "at_open": at_open, "from": rows[0].date.isoformat()}


def _bench_paths(s: Session, cfg: Settings, dep: list, started: datetime | None,
                 tz: ZoneInfo) -> dict[str, list[tuple[datetime, float]]]:
    """Early days: what the same money would be worth in each benchmark, minute by minute.
    The first day's deposits go in at the start, less what reached the pie later; every later
    amount goes in at the minute it reached the pie (from the readings: buys - sells + the
    change in cash). {ticker: [(time, value in GBP)]}."""
    if not started or not dep:
        return {}
    start_day = started.astimezone(tz).date()
    first = sum(a for d, a in dep if d <= start_day)
    later = [(t, f) for t, f in _all_readings(s, cfg)[1] if t > started]
    first_day_later = sum(f for t, f in later if t.astimezone(tz).date() == start_day)
    out = {}
    for ticker, _name in cfg.benchmarks:
        src = intraday.gbp_series_src(s, ticker)
        minute = [(t, p) for t, p, _ in src]
        p0 = intraday.at(minute, started) if minute else None
        if not p0 or not all(intraday.at(minute, t) for t, _ in later):
            continue
        units, j, path = max(0.0, first - first_day_later) / p0, 0, [(started, max(0.0, first - first_day_later), "spot")]
        for t, price, k in src:
            if t <= started:
                continue
            while j < len(later) and later[j][0] <= t:
                units += later[j][1] / intraday.at(minute, later[j][0])
                j += 1
            path.append((t, units * price, k))
        out[ticker] = path      # (time, value, "spot" or "fut")
    return out


def deposits_at(dep: list, when: date) -> float:
    return sum(a for d, a in dep if d <= when)


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
    start_day = started.astimezone(tz).date() if started else None
    paths = _bench_paths(s, cfg, dep, started, tz)
    for ticker, name in cfg.benchmarks:
        minute = intraday.gbp_series(s, ticker)
        ser = daily.get(ticker, (name, []))[1]
        if paths.get(ticker):   # early days: the money as it really went in
            out[ticker] = {"name": name, "return": paths[ticker][-1][1] / net - 1}
            continue
        value = 0.0
        for d, amt in dep:
            g = None
            if started and d <= start_day:
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


def _bench_since(s: Session, cfg: Settings, t0: datetime, t1: datetime | None = None) -> list[dict]:
    """Each benchmark's minute prices from t0 on, as % change since t0."""
    out = []
    for ticker, name in cfg.benchmarks:
        src = intraday.gbp_series_src(s, ticker)
        ser = [(t, p) for t, p, _ in src]
        p0 = intraday.at(ser, t0)
        if p0:
            later = [(t, p, k) for t, p, k in src if t > t0 and (t1 is None or t <= t1)]
            pts = [{"t": t0.isoformat(), "pct": 0.0}]
            if later and (later[0][0] - t0) > timedelta(minutes=5):
                # the market was shut: flat until its first price
                pts.append({"t": (later[0][0] - timedelta(seconds=1)).isoformat(), "pct": 0.0})
            # "f": from index futures, while the fund that tracks the index isn't trading
            out.append({"key": ticker, "name": name, "points": hold_flat(pts + [{"t": t.isoformat(), "pct": p / p0 - 1, **({"f": 1} if k == "fut" else {})} for t, p, k in later])})
    return out


def _reading_info(series: list) -> dict:
    return {"from": _iso(series[0][0]), "to": _iso(series[-1][0]), "readings": len(series)}


def stats_payload(s: Session, cfg: Settings) -> dict:
    rows = _daily(s)
    series = [(r.date, r.unit_price) for r in rows]
    readings = reading_series(s, cfg=cfg) if len(rows) < DAILY_STATS_FROM else []
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
    readings = reading_series(s, cfg=cfg) if len(rows) < DAILY_STATS_FROM else []
    ppy = _per_year(readings) if len(readings) >= 5 else None
    if ppy:
        # too few daily closes: every reading so far, annualised by the time it spans, against
        # the market's minute prices; moves measured from the minute they were spotted
        benches_min = {t: (n, ser) for t, n in cfg.benchmarks if (ser := intraday.gbp_series(s, t))}
        move_times = sorted(intraday.utc(ts) for (ts,) in s.execute(select(Rebalance.ts)))
        a = analysis.analyse(readings, benches_min, move_times, cfg.risk_free_rate, periods_per_year=ppy)
        if a.get("ready"):
            a["readings"] = _reading_info(readings)
            a["verdict"] = []
    else:
        a = analysis.analyse(series, benches, move_days, cfg.risk_free_rate)
    # the raw unit prices, so the page can show returns over any timeframe: every reading while
    # New York was open over the last week, and every daily close
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    a["series_min"] = [[t.isoformat(), round(u, 7)] for t, u in (readings or reading_series(s, cfg=cfg)) if t >= week_ago]
    a["series_day"] = [[r.date.isoformat(), round(r.unit_price, 7)] for r in rows]
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


RR_WINDOWS = (21, 63, 252)   # trading days: about a month, three months, a year


def _rr_point(prices: list[float], n: int) -> dict | None:
    """Return over the last n daily closes and the swing over the same stretch: the spread of
    daily returns scaled to n days (sd x sqrt(n)), so the two are directly comparable."""
    if len(prices) < n + 1:
        return None
    w = prices[-(n + 1):]
    r = [b / a - 1 for a, b in zip(w, w[1:]) if a]
    if len(r) < 5:
        return None
    m = sum(r) / len(r)
    sd = (sum((x - m) ** 2 for x in r) / (len(r) - 1)) ** 0.5
    return {"ret": w[-1] / w[0] - 1, "swing": sd * n ** 0.5, "vol": sd * 252 ** 0.5}


def risk_return(s: Session, cfg: Settings) -> dict:
    """Each holding, the pie's current mix and the benchmarks: return against swing over about
    a month, three months and a year, from Yahoo daily closes in GBP."""
    from . import montecarlo
    hs = [h for h in holdings(s, cfg).get("holdings", []) if (h.get("value") or 0) > 0]
    per = yahoo_closes.per_share(s)
    out = {"windows": list(RR_WINDOWS), "stocks": [], "pie": {}, "benchmarks": []}
    for h in hs:
        prices = [p for _, p in per.get(h["ticker"], [])]
        pts = {str(n): _rr_point(prices, n) for n in RR_WINDOWS}
        if any(pts.values()):
            out["stocks"].append({"ticker": h["ticker"], "symbol": h["symbol"], "name": h["name"],
                                  "sector": h.get("sector") or "Other", "weight": h["weight"], **pts})
    weights = {h["ticker"]: h["weight"] for h in hs}
    daily = montecarlo.portfolio_returns(per, weights)
    if daily:
        growth = [1.0]
        for r in daily:
            growth.append(growth[-1] * (1 + r))
        out["pie"] = {str(n): _rr_point(growth, n) for n in RR_WINDOWS}
    for ticker, name in cfg.benchmarks:
        h = market.get(s, "history", ticker, allow_fetch=False) or {}
        prices = [p["close"] for p in h.get("points", []) if p.get("close")]
        if prices:
            out["benchmarks"].append({"ticker": ticker, "name": name, **{str(n): _rr_point(prices, n) for n in RR_WINDOWS}})
    return out


def quant_pack(s: Session, cfg: Settings) -> dict:
    """The deeper analysis of today's mix (built by the poller), plus this pie's own rolling beta
    against the S&P from its minute readings."""
    from . import quant
    import numpy as np
    pack = dict(get_meta(s, quant.META_KEY) or {"ready": False})
    readings = reading_series(s, cfg=cfg)
    ser = intraday.gbp_series(s, cfg.benchmark_ticker)
    pts = [(t, u, intraday.at(ser, t)) for t, u in readings] if ser else []
    pts = [x for x in pts if x[2]]
    pairs = [((u1 / u0 - 1), (b1 / b0 - 1), t1) for (t0, u0, b0), (t1, u1, b1) in zip(pts, pts[1:])
             if (t1 - t0).total_seconds() < 600]     # within a session, not across the night
    W = 30
    roll = []
    if len(pairs) > W + 5:
        y = np.array([p[0] for p in pairs]); x = np.array([p[1] for p in pairs])
        per_year = _per_year(readings) or 252 * 390
        for i in range(W, len(pairs) + 1):
            f = quant.ols(y[i - W:i], x[i - W:i, None])
            b, se = float(f["coef"][1]), float(f["se"][1])
            roll.append({"t": pairs[i - 1][2].isoformat(), "beta": b, "lo": b - 1.96 * se, "hi": b + 1.96 * se,
                         "alpha": float(f["coef"][0]) * per_year, "r2": f["r2"]})
    pack["pie_rolling"] = roll
    pack["pie_window"] = W
    return pack


def monte_carlo(s: Session, cfg: Settings) -> dict:
    """The latest range of outcomes, and the first one ever made with the pie's real path since
    (unit price, so deposits don't count), in trading days from when it was made."""
    from . import montecarlo
    data = get_meta(s, montecarlo.META_KEY)
    if not data:
        return {"ready": False}
    anchor = get_meta(s, montecarlo.ANCHOR_KEY)
    if anchor and anchor.get("unit_price"):
        tz = ZoneInfo(cfg.timezone)
        t0 = datetime.fromisoformat(anchor["built"])
        d0 = t0.astimezone(tz).date()
        weekdays = lambda d: sum(1 for i in range(1, (d - d0).days + 1) if (d0 + timedelta(days=i)).weekday() < 5)
        real = [[0, 0.0]]
        for r in _daily(s):
            if r.date > d0:
                real.append([weekdays(r.date), r.unit_price / anchor["unit_price"] - 1])
        sm = summary(s, cfg)
        if sm.get("ready") and sm.get("unit_price"):
            now = datetime.now(timezone.utc)
            today = now.astimezone(tz).date()
            # part of today's session gone (09:30-16:00 New York)
            et = now.astimezone(NY)
            frac = min(1.0, max(0.0, ((et.hour - 9.5) * 60 + et.minute) / 390)) if today.weekday() < 5 else 0.0
            since = (now - t0).total_seconds() / (6.5 * 3600) if today == d0 else None
            x = min(since, 1.0) if since is not None else max(0, weekdays(today) - 1) + frac
            real.append([round(x, 3), sm["unit_price"] / anchor["unit_price"] - 1])
        anchor = {k: v for k, v in anchor.items() if k != "unit_price"} | {"real": sorted(real)}
    return {**data, "anchor": anchor}


def stock_intraday(s: Session, cfg: Settings, ticker: str) -> dict | None:
    """Minute prices (the stock's own currency) for its latest New York session, from the open,
    with the open and the close before it. None if it was never in the pie."""
    if ticker not in pie_tickers(s):
        return None
    sym = market.yahoo_symbol(s, ticker)
    data = market.get(s, "intraday", sym) if sym else None
    pts = [(datetime.fromisoformat(t), c) for t, c in (data or {}).get("points", [])]
    if not pts:
        return {"ticker": ticker, "points": []}
    day = pts[-1][0].astimezone(NY).date()
    ny_open = datetime.combine(day, datetime.min.time().replace(hour=9, minute=30), tzinfo=NY)
    today = [(t, c) for t, c in pts if t >= ny_open]          # the session and after hours
    # the close before: the last regular-session price before today (not pre-market or after hours)
    before = [c for t, c in pts if t < ny_open and (9, 30) <= (t.astimezone(NY).hour, t.astimezone(NY).minute) <= (16, 0)]
    return {"ticker": ticker, "symbol": sym, "session": day.isoformat(),
            "open": today[0][1] if today else None, "prev_close": before[-1] if before else None,
            "points": hold_flat([{"t": t.isoformat(), "price": c} for t, c in today])}


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
    from . import market, quant
    if ticker == "PIE":   # the whole pie, from its US holdings' surfaces
        hs = [h for h in holdings(s).get("holdings", []) if (h.get("value") or 0) > 0]
        return {"ticker": "PIE", "symbol": "The whole pie", **quant.pie_vol_surface(s, hs)}
    if ticker not in pie_tickers(s):
        return None
    sym = market.yahoo_symbol(s, ticker)
    data = market.get(s, "vol", sym) if sym else None
    return {"ticker": ticker, "symbol": symbol(ticker), "yahoo": sym, **(data or {"available": False})}
