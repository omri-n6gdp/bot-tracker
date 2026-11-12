"""Daily closing prices in GBP for Trading 212 instruments, from Yahoo Finance.

Trading 212 tickers often keep old names (e.g. Rocket Lab is VACQ_US_EQ), so each one is
matched to a Yahoo symbol using the ticker, then the ISIN, and the match is checked
against the actual prices of your fills before it's trusted.
"""
from __future__ import annotations

import logging
import re
import statistics
from datetime import date, timedelta

log = logging.getLogger(__name__)

# Trading 212 suffix -> Yahoo exchange suffix
SUFFIX = {"l_EQ": ".L", "d_EQ": ".DE", "p_EQ": ".PA", "a_EQ": ".AS", "m_EQ": ".MI", "s_EQ": ".SW", "e_EQ": ".MC"}
FX_PAIRS = {"USD": "GBPUSD=X", "EUR": "GBPEUR=X", "CAD": "GBPCAD=X", "CHF": "GBPCHF=X"}
MAX_MEDIAN_ERROR = 0.06   # fills vs Yahoo close on the same day
MAX_ERROR_SAME_SYMBOL = 0.12  # intraday fills on volatile days; symbol equals the T212 ticker


def candidates(ticker: str, isin: str | None = None) -> list[str]:
    out: list[str] = []
    if ticker.endswith("_US_EQ"):
        base = ticker[: -len("_US_EQ")].rstrip("_")
        out += [base, re.sub(r"\d+$", "", base), base.replace("_", "-")]
    elif ticker.endswith("_CA_EQ"):
        base = ticker[: -len("_CA_EQ")]
        out += [f"{base}.TO", f"{base}.V", base]
    else:
        for suf, ysuf in SUFFIX.items():
            if ticker.endswith(suf):
                out.append(ticker[: -len(suf)] + ysuf)
        if ticker.endswith("_EQ"):
            out.append(ticker.split("_")[0] + ".L")
    if isin:
        try:
            import yfinance as yf
            res = yf.Search(isin, max_results=8)
            out += [q["symbol"] for q in getattr(res, "quotes", []) if q.get("symbol")]
        except Exception as exc:  # pragma: no cover - network
            log.debug("ISIN search failed for %s: %s", isin, exc)
    seen, uniq = set(), []
    for s in out:
        if s and s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def _download(symbols: list[str], start: date, end: date) -> dict[str, dict[date, float]]:
    import yfinance as yf
    if not symbols:
        return {}
    df = yf.download(symbols, start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                     progress=False, auto_adjust=False, group_by="ticker", threads=True)
    out: dict[str, dict[date, float]] = {}
    for s in symbols:
        try:
            if getattr(df.columns, "nlevels", 1) > 1:   # (ticker, field) columns
                lvl0 = set(df.columns.get_level_values(0))
                col = df[s]["Close"] if s in lvl0 else df["Close"][s]
            else:
                col = df["Close"]
            if hasattr(col, "columns"):
                col = col.iloc[:, 0]
        except (KeyError, TypeError):
            continue
        series = {ts.date(): float(v) for ts, v in col.items() if v == v}
        if series:
            out[s] = series
    return out


def _nearest(series: dict[date, float], d: date) -> float | None:
    for back in range(0, 5):
        v = series.get(d - timedelta(days=back))
        if v is not None:
            return v
    return None


def best_match(series_by_symbol: dict[str, dict[date, float]], fills: list[tuple[date, float]]):
    """Pick the symbol whose closes best match the fill prices. Returns (symbol, scale, error)."""
    best = (None, 1.0, float("inf"))
    for sym, series in series_by_symbol.items():
        for scale in (1.0, 0.01, 100.0):   # GBp vs GBP quirks
            errs = []
            for d, px in fills:
                c = _nearest(series, d)
                if c and px:
                    errs.append(abs(c * scale / px - 1))
            if errs:
                e = statistics.median(errs)
                if e < best[2]:
                    best = (sym, scale, e)
    return best


def resolve_and_price(instruments: dict[str, dict], start: date, end: date) -> tuple[dict, dict]:
    """instruments: ticker -> {isin, currency, fills: [(date, price_in_instrument_ccy)]}

    Returns (prices_gbp: ticker -> {date: close in GBP}, report: ticker -> {symbol, error} or {error})."""
    report, chosen = {}, {}
    for t, info in instruments.items():
        cands = candidates(t, info.get("isin"))
        data = _download(cands, start - timedelta(days=7), end)
        sym, scale, err = best_match(data, info.get("fills") or [])
        if sym is None and data and not info.get("fills"):
            sym, scale, err = next(iter(data)), 1.0, None
        same_symbol = sym is not None and sym == (cands[0] if cands else None)
        limit = MAX_ERROR_SAME_SYMBOL if same_symbol else MAX_MEDIAN_ERROR
        if sym is None or (err is not None and err > limit):
            report[t] = {"error": f"no reliable Yahoo match (tried {', '.join(cands) or 'nothing'})", "best": sym, "err": err}
            continue
        chosen[t] = (sym, scale, data[sym])
        report[t] = {"symbol": sym, "scale": scale, "fill_error": err}

    currencies = {(instruments[t].get("currency") or "USD").upper() for t in chosen}
    fx_syms = [FX_PAIRS[c] for c in currencies if c in FX_PAIRS]
    fx = _download(fx_syms, start - timedelta(days=7), end)

    prices: dict[str, dict[date, float]] = {}
    for t, (sym, scale, series) in chosen.items():
        ccy = (instruments[t].get("currency") or "USD").upper()
        out = {}
        for d, close in series.items():
            native = close * scale
            if ccy == "GBP":
                gbp = native
            elif ccy == "GBX":
                gbp = native / 100
            else:
                rate = _nearest(fx.get(FX_PAIRS.get(ccy, ""), {}), d)
                if not rate:
                    continue
                gbp = native / rate
            out[d] = gbp
        prices[t] = out
    return prices, report
