"""Quantitative analysis of the bot's daily returns, with significance tests.

Inputs are the unit-price series (deposit-neutral, so returns are the bot's own) and
benchmark price series. All tests are on daily simple returns; annualisation uses 252.
"""
from __future__ import annotations

import math
import random
from bisect import bisect_right
from datetime import date, datetime
from statistics import mean

from . import mathx

TD = 252


# ---------- basics ----------------------------------------------------------

def returns(prices: list[float]) -> list[float]:
    return [b / a - 1 for a, b in zip(prices, prices[1:]) if a]


def sample_std(x: list[float]) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    m = sum(x) / n
    return math.sqrt(sum((v - m) ** 2 for v in x) / (n - 1))


def skew_kurt(x: list[float]) -> tuple[float, float]:
    """Sample skewness and excess kurtosis (population moments)."""
    n = len(x)
    if n < 3:
        return 0.0, 0.0
    m = sum(x) / n
    m2 = sum((v - m) ** 2 for v in x) / n
    if m2 == 0:
        return 0.0, 0.0
    m3 = sum((v - m) ** 3 for v in x) / n
    m4 = sum((v - m) ** 4 for v in x) / n
    return m3 / m2 ** 1.5, m4 / m2 ** 2 - 3


def quantile(x: list[float], q: float) -> float:
    s = sorted(x)
    if not s:
        return float("nan")
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def align(series: list[tuple[date, float]], other: list[tuple[date, float]]) -> list[float | None]:
    """Value of `other` on or before each date of `series`."""
    od = [d for d, _ in other]
    out = []
    for d, _ in series:
        i = bisect_right(od, d)
        out.append(other[i - 1][1] if i else None)
    return out


# ---------- tests -----------------------------------------------------------

def ols(y: list[float], x: list[float]) -> dict:
    n = len(y)
    if n < 3:
        return {}
    mx, my = mean(x), mean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    if sxx == 0:
        return {}
    beta = sum((a - mx) * (b - my) for a, b in zip(x, y)) / sxx
    alpha = my - beta * mx
    resid = [b - alpha - beta * a for a, b in zip(x, y)]
    s2 = sum(r * r for r in resid) / (n - 2)
    se_b = math.sqrt(s2 / sxx)
    se_a = math.sqrt(s2 * (1 / n + mx * mx / sxx))
    sst = sum((b - my) ** 2 for b in y)
    r2 = 1 - sum(r * r for r in resid) / sst if sst else 0.0
    t_a = alpha / se_a if se_a else 0.0
    t_b = beta / se_b if se_b else 0.0
    return {"alpha": alpha, "beta": beta, "se_alpha": se_a, "se_beta": se_b, "t_alpha": t_a,
            "p_alpha": mathx.t_sf2(t_a, n - 2), "t_beta": t_b, "r2": r2, "n": n, "resid_std": math.sqrt(s2)}


def t_test_mean(x: list[float]) -> tuple[float, float]:
    n = len(x)
    sd = sample_std(x)
    if n < 3 or sd == 0:
        return 0.0, 1.0
    t = mean(x) / (sd / math.sqrt(n))
    return t, mathx.t_sf2(t, n - 1)


def jarque_bera(x: list[float]) -> tuple[float, float]:
    n = len(x)
    s, k = skew_kurt(x)
    jb = n / 6 * (s * s + k * k / 4)
    return jb, mathx.chi2_sf(jb, 2)


def ljung_box(x: list[float], lags: int = 5) -> tuple[float, float]:
    n = len(x)
    if n <= lags + 2:
        return 0.0, 1.0
    m = mean(x)
    d = [v - m for v in x]
    c0 = sum(v * v for v in d)
    if c0 == 0:
        return 0.0, 1.0
    q = 0.0
    for k in range(1, lags + 1):
        rk = sum(d[i] * d[i - k] for i in range(k, n)) / c0
        q += rk * rk / (n - k)
    q *= n * (n + 2)
    return q, mathx.chi2_sf(q, lags)


def sharpe_daily(x: list[float], rf_daily: float = 0.0) -> float | None:
    sd = sample_std(x)
    return (mean(x) - rf_daily) / sd if sd else None


def probabilistic_sharpe(x: list[float], sr_star: float = 0.0, rf_daily: float = 0.0) -> float | None:
    """Bailey & López de Prado: P(true Sharpe > sr_star), allowing for skew and fat tails."""
    sr = sharpe_daily(x, rf_daily)
    n = len(x)
    if sr is None or n < 3:
        return None
    g3, g4 = skew_kurt(x)
    denom = 1 - g3 * sr + (g4 + 3 - 1) / 4 * sr * sr
    if denom <= 0:
        return None
    return mathx.norm_cdf((sr - sr_star) * math.sqrt(n - 1) / math.sqrt(denom))


