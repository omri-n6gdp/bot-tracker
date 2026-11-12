"""The daily recap and the Monte Carlo range of outcomes."""
import math
import random
from datetime import date, datetime, timedelta

import pytest

from tracker import montecarlo, service
from tracker.config import Settings
from tracker.db import HoldingSnapshot, PieSnapshot, make_session_factory, set_meta


def test_monte_carlo_spread_has_no_trend_and_widens():
    rng = random.Random(1)
    daily = [0.004 + rng.gauss(0, 0.015) for _ in range(250)]     # a strong upward drift...
    sim = montecarlo.simulate(daily, 1000.0, 100.0, horizon=252, paths=800, seed=3)
    fan = sim["fan"]
    p5, p50, p95 = fan[-1][1], fan[-1][3], fan[-1][5]
    assert p5 < p50 < p95
    assert abs(p50 / 1000 - 1) < 0.06                               # ...is taken out: no trend
    assert (fan[-1][5] - fan[-1][1]) > (fan[20][5] - fan[20][1])    # wider further out
    assert 0.3 < sim["horizons"]["252"]["loss"] < 0.7
    # cash stays flat: the invested 900 swings, so the spread is about 0.9 of the stocks'
    assert sim["vol_annual"] == pytest.approx(0.015 * math.sqrt(252) * 0.9, rel=0.15)


def test_monte_carlo_build_uses_todays_weights(tmp_path):
    S = make_session_factory(f"sqlite:///{tmp_path / 'mc.db'}")
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(200)]
    with S() as s:
        set_meta(s, "yahoo_closes", {"built": "x", "tickers": ["A", "B"], "per_share": {
            "A": [[d.isoformat(), 100 * (1.01 ** (i % 7))] for i, d in enumerate(days)],
            "B": [[d.isoformat(), 50 + (i % 3)] for i, d in enumerate(days)]}})
        out = montecarlo.build(s, [("A", 600.0), ("B", 300.0)], cash=100.0, today=date(2026, 11, 12))
    assert out["value"] == 1000.0 and out["holdings"] == 2 and len(out["fan"]) == 252


def test_recap_text_reads_well():
    r = {"date": "2026-11-12", "value": 953.19, "return_on_deposits": 0.0034, "net_deposits": 950.0,
         "since_open": {"pct": 0.0088, "gbp": 8.33}, "bench_since_open": {"VUAG.L": {"name": "S&P 500", "change": 0.0053}},
         "best": {"symbol": "SNDX", "pct": 0.0319}, "worst": {"symbol": "NVDA", "pct": -0.0662}, "up": 11, "down": 8,
         "moves": [{"count": 16, "added": ["BHF"], "removed": ["NBIX"], "judged": {"value_added": -0.0024}}],
         "game": {"open_week": "2026-11-16", "guessed": 2, "players": 5, "current": None, "just_scored": None}}
    text = service.recap_text(r)
    assert "Thu 12 Nov: the pie is worth £953.19, +0.34% on £950 paid in." in text
    assert "Since the open: +0.88% (+£8.33), S&P 500 +0.53%." in text
    assert "bought BHF, sold NBIX" in text and "-0.24 pts against keeping the old mix" in text
    assert "2 of 5 in" in text


def test_risk_against_reward_point():
    prices = [100 * (1.01 if i % 2 else 0.995) ** i for i in range(80)]
    pt = service._rr_point(prices, 63)
    assert pt["ret"] == pytest.approx(prices[-1] / prices[-64] - 1)
    assert pt["swing"] > 0 and pt["vol"] == pytest.approx(pt["swing"] / math.sqrt(63) * math.sqrt(252))
    assert service._rr_point(prices[:30], 63) is None            # not enough history for the window


def test_first_range_is_kept_and_carries_sample_paths(tmp_path):
    S = make_session_factory(f"sqlite:///{tmp_path / 'mca.db'}")
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(200)]
    with S() as s:
        set_meta(s, "yahoo_closes", {"built": "x", "tickers": ["A"], "per_share": {
            "A": [[d.isoformat(), 100 * (1.01 ** (i % 7))] for i, d in enumerate(days)]}})
        first = montecarlo.build(s, [("A", 900.0)], cash=100.0, today=date(2026, 11, 12), unit_price=1.0)
        montecarlo.build(s, [("A", 950.0)], cash=100.0, today=date(2026, 11, 13), unit_price=1.02)
        from tracker.db import get_meta
        anchor = get_meta(s, montecarlo.ANCHOR_KEY)
    assert anchor["value"] == first["value"] == 1000.0 and anchor["unit_price"] == 1.0
    assert len(first["samples"]) == montecarlo.SAMPLE_PATHS and len(first["samples"][0]) == montecarlo.HORIZON
