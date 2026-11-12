"""Market data for individual stocks (Yahoo Finance), cached in the database.

Only stocks that have been in the bot pie can be looked up (enforced by the API).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .db import StockCache, get_meta, set_meta

log = logging.getLogger(__name__)

TTL = {"profile": timedelta(hours=6), "news": timedelta(minutes=30), "history": timedelta(hours=6),
       "vol": timedelta(hours=4)}
FAIL_TTL = timedelta(minutes=10)

PROFILE_FIELDS = [
    "longName", "shortName", "quoteType", "sector", "industry", "country", "website", "fullTimeEmployees",
    "longBusinessSummary", "currency", "exchange", "marketCap", "enterpriseValue",
    "currentPrice", "regularMarketPrice", "previousClose", "regularMarketChangePercent",
    "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "fiftyDayAverage", "twoHundredDayAverage",
    "averageVolume", "beta", "trailingPE", "forwardPE", "priceToBook", "pegRatio", "trailingPegRatio",
    "priceToSalesTrailing12Months", "enterpriseToEbitda", "trailingEps", "forwardEps",
    "dividendYield", "payoutRatio", "totalRevenue", "revenueGrowth", "earningsGrowth",
    "grossMargins", "operatingMargins", "profitMargins", "returnOnEquity", "returnOnAssets",
    "debtToEquity", "currentRatio", "freeCashflow", "totalCash", "totalDebt",
    "shortPercentOfFloat", "heldPercentInsiders", "heldPercentInstitutions",
    "recommendationKey", "recommendationMean", "numberOfAnalystOpinions",
    "targetMeanPrice", "targetHighPrice", "targetLowPrice",
    "totalAssets", "category", "fundFamily", "yield", "netExpenseRatio", "ytdReturn",
]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def cached(s: Session, key: str) -> tuple[dict | None, bool]:
    """(data, fresh?)"""
    row = s.get(StockCache, key)
    if not row:
        return None, False
    kind = key.split(":", 1)[0]
    ttl = FAIL_TTL if row.data.get("_error") else TTL.get(kind, timedelta(hours=6))
    return row.data, (_now() - row.fetched_at) < ttl


def _store(s: Session, key: str, data: dict) -> None:
    row = s.get(StockCache, key)
    if row:
        row.data, row.fetched_at = data, _now()
    else:
        s.add(StockCache(key=key, data=data, fetched_at=_now()))


def yahoo_symbol(s: Session, ticker: str) -> str | None:
    """Yahoo symbol for a Trading 212 ticker: from the backfill's verified matches, else resolved now."""
    mapping = get_meta(s, "yahoo_map", {}) or {}
    if ticker in mapping:
        return mapping[ticker]
    try:
        import yfinance as yf
        from .prices import candidates
        isin = (get_meta(s, "isin_map", {}) or {}).get(ticker)
        for cand in candidates(ticker, isin):
            hist = yf.Ticker(cand).history(period="5d")
            if hist is not None and not hist.empty:
                mapping[ticker] = cand
                set_meta(s, "yahoo_map", dict(mapping))
                s.commit()
                return cand
    except Exception as exc:  # network trouble: try again next time
        log.warning("couldn't resolve %s: %s", ticker, exc)
    return None


def _fetch_profile(sym: str) -> dict:
    import yfinance as yf
    info = yf.Ticker(sym).info or {}
    out = {k: info.get(k) for k in PROFILE_FIELDS if info.get(k) is not None}
    if out.get("longBusinessSummary"):
        out["longBusinessSummary"] = out["longBusinessSummary"][:900]
    return out