def min_track_record(x: list[float], sr_star: float = 0.0, conf: float = 0.95, rf_daily: float = 0.0) -> float | None:
    """Days of data needed to be `conf` sure the true Sharpe exceeds sr_star (at the observed one)."""
    sr = sharpe_daily(x, rf_daily)
    if sr is None or sr <= sr_star:
        return None
    g3, g4 = skew_kurt(x)
    z = mathx.norm_ppf(conf)
    return 1 + (1 - g3 * sr + (g4 + 2) / 4 * sr * sr) * (z / (sr - sr_star)) ** 2


def block_bootstrap(x: list[float], stat, n_boot: int = 2000, mean_block: int = 5, seed: int = 7) -> list[float]:
    """Stationary bootstrap (Politis & Romano) keeping short-range dependence."""
    n = len(x)
    if n < 10:
        return []
    rng = random.Random(seed)
    p = 1 / mean_block
    out = []
    for _ in range(n_boot):
        sample, i = [], rng.randrange(n)
        while len(sample) < n:
            sample.append(x[i])
            i = rng.randrange(n) if rng.random() < p else (i + 1) % n
        v = stat(sample)
        if v is not None and v == v:
            out.append(v)
    return out


# ---------- drawdowns -------------------------------------------------------

def drawdown_stats(series: list[tuple[date, float]]) -> dict:
    peak, peak_i = -math.inf, 0
    worst, worst_i, worst_peak_i = 0.0, 0, 0
    longest, cur_len = 0, 0
    dd2 = []
    for i, (_, p) in enumerate(series):
        if p >= peak:
            peak, peak_i, cur_len = p, i, 0
        else:
            cur_len += 1
            longest = max(longest, cur_len)
        dd = p / peak - 1 if peak > 0 else 0.0
        dd2.append(dd * dd)
        if dd < worst:
            worst, worst_i, worst_peak_i = dd, i, peak_i
    recovered = None
    if worst < 0:
        peak_val = series[worst_peak_i][1]
        recovered = next((series[j][0].isoformat() for j in range(worst_i, len(series)) if series[j][1] >= peak_val), None)
    last_peak = max(p for _, p in series)
    return {
        "max_drawdown": worst,
        "max_dd_peak": series[worst_peak_i][0].isoformat() if worst < 0 else None,
        "max_dd_trough": series[worst_i][0].isoformat() if worst < 0 else None,
        "max_dd_recovered": recovered,
        "longest_underwater_days": longest,
        "current_drawdown": series[-1][1] / last_peak - 1,
        "ulcer_index": math.sqrt(sum(dd2) / len(dd2)) if dd2 else 0.0,
    }


# ---------- main ------------------------------------------------------------

