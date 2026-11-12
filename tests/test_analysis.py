import math
import random
from datetime import date, timedelta

import pytest

from tracker import analysis, mathx


def test_distribution_functions_against_known_values():
    assert mathx.norm_cdf(1.96) == pytest.approx(0.975, abs=1e-4)
    assert mathx.norm_ppf(0.975) == pytest.approx(1.959964, abs=1e-6)
    assert mathx.norm_ppf(0.01) == pytest.approx(-2.326348, abs=1e-6)
    # t(10): two-sided p for t=2.228 is 0.05
    assert mathx.t_sf2(2.228, 10) == pytest.approx(0.05, abs=1e-3)
    assert mathx.t_sf2(0.0, 5) == pytest.approx(1.0)
    # chi2: P(X>5.991 | k=2) = 0.05 ; P(X>11.07 | k=5) = 0.05
    assert mathx.chi2_sf(5.991, 2) == pytest.approx(0.05, abs=1e-3)
    assert mathx.chi2_sf(11.07, 5) == pytest.approx(0.05, abs=1e-3)


def test_ols_recovers_known_line():
    rng = random.Random(1)
    x = [rng.gauss(0, 0.01) for _ in range(500)]
    y = [0.0005 + 1.5 * v + rng.gauss(0, 0.001) for v in x]
    reg = analysis.ols(y, x)
    assert reg["beta"] == pytest.approx(1.5, abs=0.03)
    assert reg["alpha"] == pytest.approx(0.0005, abs=0.0002)
    assert reg["p_alpha"] < 0.01 and reg["r2"] > 0.9


def test_skew_kurtosis_and_normality():
    rng = random.Random(2)
    normal = [rng.gauss(0, 1) for _ in range(4000)]
    s, k = analysis.skew_kurt(normal)
    assert abs(s) < 0.1 and abs(k) < 0.2
    assert analysis.jarque_bera(normal)[1] > 0.01
    fat = [rng.gauss(0, 1) * (5 if rng.random() < 0.05 else 1) for _ in range(4000)]
    assert analysis.jarque_bera(fat)[1] < 0.001


def test_ljung_box_detects_autocorrelation():
    rng = random.Random(3)
    iid = [rng.gauss(0, 1) for _ in range(1000)]
    ar = [0.0]
    for _ in range(999):
        ar.append(0.6 * ar[-1] + rng.gauss(0, 1))
    assert analysis.ljung_box(iid)[1] > 0.01
    assert analysis.ljung_box(ar)[1] < 1e-6


def test_drawdown_stats():
    d0 = date(2026, 1, 1)
    s = [(d0 + timedelta(days=i), p) for i, p in enumerate([1, 1.2, 0.9, 1.0, 1.25, 1.1])]
    dd = analysis.drawdown_stats(s)
    assert dd["max_drawdown"] == pytest.approx(0.9 / 1.2 - 1)
    assert dd["max_dd_recovered"] == (d0 + timedelta(days=4)).isoformat()
    assert dd["current_drawdown"] == pytest.approx(1.1 / 1.25 - 1)
    assert dd["longest_underwater_days"] == 2


def test_psr_and_min_track_record_behave():
    rng = random.Random(4)
    good = [rng.gauss(0.002, 0.01) for _ in range(500)]
    noise = [rng.gauss(0.0, 0.01) for _ in range(500)]
    assert analysis.probabilistic_sharpe(good) > 0.99
    assert 0.05 < analysis.probabilistic_sharpe(noise) < 0.95
    assert analysis.min_track_record(good) < 500


def test_analyse_end_to_end_and_benchmark_identity():
    rng = random.Random(5)
    d0 = date(2026, 6, 1)
    p, b = 1.0, 100.0
    bot, bench = [], []
    for i in range(150):
        m = rng.gauss(0.0004, 0.01)
        b *= 1 + m
        p *= 1 + 1.2 * m + rng.gauss(0.0002, 0.004)
        day = d0 + timedelta(days=i)
        bot.append((day, p)); bench.append((day, b))
    a = analysis.analyse(bot, {"spx": ("S&P 500", bench)}, [d0 + timedelta(days=30), d0 + timedelta(days=90)])
    v = a["vs"]["spx"]
    assert v["beta"] == pytest.approx(1.2, abs=0.15)
    assert 0 <= a["significance"]["prob_sharpe_positive"] <= 1
    assert a["moves"]["count"] == 2
    assert sum(h["n"] for h in a["histogram"]) == 149
    assert a["verdict"] and all(isinstance(x, str) for x in a["verdict"])
    # a benchmark compared with itself has beta 1, alpha 0
    same = analysis.analyse(bench, {"self": ("self", bench)}, [])
    assert same["vs"]["self"]["beta"] == pytest.approx(1.0)
    assert same["vs"]["self"]["alpha_annual"] == pytest.approx(0.0, abs=1e-12)
