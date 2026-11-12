"""The deeper analysis: regression and style-factor exposures."""
import numpy as np
import pytest

from tracker import quant


def test_ols_recovers_a_known_line():
    rng = np.random.default_rng(0)
    x = rng.normal(0, 0.01, 500)
    y = 0.0002 + 1.5 * x + rng.normal(0, 0.001, 500)
    f = quant.ols(y, x[:, None])
    assert f["coef"][1] == pytest.approx(1.5, abs=0.05) and f["r2"] > 0.9


def test_factor_exposures_find_the_right_tilts():
    rng = np.random.default_rng(1)
    F = rng.normal(0, 0.01, (400, 6))
    y = F @ np.array([1.0, 0.5, -0.3, 0.0, 0.0, 0.0]) + rng.normal(0, 0.002, 400)
    out = quant.factor_exposures(y, F)
    betas = [f["beta"] for f in out["factors"]]
    assert betas[:3] == pytest.approx([1.0, 0.5, -0.3], abs=0.05)
    assert out["factors"][3]["lo"] < 0 < out["factors"][3]["hi"]          # no momentum tilt: interval spans 0


def test_iv_between_expiries_is_linear_in_total_variance():
    surf = {"expiries": [{"days": 30}, {"days": 90}], "iv": [[0.40, 0.30], [0.20, 0.20]]}
    mid = quant._iv_at(surf, 60)
    assert mid[0] == pytest.approx(((0.5 * 0.16 * 30 + 0.5 * 0.04 * 90) / 60) ** 0.5)
    assert quant._iv_at(surf, 10) == [0.40, 0.30] and quant._iv_at(surf, 400) == [0.20, 0.20]   # flat beyond


def test_whole_pie_surface_combines_with_correlation(tmp_path, monkeypatch):
    from datetime import date, timedelta
    from tracker import market
    from tracker.db import make_session_factory, set_meta
    M = [0.9, 1.0, 1.1]
    surf = lambda v: {"available": True, "moneyness": M, "expiries": [{"days": 30}, {"days": 365}], "iv": [[v] * 3, [v] * 3]}
    vols = {"A": surf(0.4), "B": surf(0.4), "C": surf(0.4)}
    monkeypatch.setattr(market, "get", lambda s, kind, sym, allow_fetch=True: vols.get(sym))
    rng = np.random.default_rng(3)
    base = rng.normal(0, 0.01, 200)
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(201)]
    S = make_session_factory(f"sqlite:///{tmp_path / 'v.db'}")
    with S() as s:
        set_meta(s, "yahoo_map", {"A_US_EQ": "A", "B_US_EQ": "B", "C_US_EQ": "C"})
        set_meta(s, "yahoo_closes", {"built": "x", "per_share": {   # three identical stocks: correlation 1
            t: [[d.isoformat(), float(100 * np.prod(1 + base[:i]))] for i, d in enumerate(days)] for t in ("A_US_EQ", "B_US_EQ", "C_US_EQ")}})
        out = quant.pie_vol_surface(s, [{"ticker": t, "weight": 1 / 3} for t in ("A_US_EQ", "B_US_EQ", "C_US_EQ")])
    assert out["available"] and out["covered"] == 3
    assert out["iv"][0][1] == pytest.approx(0.4, abs=1e-3)   # perfectly correlated: no diversification
