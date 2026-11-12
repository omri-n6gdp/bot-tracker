"""Copies the bot pie's state from Trading 212 into the database.

Schedule (all well inside the published rate limits, which the bot shares):
  pie detail      every 60 s   value, holdings, target weights -> rebalances
  pie list        every 5 min  uninvested cash in the pie
  positions       every 5 min  instrument names; GBP prices for the fallback path
  dividends       every hour
  benchmark       every 6 h
  daily close     21:30 London (after the US close)

Fallback: if the deprecated pie endpoints stop working, value = last known pie
quantities x live prices from /positions. Switches automatically in TRACKER_MODE=auto.
"""
from __future__ import annotations

import csv
import logging
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from . import service
from .config import Settings
from .attribution import MAX_REBALANCE_BATCH, trades_from_orders
from .db import (
    BenchmarkPrice, BotTrade, Contribution, Dividend, HoldingSnapshot, InstrumentName, PieSnapshot,
    Rebalance, TargetWeights, get_meta, set_meta, utcnow,
)
from .alerts import FailureTracker, send as send_alert
from .rebalance import diff_targets
from .t212 import T212Client, T212Error

log = logging.getLogger(__name__)

PIE_FAILS_BEFORE_FALLBACK = 3


class Poller:
    def __init__(self, client: T212Client, Session: sessionmaker[Session], settings: Settings, clock=utcnow):
        if settings.pie_id is None:
            raise ValueError("Set T212_PIE_ID (run `python -m tracker find-pie` to see your pies)")
        self.c, self.Session, self.cfg, self.now = client, Session, settings, clock
        self.pie_fail_streak = 0
        self.tz = ZoneInfo(settings.timezone)
        self.failures = FailureTracker()

    # ---- health ---------------------------------------------------------
    def _ok(self, s: Session, source: str) -> None:
        h = get_meta(s, "health", {}) or {}
        h[source] = {**h.get(source, {}), "last_ok": self.now().isoformat(), "error": None}
        set_meta(s, "health", h)

    def _err(self, source: str, exc: Exception) -> None:
        log.warning("%s failed: %s", source, exc)
        with self.Session() as s:
            h = get_meta(s, "health", {}) or {}
            h[source] = {**h.get(source, {}), "error": str(exc), "error_at": self.now().isoformat()}
            set_meta(s, "health", h)
            s.commit()

    @property
    def mode(self) -> str:
        if self.cfg.mode in ("pie", "fallback"):
            return self.cfg.mode
        return "fallback" if self.pie_fail_streak >= PIE_FAILS_BEFORE_FALLBACK else "pie"

    # ---- jobs -----------------------------------------------------------
    def poll_pie_list(self) -> None:
        pies = self.c.pies()
        mine = next((p for p in pies if p.id == self.cfg.pie_id), None)
        if mine is None:
            raise T212Error(404, "/pies", f"pie {self.cfg.pie_id} not found in account")
        with self.Session() as s:
            set_meta(s, "pie_list", {
                "cash": mine.cash or 0.0,
                "invested": mine.result.priceAvgInvestedValue,
                "value": mine.result.priceAvgValue,
                "status": mine.status,
                "dividends": mine.dividendDetails.model_dump() if mine.dividendDetails else None,
                "ts": self.now().isoformat(),
            })
            self._ok(s, "pie_list")
            s.commit()

    def poll_pie(self, daily_close: bool = False) -> PieSnapshot:
        detail = self.c.pie(self.cfg.pie_id)
        now = self.now()
        with self.Session() as s:
            cash = float((get_meta(s, "pie_list") or {}).get("cash") or 0.0)
            holdings_value = sum((i.result.priceAvgValue or 0.0) for i in detail.instruments)
            snap = PieSnapshot(
                ts=now,
                value=holdings_value,  # performance is measured on holdings; cash is shown separately
                holdings_value=holdings_value,
                cash=cash,
                invested=sum((i.result.priceAvgInvestedValue or 0.0) for i in detail.instruments),
                result=sum((i.result.priceAvgResult or 0.0) for i in detail.instruments),
                is_daily_close=daily_close,
                source="pie",
            )
            for i in detail.instruments:
                snap.holdings.append(HoldingSnapshot(
                    ticker=i.ticker, quantity=i.ownedQuantity, value=i.result.priceAvgValue,
                    invested=i.result.priceAvgInvestedValue, result=i.result.priceAvgResult,
                    current_share=i.currentShare, expected_share=i.expectedShare,
                ))
            s.add(snap)
            self._record_targets(s, now, {i.ticker: i.expectedShare or 0.0 for i in detail.instruments})
            if detail.settings.name:
                set_meta(s, "pie_name", detail.settings.name)
            self._ok(s, "pie")
            s.commit()
            self.pie_fail_streak = 0
            return snap

    def _record_targets(self, s: Session, now: datetime, targets: dict[str, float]) -> None:
        if not targets:
            return
        latest = s.scalars(select(TargetWeights).order_by(TargetWeights.ts.desc()).limit(1)).first()
        before = latest.weights if latest else None
        changes = diff_targets(before, targets)
        if changes:
            s.add(Rebalance(ts=now, weights_before=before, weights_after=targets, changes=changes))
            log.info("rebalance detected: %d changes", len(changes))
        if latest is None or changes:
            s.add(TargetWeights(ts=now, weights=targets))

    def poll_positions(self) -> dict[str, float]:
        """Names for pie tickers, and GBP prices (for the fallback). Non-pie holdings are ignored."""
        positions = self.c.positions()
        with self.Session() as s:
            pie_tickers = service.pie_tickers(s)
            prices = {}
            isins = dict(get_meta(s, "isin_map", {}) or {})
            for p in positions:
                if p.ticker not in pie_tickers:
                    continue  # never store anything about the rest of the ISA
                if p.instrument.name:
                    s.merge(InstrumentName(ticker=p.ticker, name=p.instrument.name, currency=p.instrument.currency))
                if p.instrument.isin:
                    isins[p.ticker] = p.instrument.isin
                if p.price_in_account_ccy is not None:
                    prices[p.ticker] = p.price_in_account_ccy
            set_meta(s, "prices", {"ts": self.now().isoformat(), "gbp": prices})
            set_meta(s, "isin_map", isins)
            self._ok(s, "positions")
            s.commit()
            return prices

    def poll_fallback(self, daily_close: bool = False) -> PieSnapshot | None:
        prices = self.poll_positions()
        now = self.now()
        with self.Session() as s:
            last = s.scalars(
                select(PieSnapshot).where(PieSnapshot.source == "pie").order_by(PieSnapshot.ts.desc()).limit(1)
            ).first()
            if last is None:
                log.error("fallback needs at least one pie snapshot for quantities")
                return None
            cash = float((get_meta(s, "pie_list") or {}).get("cash") or last.cash or 0.0)
            snap = PieSnapshot(ts=now, holdings_value=0.0, cash=cash, value=0.0,
                               is_daily_close=daily_close, source="fallback")
            total = 0.0
            for h in last.holdings:
                price = prices.get(h.ticker)
                if price is None and h.quantity:
                    price = (h.value or 0.0) / h.quantity  # stale but better than zero
                value = (h.quantity or 0.0) * (price or 0.0)
                total += value
                snap.holdings.append(HoldingSnapshot(
                    ticker=h.ticker, quantity=h.quantity, value=value, invested=h.invested,
                    result=(value - h.invested) if h.invested is not None else None,
                    expected_share=h.expected_share,
                ))
            snap.holdings_value, snap.value = total, total
            for h in snap.holdings:
                h.current_share = (h.value or 0.0) / total if total else None
            s.add(snap)
            self._ok(s, "fallback")
            s.commit()
            return snap

    def snapshot(self, daily_close: bool = False) -> None:
        if self.mode == "pie":
            try:
                self.poll_pie(daily_close)
                return
            except T212Error as exc:
                self.pie_fail_streak += 1
                self._err("pie", exc)
                if self.mode == "pie":
                    return
                log.warning("pie endpoints failing; switching to fallback")
                send_alert("Trading 212's pie endpoints are failing, so the tracker switched to backup pricing.")
        self.poll_fallback(daily_close)

    def poll_dividends(self) -> int:
        """Dividends on the pie's stocks, from when tracking began.

        Another pie (or a plain holding) can own the same stock, so each payment is scaled
        by this pie's share of the account's holding in it. Account-wide quantities are
        used for that ratio only and never stored.
        """
        added = 0
        with self.Session() as s:
            tickers = service.pie_tickers(s)
            known = set(s.scalars(select(Dividend.reference)))
            first = s.scalar(select(func.min(PieSnapshot.ts)).where(PieSnapshot.source == "pie"))
            snap = service.latest_snapshot(s)
            pie_qty = {h.ticker: (h.quantity or 0.0) for h in snap.holdings} if snap else {}
        share: dict[str, float] = {}
        for dv in self.c.dividends():
            if not dv.reference or dv.reference in known:
                if dv.reference in known:
                    break  # newest-first: everything after this is already stored
                continue
            paid = _parse_ts(dv.paidOn) or self.now()
            if first is not None and paid < first:
                break  # older than the tracker: not this pie's
            if dv.ticker not in tickers or dv.amount is None:
                continue
            if not share:
                try:
                    acct = {p.ticker: (p.quantity or 0.0) for p in self.c.positions()}
                except T212Error:
                    acct = {}
                share = {t: (min(1.0, pie_qty.get(t, 0.0) / acct[t]) if acct.get(t) else 1.0) for t in tickers}
                share["_"] = 1.0
            amount = dv.amount * share.get(dv.ticker, 1.0)
            if amount <= 0:
                continue
            with self.Session() as s:
                s.add(Dividend(reference=dv.reference, ticker=dv.ticker, amount=round(amount, 4), paid_on=paid))
                s.commit()
            added += 1
        with self.Session() as s:
            self._ok(s, "dividends")
            s.commit()
        return added

    def poll_trades(self, max_pages: int = 5) -> int:
        """New filled orders for the bot pie -> bot_trades (these are the money in/out).

        Order history doesn't say which pie an order came from, and another pie can hold the
        same stocks. So a batch of fills (one createdAt) counts as this pie's only when the
        pie's own share counts moved by those amounts between the snapshot before the batch
        and the one after it. Account-wide sell-offs and manual orders never count.
        Fills from before the tracker's first snapshot are ignored.
        """
        with self.Session() as s:
            known = set(s.scalars(select(BotTrade.fill_id)))
            seen_before = get_meta(s, "seen_fill_ids_floor") or 0
        fresh = []
        for o in self.c.orders(max_pages=max_pages):
            fid = o.fill.id if o.fill else None
            if fid is not None and (fid in known or fid <= seen_before):
                break
            fresh.append(o)
        trades = trades_from_orders(fresh)
        if not trades:
            with self.Session() as s:
                self._ok(s, "trades")
                s.commit()
            return 0
        batches: dict[str, list] = {}
        for t in trades:
            batches.setdefault(t.batch, []).append(t)
        fill_times = [x for x in (_parse_ts(t.filled_at) for t in trades) if x]
        with self.Session() as s:
            first = s.scalar(select(func.min(PieSnapshot.ts)).where(PieSnapshot.source == "pie"))
            since = min(fill_times) - timedelta(hours=6) if fill_times else None
            q = select(PieSnapshot).where(PieSnapshot.source == "pie").order_by(PieSnapshot.ts)
            if since is not None:
                prev = s.scalar(select(func.max(PieSnapshot.ts)).where(PieSnapshot.source == "pie", PieSnapshot.ts <= since))
                q = q.where(PieSnapshot.ts >= (prev or since))
            qty_at = [(sn.ts, {h.ticker: (h.quantity or 0.0) for h in sn.holdings}) for sn in s.scalars(q)]

        accepted, deferred = [], []
        for key, b in batches.items():
            if len(b) > MAX_REBALANCE_BATCH or any(t.initiated_from not in ("AUTOINVEST", None) for t in b):
                continue
            times = [_parse_ts(t.filled_at) for t in b]
            if None in times or not qty_at:
                continue
            t0, t1 = min(times), max(times)
            if first is None or t0 < first or t0 < qty_at[0][0]:
                continue  # before tracking began (fresh start): not ours to count
            after = next((q for ts, q in qty_at if ts >= t1), None)
            if after is None:
                deferred.append(b)  # no snapshot since the fills yet; look again next time
                continue
            before = next(q for ts, q in reversed(qty_at) if ts <= t0)
            if batch_matches_pie(b, before, after):
                accepted.extend(b)

        added = 0
        with self.Session() as s:
            for t in accepted:
                s.merge(BotTrade(fill_id=t.fill_id, order_id=t.order_id, batch=t.batch,
                                 filled_at=_parse_ts(t.filled_at), ticker=t.ticker, side=t.side,
                                 quantity=t.quantity, gbp=t.gbp))
                added += 1
            floor = max(t.fill_id for t in trades)
            if deferred:
                floor = min(floor, min(t.fill_id for b in deferred for t in b) - 1)
            set_meta(s, "seen_fill_ids_floor", max(seen_before, floor))
            self._ok(s, "trades")
            s.commit()
        return added

    def update_benchmark(self) -> int:
        n = update_benchmark_prices(self.Session, self.cfg)
        with self.Session() as s:
            self._ok(s, "benchmark")
            s.commit()
        return n

    def refresh_profiles(self) -> int:
        """Daily: company profiles (sector etc.) for current holdings, so pages load fast."""
        from . import market
        with self.Session() as s:
            snap = service.latest_snapshot(s)
            tickers = [h.ticker for h in snap.holdings] if snap else []
            n = market.refresh_profiles(s, tickers)
            self._ok(s, "profiles")
            s.commit()
        return n

    def refresh_news(self) -> int:
        """Every 30 minutes: headlines for the stocks held now, for the home page's news."""
        from . import market
        with self.Session() as s:
            snap = service.latest_snapshot(s)
            held = [h.ticker for h in snap.holdings if (h.value or 0) > 0] if snap else []
            n = market.refresh_news(s, held or ([h.ticker for h in snap.holdings] if snap else []))
            self._ok(s, "news")
            s.commit()
        return n

    def sync_contributions(self) -> int:
        """Money you paid into the bot from outside (deposits.csv). Display only: net deposits,
        profit, the deposits line. Performance comes from trades and doesn't use this."""
        path = self.cfg.deposits_csv if self.cfg.deposits_csv.exists() else self.cfg.contributions_csv
        rows = read_contributions_csv(path)
        with self.Session() as s:
            s.execute(delete(Contribution))
            for d, amt, note in rows:
                s.add(Contribution(date=d, amount=amt, note=note))
            s.commit()
        return len(rows)

    def recompute(self) -> None:
        with self.Session() as s:
            service.rebuild(s, self.cfg)
            s.commit()

    def prune(self, keep_days: int = 7) -> int:
        """Keep every snapshot for a week; after that, daily closes plus one per hour."""
        cutoff = self.now() - timedelta(days=keep_days)
        with self.Session() as s:
            old = s.scalars(select(PieSnapshot).where(PieSnapshot.ts < cutoff, PieSnapshot.is_daily_close.is_(False))
                            .order_by(PieSnapshot.ts)).all()
            seen_hours, n = set(), 0
            for snap in old:
                hour = snap.ts.replace(minute=0, second=0, microsecond=0)
                if hour in seen_hours:
                    s.delete(snap)
                    n += 1
                else:
                    seen_hours.add(hour)
            s.commit()
            return n

    # ---- loop -----------------------------------------------------------
    def _is_close_due(self, last_close_day: date | None) -> tuple[bool, date]:
        local = self.now().replace(tzinfo=timezone.utc).astimezone(self.tz)
        hh, mm = map(int, self.cfg.daily_close_time.split(":"))
        due = local.weekday() < 5 and (local.hour, local.minute) >= (hh, mm) and last_close_day != local.date()
        return due, local.date()

    def run_forever(self) -> None:  # pragma: no cover - long-running loop
        jobs = {  # name: (interval seconds, function)
            "pie_list": (300, self.poll_pie_list),
            "snapshot": (60, self.snapshot),
            "positions": (300, self.poll_positions),
            "dividends": (3600, self.poll_dividends),
            "trades": (900, self.poll_trades),
            "profiles": (6 * 3600, self.refresh_profiles),
            "news": (1800, self.refresh_news),
            "benchmark": (6 * 3600, self.update_benchmark),
            "contributions": (300, self.sync_contributions),
        }
        next_run = {k: 0.0 for k in jobs}
        last_close_day = None
        log.info("poller started for pie %s in %s mode", self.cfg.pie_id, self.mode)
        while True:
            for name, (every, fn) in jobs.items():
                if time.monotonic() >= next_run[name]:
                    try:
                        fn()
                        if name in ("snapshot", "contributions"):
                            self.recompute()
                        self.failures.ok(name)
                    except Exception as exc:  # keep going; health records the error
                        self._err(name, exc)
                        self.failures.failed(name, str(exc))
                    next_run[name] = time.monotonic() + every
            due, today = self._is_close_due(last_close_day)
            if due:
                try:
                    self.snapshot(daily_close=True)
                    self.recompute()
                    self.prune()
                    last_close_day = today
                    self.failures.ok("daily_close")
                except Exception as exc:
                    self._err("daily_close", exc)
                    self.failures.failed("daily_close", str(exc))
            time.sleep(5)


