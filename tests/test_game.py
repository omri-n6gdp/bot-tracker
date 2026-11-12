"""The weekly prediction game."""
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from tracker import game, service
from tracker.api import create_app
from tracker.config import Settings
from tracker.db import PieSnapshot, make_session_factory

PLAYERS = ("Ann", "Bo", "Cy")


def _pie(tmp_path, closes):
    """closes: {date: holdings value} at 21:30 each day; no cash, no trades."""
    url = f"sqlite:///{tmp_path / 'g.db'}"
    S = make_session_factory(url)
    with S() as s:
        for d, v in closes.items():
            s.add(PieSnapshot(ts=datetime(d.year, d.month, d.day, 21, 30), value=v, holdings_value=v, cash=0.0,
                              is_daily_close=True))
        s.commit()
        service.rebuild(s, Settings(database_url=url))
        s.commit()
    return S, Settings(database_url=url, players=PLAYERS, stale_after_s=10**9)


def test_rounds_open_until_mondays_new_york_open():
    assert game.open_week(datetime(2026, 11, 12, 15)) == date(2026, 11, 16)       # Thursday: next week
    assert game.open_week(datetime(2026, 11, 16, 14, 29)) == date(2026, 11, 16)   # Monday before 14:30 UK
    assert game.open_week(datetime(2026, 11, 16, 14, 31)) == date(2026, 11, 23)   # after the open


def test_guesses_are_hidden_until_close_then_scored(tmp_path):
    S, cfg = _pie(tmp_path, {date(2026, 11, 13): 1000, date(2026, 11, 16): 1010, date(2026, 11, 20): 1020})
    with S() as s:
        game.guess(s, cfg, "Ann", 1.0, now=datetime(2026, 11, 14, 12))
        game.guess(s, cfg, "Bo", 3.0, now=datetime(2026, 11, 14, 12))
        game.guess(s, cfg, "Bo", 2.5, now=datetime(2026, 11, 15, 12))   # changed their mind
        st = game.state(s, cfg, now=datetime(2026, 11, 15, 13))
        assert st["open"]["week"] == "2026-11-16" and st["open"]["guessed"] == ["Ann", "Bo"]
        assert set(st["open"]) == {"week", "friday", "deadline", "guessed"}  # who, never how much, while open
        live = game.state(s, cfg, now=datetime(2026, 11, 18, 15))["current"]
        assert live["week"] == "2026-11-16" and not live["finished"]
        done = game.state(s, cfg, now=datetime(2026, 11, 21, 10))
    r = done["past"][0]
    assert r["return"] == pytest.approx(0.02)                            # 1000 (Fri 13th) -> 1020 (Fri 20th)
    assert [g["player"] for g in r["guesses"] if g["winner"]] == ["Bo"]   # 2.5% is closer than 1%
    top = done["leaderboard"][0]
    assert top["player"] == "Bo" and top["wins"] == 1 and top["avg_error"] == pytest.approx(0.005)


def test_bad_guesses_are_refused(tmp_path):
    S, cfg = _pie(tmp_path, {date(2026, 11, 13): 1000})
    with S() as s:
        with pytest.raises(game.GuessError):
            game.guess(s, cfg, "Mallory", 1.0)
        with pytest.raises(game.GuessError):
            game.guess(s, cfg, "Ann", 80.0)


def test_guess_endpoint(tmp_path):
    S, cfg = _pie(tmp_path, {date(2026, 11, 13): 1000})
    c = TestClient(create_app(cfg, S))
    assert c.post("/api/game/guess", json={"player": "Cy", "guess": -0.4}).status_code == 200
    assert "Cy" in c.get("/api/game").json()["open"]["guessed"]
    assert c.post("/api/game/guess", json={"player": "Nobody", "guess": 1}).status_code == 400
