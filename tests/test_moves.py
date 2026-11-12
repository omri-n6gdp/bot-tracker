"""Did the bot's moves help, and headlines from around them."""
from datetime import datetime, timedelta, timezone

import pytest

from tracker import market, moves
from tracker.db import Rebalance, make_session_factory

T0 = datetime(2026, 11, 12, 14, 45)
PRICES = {  # ticker -> (price at the move, price now)
    "A_US_EQ": (100, 110), "B_US_EQ": (50, 45), "C_US_EQ": (20, 21),
}


def _db(tmp_path):
    S = make_session_factory(f"sqlite:///{tmp_path / 'm.db'}")
    with S() as s:
        s.add(Rebalance(ts=T0, weights_before={"A_US_EQ": 0.5, "B_US_EQ": 0.5},
                        weights_after={"A_US_EQ": 0.5, "C_US_EQ": 0.5},
                        changes=[{"ticker": "B_US_EQ", "before": 0.5, "after": 0.0, "delta": -0.5, "kind": "removed"},
                                 {"ticker": "C_US_EQ", "before": 0.0, "after": 0.5, "delta": 0.5, "kind": "added"}]))
        s.commit()
    return S


def test_new_mix_against_the_old(tmp_path, monkeypatch):
    monkeypatch.setattr(moves.Prices, "at", lambda self, tk, t, minute, start: PRICES[tk][0 if start else 1])
    S = _db(tmp_path)
    with S() as s:
        v = moves.value_added(s, now=T0 + timedelta(hours=2), fetch=False)["1"]
    assert v["new_mix"] == pytest.approx(0.5 * 0.10 + 0.5 * 0.05)    # A +10%, C +5%
    assert v["old_mix"] == pytest.approx(0.5 * 0.10 + 0.5 * -0.10)   # A +10%, B -10%
    assert v["value_added"] == pytest.approx(0.075) and v["prices"] == "minute" and v["open"]


def test_news_from_around_the_move(tmp_path, monkeypatch):
    items = [{"title": "C wins contract", "url": "https://x/1", "published": "2026-11-12T10:00:00Z"},
             {"title": "Old C story", "url": "https://x/2", "published": "2026-10-01T10:00:00Z"}]
    monkeypatch.setattr(market, "yahoo_symbol", lambda s, tk: tk.split("_")[0])
    monkeypatch.setattr(market, "get", lambda s, kind, sym, allow_fetch=True: {"items": items} if sym == "C" else {"items": []})
    S = _db(tmp_path)
    with S() as s:
        assert moves.collect_news(s, now=T0.replace(tzinfo=timezone.utc) + timedelta(hours=1)) == 1
        from tracker.db import get_meta
        got = get_meta(s, moves.NEWS_KEY)["1"]["items"]
    assert [g["title"] for g in got] == ["C wins contract"] and got[0]["kind"] == "added"