def update_benchmark_prices(Session, cfg: Settings) -> int:
    """Daily closes of every benchmark from a week before the first snapshot until today."""
    try:
        import yfinance as yf
    except ImportError:
        return 0
    with Session() as s:
        first = s.scalar(select(func.min(PieSnapshot.ts)))
    if first is None:
        return 0
    start = (first - timedelta(days=7)).date()
    n = 0
    for ticker, _name in cfg.benchmarks:
        df = yf.download(ticker, start=start.isoformat(), progress=False, auto_adjust=True)
        if df is None or df.empty:
            log.warning("no data for benchmark %s", ticker)
            continue
        closes = df["Close"]
        if hasattr(closes, "columns"):  # newer yfinance returns a frame per ticker
            closes = closes.iloc[:, 0]
        with Session() as s:
            have = set(s.scalars(select(BenchmarkPrice.date).where(BenchmarkPrice.ticker == ticker)))
            for ts, close in closes.items():
                d = ts.date()
                if d not in have and close == close:  # skip NaN
                    s.add(BenchmarkPrice(ticker=ticker, date=d, close=float(close)))
                    n += 1
            s.commit()
    return n


def read_contributions_csv(path) -> list[tuple[date, float, str]]:
    """CSV with header date,amount,note. amount positive = money in, negative = money out."""
    try:
        f = open(path, newline="", encoding="utf-8")
    except FileNotFoundError:
        return []
    with f:
        out = []
        for row in csv.DictReader(f):
            if not row.get("date") or row["date"].lstrip().startswith("#") or not row.get("amount"):
                continue
            out.append((date.fromisoformat(row["date"].strip()),
                        float(row["amount"].replace("£", "").replace(",", "").strip()),
                        (row.get("note") or "").strip()))
        return out


def batch_matches_pie(batch, before: dict[str, float], after: dict[str, float], tol: float = 0.1) -> bool:
    """True when this pie's share counts changed by about the batch's quantities.

    `before`/`after`: {ticker: quantity} from the pie snapshots either side of the batch.
    Most of the batch's fills must match the pie's own change for that stock to within `tol`.
    """
    hits = 0
    for t in batch:
        dq = after.get(t.ticker, 0.0) - before.get(t.ticker, 0.0)
        if abs(dq) > 1e-9 and t.quantity * dq > 0 and abs(t.quantity / dq - 1) <= tol:
            hits += 1
    return hits >= max(1, round(0.7 * len(batch)))


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        secs = value / 1000 if value > 1e11 else value  # ms or s epoch
        return datetime.fromtimestamp(secs, tz=timezone.utc).replace(tzinfo=None)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        return None