def _fetch_news(sym: str) -> dict:
    import yfinance as yf
    items = []
    for n in (yf.Ticker(sym).news or [])[:12]:
        c = n.get("content") or n  # newer yfinance nests under "content"
        url = ((c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else None) \
            or ((c.get("clickThroughUrl") or {}).get("url") if isinstance(c.get("clickThroughUrl"), dict) else None) \
            or n.get("link")
        provider = c.get("provider")
        publisher = provider.get("displayName") if isinstance(provider, dict) else n.get("publisher")
        when = c.get("pubDate") or c.get("displayTime")
        if not when and n.get("providerPublishTime"):
            when = datetime.fromtimestamp(n["providerPublishTime"], tz=timezone.utc).isoformat()
        title = c.get("title") or n.get("title")
        if title and url and url.startswith("http"):
            items.append({"title": title, "url": url, "publisher": publisher, "published": when,
                          "summary": (c.get("summary") or "")[:280] or None})
    return {"items": items}


def _fetch_history(sym: str) -> dict:
    import yfinance as yf
    df = yf.Ticker(sym).history(period="2y", interval="1d", auto_adjust=False)
    if df is None or df.empty:
        return {"points": []}
    pts = [{"t": ts.date().isoformat(), "close": float(row["Close"]),
            "volume": float(row["Volume"]) if row.get("Volume") == row.get("Volume") else None}
           for ts, row in df.iterrows() if row["Close"] == row["Close"]]
    return {"points": pts}


MONEYNESS = [round(0.70 + 0.05 * i, 2) for i in range(13)]  # strike / spot, 70% .. 130%


def _fetch_vol(sym: str, max_expiries: int = 9) -> dict:
    """Implied vols from Yahoo's option chains, on a strike/spot x expiry grid.

    Out-of-the-money options only (puts below spot, calls above): they're the liquid side
    and their implied vols are the cleaner ones. Quotes with no open interest or a silly
    IV are dropped, then each expiry is interpolated onto the moneyness grid."""
    import math
    import yfinance as yf
    t = yf.Ticker(sym)
    expiries = list(t.options or [])
    if not expiries:
        return {"available": False}
    spot = None
    try:
        spot = float(t.fast_info.get("lastPrice") or 0) or None
    except Exception:
        pass
    if not spot:
        h = t.history(period="5d")
        spot = float(h["Close"].iloc[-1]) if h is not None and not h.empty else None
    if not spot:
        return {"available": False}
    today = datetime.now(timezone.utc).date()
    dated = [(e, (datetime.fromisoformat(e).date() - today).days) for e in expiries]
    dated = [(e, d) for e, d in dated if 6 <= d <= 420]
    if len(dated) > max_expiries:  # spread the picks across the curve
        idx = sorted({round(i * (len(dated) - 1) / (max_expiries - 1)) for i in range(max_expiries)})
        dated = [dated[i] for i in idx]
    rows, used = [], []
    for e, days in dated:
        try:
            ch = t.option_chain(e)
        except Exception:
            continue
        by_strike: dict[float, list[float]] = {}
        for df, side in ((ch.puts, "put"), (ch.calls, "call")):
            for r in df.itertuples():
                k, iv, oi = float(r.strike), float(r.impliedVolatility or 0), r.openInterest
                if not (0.03 < iv < 3.0) or not (oi == oi and oi and oi > 0):
                    continue
                m = k / spot
                # out of the money only, but near the money use both sides so there's no step at spot
                if (side == "put" and m > 1.1) or (side == "call" and m < 0.9) or not (0.6 <= m <= 1.4):
                    continue
                by_strike.setdefault(m, []).append(iv)
        pts = [(m, sum(v) / len(v)) for m, v in by_strike.items()]
        pts.sort()
        if len(pts) < 4:
            continue
        ms = [p[0] for p in pts]
        row = []
        for g in MONEYNESS:
            if g < ms[0] - 0.05 or g > ms[-1] + 0.05:
                row.append(None)
                continue
            j = next((i for i, m in enumerate(ms) if m >= g), len(ms) - 1)
            if j == 0 or ms[j] == g:
                row.append(round(pts[j][1], 4))
            else:
                (m0, v0), (m1, v1) = pts[j - 1], pts[j]
                w = (g - m0) / (m1 - m0) if m1 > m0 else 0
                row.append(round(v0 + w * (v1 - v0), 4))
        if sum(v is not None for v in row) >= 5:
            rows.append(row)
            used.append({"date": e, "days": days})
    if len(rows) < 2:
        return {"available": False}
    # fill gaps at the wings along each expiry from the nearest quoted point
    for row in rows:
        known = [i for i, v in enumerate(row) if v is not None]
        for i in range(len(row)):
            if row[i] is None:
                row[i] = row[min(known, key=lambda k: abs(k - i))]
    atm = MONEYNESS.index(1.0)
    return {"available": True, "spot": spot, "moneyness": MONEYNESS, "expiries": used, "iv": rows,
            "atm_term": [{"days": u["days"], "iv": r[atm]} for u, r in zip(used, rows)],
            "as_of": _now().isoformat() + "Z", "sqrt_t": [round(math.sqrt(u["days"] / 365), 4) for u in used]}


FETCHERS = {"profile": _fetch_profile, "news": _fetch_news, "history": _fetch_history, "vol": _fetch_vol}


def get(s: Session, kind: str, sym: str, allow_fetch: bool = True) -> dict | None:
    key = f"{kind}:{sym}"
    data, fresh = cached(s, key)
    if fresh or not allow_fetch:
        return None if (data or {}).get("_error") else data
    try:
        new = FETCHERS[kind](sym)
    except Exception as exc:
        log.warning("%s for %s failed: %s", kind, sym, exc)
        if data and not data.get("_error"):
            return data  # stale beats nothing
        new = {"_error": str(exc)[:200]}
    _store(s, key, new)
    s.commit()
    return None if new.get("_error") else new


def refresh_profiles(s: Session, tickers: list[str]) -> int:
    """Used by the poller once a day so sectors etc. are ready without waiting on Yahoo."""
    n = 0
    for t in tickers:
        sym = yahoo_symbol(s, t)
        if sym and get(s, "profile", sym):
            n += 1
    return n


def refresh_news(s: Session, tickers: list[str]) -> int:
    """Used by the poller every 30 minutes so the home page's news is ready instantly."""
    n = 0
    for t in tickers:
        sym = yahoo_symbol(s, t)
        if sym and get(s, "news", sym):
            n += 1
    return n


def cached_news(s: Session, tickers: list[str]) -> dict[str, list[dict]]:
    """News already in the cache for these tickers (never fetches): {ticker: items}."""
    mapping = get_meta(s, "yahoo_map", {}) or {}
    out = {}
    for t in tickers:
        sym = mapping.get(t)
        if not sym:
            continue
        data, _ = cached(s, f"news:{sym}")
        if data and not data.get("_error"):
            out[t] = data.get("items", [])
    return out