def analyse(series: list[tuple[date, float]], benchmarks: dict[str, tuple[str, list[tuple[date, float]]]],
            move_days: list[date], rf_annual: float = 0.0, periods_per_year: float = TD) -> dict:
    """periods_per_year: T for daily closes; more for intraday readings (see service)."""
    if len(series) < 5:
        return {"ready": False, "reason": "needs at least a week of daily data"}
    T = periods_per_year
    prices = [p for _, p in series]
    r = returns(prices)
    n = len(r)
    rf_d = rf_annual / T
    years = (series[-1][0] - series[0][0]).days / 365.25
    total = prices[-1] / prices[0] - 1
    sd = sample_std(r)
    vol = sd * math.sqrt(T)
    downside = math.sqrt(sum(min(0.0, v - rf_d) ** 2 for v in r) / n) * math.sqrt(T)
    ann_mean = mean(r) * T
    g3, g4 = skew_kurt(r)
    dd = drawdown_stats(series)
    cagr = (1 + total) ** (1 / years) - 1 if years >= 0.25 else None
    wins, losses = [v for v in r if v > 0], [v for v in r if v < 0]
    var95 = -quantile(r, 0.05)
    tail = [v for v in r if v <= -var95]
    gains_above = sum(v - rf_d for v in r if v > rf_d)
    losses_below = -sum(v - rf_d for v in r if v < rf_d)

    out: dict = {
        "ready": True,
        "n_days": n,
        "start": series[0][0].isoformat(),
        "end": series[-1][0].isoformat(),
        "years": years,
        "returns": {
            "total": total,
            "cagr": cagr,
            "mean_daily": mean(r),
            "pct_up_days": len(wins) / n,
            "avg_up_day": mean(wins) if wins else None,
            "avg_down_day": mean(losses) if losses else None,
            "win_loss_ratio": (mean(wins) / -mean(losses)) if wins and losses else None,
            "profit_factor": (sum(wins) / -sum(losses)) if losses else None,
            "best_day": max(r), "worst_day": min(r),
        },
        "risk": {
            "volatility": vol, "downside_deviation": downside,
            "var95_daily": var95, "cvar95_daily": -mean(tail) if tail else None,
            "skew": g3, "excess_kurtosis": g4, **dd,
        },
        "ratios": {
            "sharpe": (ann_mean - rf_annual) / vol if vol else None,
            "sortino": (ann_mean - rf_annual) / downside if downside else None,
            "calmar": (cagr / -dd["max_drawdown"]) if cagr is not None and dd["max_drawdown"] < 0 else None,
            "omega": (gains_above / losses_below) if losses_below else None,
        },
    }

    # significance of the bot's own returns
    t, p = t_test_mean([v - rf_d for v in r])
    boot_ret = block_bootstrap(r, lambda x: mean(x) * T)
    boot_sr = block_bootstrap(r, lambda x: (mean(x) * T - rf_annual) / (sample_std(x) * math.sqrt(T)) if sample_std(x) else None)
    jb, jb_p = jarque_bera(r)
    lb, lb_p = ljung_box(r, 5)
    out["significance"] = {
        "t_mean": t, "p_mean": p,
        "annual_return_ci95": [quantile(boot_ret, 0.025), quantile(boot_ret, 0.975)] if boot_ret else None,
        "sharpe_ci95": [quantile(boot_sr, 0.025), quantile(boot_sr, 0.975)] if boot_sr else None,
        "prob_sharpe_positive": probabilistic_sharpe(r, 0.0, rf_d),
        "min_track_record_days": (lambda m: m * TD / T if m is not None else None)(min_track_record(r, 0.0, 0.95, rf_d)),
        "jarque_bera": jb, "jarque_bera_p": jb_p,
        "ljung_box": lb, "ljung_box_p": lb_p,
    }

    # versus each benchmark
    vs = {}
    for key, (name, bser) in benchmarks.items():
        b = align(series, bser)
        if b[0] is None or sum(1 for v in b if v is None) > len(b) // 4:
            continue
        pairs = [(series[i][1], b[i]) for i in range(len(series)) if b[i]]
        rb = [y / x - 1 for (x, _), (y, _) in zip(pairs, pairs[1:]) if x]
        rm = [y / x - 1 for (_, x), (_, y) in zip(pairs, pairs[1:]) if x]
        m = min(len(rb), len(rm))
        rb, rm = rb[:m], rm[:m]
        if m < 5:
            continue
        reg = ols([v - rf_d for v in rb], [v - rf_d for v in rm])
        active = [x - y for x, y in zip(rb, rm)]
        te = sample_std(active) * math.sqrt(T)
        b_total = pairs[-1][1] / pairs[0][1] - 1
        up = [(x, y) for x, y in zip(rb, rm) if y > 0]
        dn = [(x, y) for x, y in zip(rb, rm) if y < 0]
        ta, pa = t_test_mean(active)
        # months beaten
        months = {}
        for (d, _), (pb, pm) in zip(series, pairs):
            months.setdefault((d.year, d.month), []).append((pb, pm))
        mk = sorted(months)
        beaten = 0
        prev = pairs[0]
        for k in mk:
            last = months[k][-1]
            if (last[0] / prev[0]) > (last[1] / prev[1]):
                beaten += 1
            prev = last
        vs[key] = {
            "name": name,
            "total": b_total,
            "excess_total": total - b_total,
            "beta": reg.get("beta"), "alpha_annual": reg["alpha"] * T if reg else None,
            "alpha_t": reg.get("t_alpha"), "alpha_p": reg.get("p_alpha"), "beta_t": reg.get("t_beta"),
            "r2": reg.get("r2"), "correlation": math.copysign(math.sqrt(reg["r2"]), reg["beta"]) if reg else None,
            "tracking_error": te,
            "information_ratio": (mean(active) * T) / te if te else None,
            "active_t": ta, "active_p": pa,
            "up_capture": (mean(x for x, _ in up) / mean(y for _, y in up)) if up else None,
            "down_capture": (mean(x for x, _ in dn) / mean(y for _, y in dn)) if dn else None,
            "months_beaten": beaten, "months": len(mk),
            "treynor": ((ann_mean - rf_annual) / reg["beta"]) if reg and reg.get("beta") else None,
        }
    out["vs"] = vs

    # the bot's moves: return between one move and the next, vs the first benchmark
    days = sorted(set(d for d in move_days if series[0][0] <= d <= series[-1][0]))
    first_b = next(iter(benchmarks.values()), None)
    windows = []
    for i, d0 in enumerate(days):
        d1 = days[i + 1] if i + 1 < len(days) else series[-1][0]
        p0 = _at(series, d0); p1 = _at(series, d1)
        if not p0 or not p1 or d1 <= d0:
            continue
        w = {"from": d0.isoformat(), "to": d1.isoformat(), "bot": p1 / p0 - 1}
        if first_b:
            b0, b1 = _at(first_b[1], d0), _at(first_b[1], d1)
            if b0 and b1:
                w["bench"] = b1 / b0 - 1
        windows.append(w)
    beat = [w for w in windows if "bench" in w]
    out["moves"] = {
        "count": len(windows),
        "win_rate": (sum(w["bot"] > 0 for w in windows) / len(windows)) if windows else None,
        "beat_bench_rate": (sum(w["bot"] > w["bench"] for w in beat) / len(beat)) if beat else None,
        "avg_return": mean(w["bot"] for w in windows) if windows else None,
        "avg_excess": mean(w["bot"] - w["bench"] for w in beat) if beat else None,
        "avg_days_between": mean(_days_between(w["from"], w["to"]) for w in windows) if windows else None,
        "windows": windows[-30:],
    }

    # charts: histogram of daily returns, rolling 20-day return vs first benchmark
    lo, hi = min(r), max(r)
    bins = 15
    width = (hi - lo) / bins or 1e-9
    hist = [0] * bins
    for v in r:
        hist[min(bins - 1, int((v - lo) / width))] += 1
    out["histogram"] = [{"lo": lo + i * width, "hi": lo + (i + 1) * width, "n": c} for i, c in enumerate(hist)]
    roll = []
    bser = first_b[1] if first_b else []
    bal = align(series, bser) if bser else [None] * len(series)
    W = 20
    for i in range(W, len(series)):
        item = {"t": series[i][0].isoformat(), "bot": series[i][1] / series[i - W][1] - 1}
        if bal[i] and bal[i - W]:
            item["bench"] = bal[i] / bal[i - W] - 1
        roll.append(item)
    out["rolling20"] = roll
    out["verdict"] = verdict(out)
    return out


