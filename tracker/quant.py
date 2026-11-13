"""Deeper analysis of the stocks the pie holds now, from a year of daily prices in GBP.

Built by the poller (every few hours) and kept in the database; the Quant page reads it.

- rolling: beta and alpha of today's mix against the S&P over a moving 3-month window
- factors: today's mix regressed on style funds (market, small caps, value vs growth,
  momentum, quality, low volatility), with standard errors
- options: each stock's implied volatility (option prices, about a month out) against how much
  it has actually moved over the last month, and the same for the whole mix

Today's mix means today's weights held through the past year; the pie's own short record
can't support any of these yet.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone

import numpy as np
from sqlalchemy.orm import Session

from . import market, yahoo_closes
from .db import set_meta

log = logging.getLogger(__name__)

META_KEY = "quant_pack"
TD = 252
ROLL = 63                     # rolling window: about three months
FACTOR_FUNDS = ["SPY", "IWM", "IWD", "IWF", "MTUM", "QUAL", "USMV"]
FACTORS = [  # name, explanation, (long fund, short fund or None)
    ("Market", "the S&P 500 (SPY)", ("SPY", None)),
    ("Small companies", "small caps (IWM) minus the S&P", ("IWM", "SPY")),
    ("Value", "value (IWD) minus growth (IWF)", ("IWD", "IWF")),
    ("Momentum", "recent winners (MTUM) minus the S&P", ("MTUM", "SPY")),
    ("Quality", "profitable, steady companies (QUAL) minus the S&P", ("QUAL", "SPY")),
    ("Low volatility", "calm stocks (USMV) minus the S&P", ("USMV", "SPY")),
]


# ---------------------------------------------------------------- data

def _usd_gbp(s: Session) -> dict[date, float]:
    h = market.get(s, "history", "GBPUSD=X", allow_fetch=False) or {}
    return {date.fromisoformat(p["t"]): p["close"] for p in h.get("points", []) if p.get("close")}


def fund_prices_gbp(s: Session, sym: str, fx: dict[date, float]) -> dict[date, float]:
    h = market.get(s, "history", sym, allow_fetch=False) or {}
    out = {}
    for p in h.get("points", []):
        d = date.fromisoformat(p["t"])
        if p.get("close") and fx.get(d):
            out[d] = p["close"] / fx[d]
    return out


def returns_matrix(series: dict[str, dict[date, float]], min_days: int = 120):
    """Daily returns on the days every series has a price: (dates, tickers, T x N array)."""
    series = {k: v for k, v in series.items() if len(v) > min_days}
    if not series:
        return [], [], np.zeros((0, 0))
    days = sorted(set.intersection(*(set(v) for v in series.values())))
    tick = sorted(series)
    P = np.array([[series[k][d] for k in tick] for d in days])
    if len(days) < 30:
        return [], [], np.zeros((0, 0))
    return days[1:], tick, P[1:] / P[:-1] - 1


def ols(y: np.ndarray, X: np.ndarray) -> dict:
    """y = a + X b. Coefficients, standard errors, R squared."""
    A = np.column_stack([np.ones(len(y)), X])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ coef
    dof = max(1, len(y) - A.shape[1])
    s2 = float(resid @ resid) / dof
    cov = s2 * np.linalg.pinv(A.T @ A)
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    tss = float(((y - y.mean()) ** 2).sum())
    return {"coef": coef, "se": se, "r2": 1 - float(resid @ resid) / tss if tss > 0 else 0.0}


# ---------------------------------------------------------------- pieces

def rolling_beta(dates, mix: np.ndarray, bench: np.ndarray, window: int = ROLL) -> list[dict]:
    out = []
    for i in range(window, len(mix) + 1):
        y, x = mix[i - window:i], bench[i - window:i]
        f = ols(y, x[:, None])
        b, se = float(f["coef"][1]), float(f["se"][1])
        out.append({"t": dates[i - 1].isoformat(), "beta": b, "lo": b - 1.96 * se, "hi": b + 1.96 * se,
                    "alpha": float(f["coef"][0]) * TD, "r2": f["r2"]})
    return out


def factor_exposures(mix: np.ndarray, F: np.ndarray) -> dict:
    f = ols(mix, F)
    rows = []
    for i, (name, about, _) in enumerate(FACTORS):
        b, se = float(f["coef"][i + 1]), float(f["se"][i + 1])
        rows.append({"name": name, "about": about, "beta": b, "lo": b - 1.96 * se, "hi": b + 1.96 * se,
                     "t": b / se if se else None})
    a, ase = float(f["coef"][0]), float(f["se"][0])
    return {"factors": rows, "alpha": a * TD, "alpha_lo": (a - 1.96 * ase) * TD, "alpha_hi": (a + 1.96 * ase) * TD,
            "r2": f["r2"], "days": len(mix)}


def _iv30(vol: dict | None) -> float | None:
    """At-the-money implied volatility about 30 days out, from the cached option surface."""
    term = (vol or {}).get("atm_term") or []
    term = sorted((t["days"], t["iv"]) for t in term if t.get("iv"))
    if not term:
        return None
    if term[0][0] >= 30:
        return term[0][1]
    for (d0, v0), (d1, v1) in zip(term, term[1:]):
        if d0 <= 30 <= d1:
            return v0 + (v1 - v0) * (30 - d0) / (d1 - d0) if d1 > d0 else v0
    return term[-1][1]


def options_vs_reality(s: Session, tick, R: np.ndarray, weights: dict[str, float], symbols: dict[str, str]) -> dict:
    ymap = {}
    from .db import get_meta
    ymap = get_meta(s, "yahoo_map", {}) or {}
    rv = R[-21:].std(axis=0, ddof=1) * math.sqrt(TD)   # last month, a year's worth
    rows = []
    iv = np.full(len(tick), np.nan)
    for i, t in enumerate(tick):
        sym = ymap.get(t)
        v = _iv30(market.get(s, "vol", sym, allow_fetch=False)) if sym else None
        if v:
            iv[i] = v
            rows.append({"symbol": symbols.get(t, t), "iv": v, "rv": float(rv[i]), "weight": weights.get(t, 0.0)})
    ok = ~np.isnan(iv)
    pie = {}
    if ok.sum() >= 3:
        w = np.array([weights.get(t, 0.0) for t in tick])[ok]
        w = w / w.sum()
        C = np.corrcoef(R[:, ok], rowvar=False)
        pie = {"iv": float(np.sqrt(w @ (np.outer(iv[ok], iv[ok]) * C) @ w)),
               "rv": float(np.sqrt(w @ (np.outer(rv[ok], rv[ok]) * C) @ w)), "covered": int(ok.sum())}
    return {"stocks": rows, "pie": pie}


TENORS = [14, 30, 61, 91, 122, 182, 273, 365]   # days: the whole-pie surface's expiries


def _iv_at(surf: dict, days: int) -> list[float] | None:
    """One stock's implied vols across the strike grid at `days` to expiry: linear in total
    variance (vol squared x time) between its quoted expiries, flat beyond them."""
    E = [e["days"] for e in surf.get("expiries", [])]
    Z = surf.get("iv") or []
    if not E or len(Z) != len(E):
        return None
    pairs = sorted(zip(E, Z))
    if days <= pairs[0][0]:
        return list(pairs[0][1])
    if days >= pairs[-1][0]:
        return list(pairs[-1][1])
    for (d0, z0), (d1, z1) in zip(pairs, pairs[1:]):
        if d0 <= days <= d1:
            f = (days - d0) / (d1 - d0) if d1 > d0 else 0
            return [math.sqrt(max(1e-8, ((1 - f) * a * a * d0 + f * b * b * d1) / days)) for a, b in zip(z0, z1)]
    return None


def pie_vol_surface(s: Session, holdings: list[dict]) -> dict:
    """The whole pie's implied volatility surface: at each strike (% of price) and expiry, each
    stock's implied vol combined with today's weights and how the stocks have moved together
    over the past year (correlations), the way risk adds up for a mix:
    vol = sqrt(sum_ij w_i w_j vol_i vol_j corr_ij). US holdings with options only."""
    from .db import get_meta
    ymap = get_meta(s, "yahoo_map", {}) or {}
    surfs, weights = {}, {}
    for h in holdings:
        sym = ymap.get(h["ticker"])
        v = market.get(s, "vol", sym, allow_fetch=False) if sym else None
        if v and v.get("available") and h.get("weight"):
            surfs[h["ticker"]], weights[h["ticker"]] = v, h["weight"]
    # stocks showing their last known option prices because live ones weren't available
    kept = [v.get("as_of") for v in surfs.values() if v.get("_kept")]
    if len(surfs) < 3:
        return {"available": False}
    per = yahoo_closes.per_share(s)
    days, tick, R = returns_matrix({t: dict(per.get(t, [])) for t in surfs})
    if len(days) < 60:
        return {"available": False}
    C = np.corrcoef(R[-TD:], rowvar=False)
    w = np.array([weights[t] for t in tick])
    covered = float(w.sum())
    w = w / w.sum()
    moneyness = next(iter(surfs.values()))["moneyness"]
    rows, used = [], []
    longest = max(max(e["days"] for e in surfs[t]["expiries"]) for t in tick)
    for T in [d for d in TENORS if d <= max(longest, TENORS[1])]:
        grids = [_iv_at(surfs[t], T) for t in tick]
        if any(g is None for g in grids):
            continue
        G = np.array(grids)                         # stocks x strikes
        row = [float(np.sqrt(max(0.0, (w * G[:, j]) @ C @ (w * G[:, j])))) for j in range(G.shape[1])]
        rows.append([round(x, 4) for x in row])
        used.append({"date": (date.today() + timedelta(days=T)).isoformat(), "days": T})
    if len(rows) < 2:
        return {"available": False}
    atm = moneyness.index(1.0)
    return {"available": True, "pie": True, "spot": None, "moneyness": moneyness, "expiries": used, "iv": rows,
            "atm_term": [{"days": u["days"], "iv": r[atm]} for u, r in zip(used, rows)],
            "sqrt_t": [round(math.sqrt(u["days"] / 365), 4) for u in used],
            "covered": len(tick), "weight_covered": covered,
            "as_of": min(kept) if kept else datetime.now(timezone.utc).isoformat(),
            "stale": bool(kept), "stale_count": len(kept)}


# ---------------------------------------------------------------- build

def build(s: Session, holdings: list[dict], benchmark: str = "VUAG.L") -> dict | None:
    """holdings: [{ticker, symbol, weight}] (today's). Stores and returns the pack."""
    per = yahoo_closes.per_share(s)
    weights = {h["ticker"]: h["weight"] for h in holdings if h.get("weight")}
    symbols = {h["ticker"]: h["symbol"] for h in holdings}
    days, tick, R = returns_matrix({t: dict(per.get(t, [])) for t in weights})
    if len(days) < ROLL + 5:
        return None
    w = np.array([weights[t] for t in tick])
    w = w / w.sum()
    mix = R @ w
    fx = _usd_gbp(s)
    bench_px = {date.fromisoformat(p["t"]): p["close"]
                for p in (market.get(s, "history", benchmark, allow_fetch=False) or {}).get("points", []) if p.get("close")}
    out: dict = {"built": datetime.now(timezone.utc).isoformat(), "days": len(days),
                 "from": days[0].isoformat(), "to": days[-1].isoformat(), "holdings": len(tick),
                 "mix_returns": [[d.isoformat(), round(float(r), 6)] for d, r in zip(days, mix)]}
    # rolling beta against the benchmark (both in GBP), on the days both have prices
    bd = [i for i, d in enumerate(days) if d in bench_px and _prev(bench_px, d)]
    if len(bd) > ROLL + 5:
        b = np.array([bench_px[days[i]] / _prev(bench_px, days[i]) - 1 for i in bd])
        out["rolling"] = rolling_beta([days[i] for i in bd], mix[bd], b)
    # style factors
    funds = {f: fund_prices_gbp(s, f, fx) for f in FACTOR_FUNDS}
    fdays, ftick, FR = returns_matrix(funds, min_days=100)
    if len(fdays):
        idx = {d: i for i, d in enumerate(fdays)}
        common = [i for i, d in enumerate(days) if d in idx]
        col = {t: FR[:, j] for j, t in enumerate(ftick)}
        cols = []
        for _, _, (long, short) in FACTORS:
            cols.append(col[long] - (col[short] if short else 0))
        Fm = np.column_stack(cols)[[idx[days[i]] for i in common]]
        if len(common) > 60:
            out["factors"] = factor_exposures(mix[common], Fm)
    out["options"] = options_vs_reality(s, tick, R, weights, symbols)
    set_meta(s, META_KEY, out)
    return out


def _prev(px: dict[date, float], d: date) -> float | None:
    earlier = [x for x in px if x < d]
    return px[max(earlier)] if earlier else None