def _days_between(a: str, b: str) -> float:
    """Days between two ISO dates or timestamps (fractions for timestamps)."""
    if "T" in a or "T" in b:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 86400
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _at(series, d):
    ds = [x for x, _ in series]
    i = bisect_right(ds, d)
    return series[i - 1][1] if i else None


def verdict(a: dict) -> list[str]:
    """Plain-English reading of the numbers, careful about what small samples can show."""
    s = a["significance"]
    lines = []
    n = a["n_days"]
    psr = s.get("prob_sharpe_positive")
    if psr is not None:
        lines.append(f"Given the swings so far, there's a {psr:.0%} probability the bot's true Sharpe ratio is above zero "
                     f"(probabilistic Sharpe ratio, adjusted for skew and fat tails).")
    if s["p_mean"] is not None:
        sig = "is" if s["p_mean"] < 0.05 else "isn't"
        lines.append(f"The average daily return {sig} statistically different from zero after {n} trading days (p = {s['p_mean']:.2f}).")
    mtr = s.get("min_track_record_days")
    if mtr:
        more = max(0, mtr - n)
        lines.append(f"At this Sharpe ratio, about {mtr:,.0f} trading days are needed to be 95% sure it's positive"
                     + (f", roughly {more / TD:.1f} more years." if more > 0 else ", which the record already has."))
    for key, v in a.get("vs", {}).items():
        if v.get("alpha_p") is not None:
            word = "significant" if v["alpha_p"] < 0.05 else "not yet significant"
            lines.append(f"Against the {v['name']}: alpha {v['alpha_annual']:+.1%} a year with beta {v['beta']:.2f}; "
                         f"the alpha is {word} (p = {v['alpha_p']:.2f}).")
        break
    if s.get("jarque_bera_p") is not None and s["jarque_bera_p"] < 0.05:
        lines.append("Daily returns aren't normally distributed (fat tails or skew), so the bootstrap ranges are more reliable than the normal-theory p-values.")
    return lines
