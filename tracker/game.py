"""The weekly prediction game: friends guess the pie's return for the week.

A round is a trading week, Monday to Friday. Guesses close at Monday's open in New York
(09:30, 14:30 UK most of the year) and stay hidden until then, so nobody can copy. The week's
return is the pie's unit price (money in and out taken out) from the last daily close before
Monday to Friday's close. Closest guess wins; ties share the win.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .db import Prediction, utcnow

NY = ZoneInfo("America/New_York")
MAX_GUESS = 0.5  # ±50% in a week is plenty


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def deadline(week: date) -> datetime:
    """Guessing closes at Monday's New York open (naive UTC, like the database)."""
    return datetime.combine(week, time(9, 30), tzinfo=NY).astimezone(timezone.utc).replace(tzinfo=None)


def open_week(now: datetime) -> date:
    """The week currently taking guesses: this week until Monday's open, then next week."""
    this = monday(now.date())
    return this if now < deadline(this) else this + timedelta(days=7)


def _week_return(rows, week: date, live_price: float | None, now_local: datetime,
                 close_time: time) -> tuple[float | None, bool]:
    """(return so far or final, finished?) for a week, from the daily unit prices."""
    friday = week + timedelta(days=4)
    before = [r for r in rows if r.date < week]
    inside = [r for r in rows if week <= r.date <= friday]
    base = before[-1] if before else (inside[0] if inside else None)
    today = now_local.date()
    finished = today > friday or (today == friday and now_local.time() >= close_time)
    if base is None:
        return None, finished
    end = (inside[-1].unit_price if inside else None) if finished else (live_price or (inside[-1].unit_price if inside else None))
    return (end / base.unit_price - 1 if end else None), finished


def _scored(guesses: list[Prediction], actual: float | None) -> list[dict]:
    out = [{"player": g.player, "guess": g.guess,
            "error": abs(g.guess - actual) if actual is not None else None} for g in guesses]
    if actual is not None and out:
        best = min(x["error"] for x in out)
        for x in out:
            x["winner"] = abs(x["error"] - best) < 1e-12
    return sorted(out, key=lambda x: (x["error"] is None, x["error"] or 0, x["player"]))


def state(s: Session, cfg: Settings, now: datetime | None = None) -> dict:
    """Everything the page shows. Guesses for a week still open are hidden (names only)."""
    from . import service
    now = now or utcnow()
    tz = ZoneInfo(cfg.timezone)
    rows = service._daily(s)
    snap = service.latest_snapshot(s)
    live = service._live(s, rows, snap, tz) if rows else None
    now_local = now.replace(tzinfo=timezone.utc).astimezone(tz)
    close_time = time(*map(int, cfg.daily_close_time.split(":")))
    week_open = open_week(now)
    by_week: dict[date, list[Prediction]] = {}
    for p in s.scalars(select(Prediction).order_by(Prediction.week)):
        by_week.setdefault(p.week, []).append(p)

    rounds = []
    for week in sorted(w for w in by_week if w < week_open):
        actual, finished = _week_return(rows, week, live, now_local, close_time)
        rounds.append({"week": week.isoformat(), "friday": (week + timedelta(days=4)).isoformat(),
                       "return": actual, "finished": finished, "guesses": _scored(by_week[week], actual)})
    current = next((r for r in reversed(rounds) if not r["finished"]), None)
    past = [r for r in rounds if r["finished"]]

    board = {p: {"player": p, "played": 0, "wins": 0, "errors": []} for p in cfg.players}
    for r in past:
        for g in r["guesses"]:
            b = board.setdefault(g["player"], {"player": g["player"], "played": 0, "wins": 0, "errors": []})
            b["played"] += 1
            b["wins"] += 1 if g.get("winner") else 0
            if g["error"] is not None:
                b["errors"].append(g["error"])
    leaderboard = sorted(({"player": b["player"], "played": b["played"], "wins": b["wins"],
                           "avg_error": (sum(b["errors"]) / len(b["errors"])) if b["errors"] else None}
                          for b in board.values()),
                         key=lambda b: (-b["wins"], b["avg_error"] if b["avg_error"] is not None else 9, b["player"]))
    return {
        "players": list(cfg.players),
        "open": {"week": week_open.isoformat(), "friday": (week_open + timedelta(days=4)).isoformat(),
                 "deadline": deadline(week_open).replace(tzinfo=timezone.utc).isoformat(),
                 "guessed": sorted(p.player for p in by_week.get(week_open, []))},
        "current": current,
        "past": list(reversed(past))[:8],
        "leaderboard": leaderboard,
        "as_of": snap.ts.replace(tzinfo=timezone.utc).isoformat() if snap else None,
    }


class GuessError(ValueError):
    pass


def guess(s: Session, cfg: Settings, player: str, pct: float, now: datetime | None = None) -> date:
    """Save (or change) a player's guess, in percent (1.5 = +1.5%), for the open week."""
    now = now or utcnow()
    if player not in cfg.players:
        raise GuessError("pick your name from the list")
    if pct != pct or abs(pct / 100) > MAX_GUESS:
        raise GuessError("guess between -50% and +50%")
    week = open_week(now)
    row = s.scalars(select(Prediction).where(Prediction.week == week, Prediction.player == player)).first()
    if row:
        row.guess, row.updated_at = pct / 100, now
    else:
        s.add(Prediction(week=week, player=player, guess=pct / 100, updated_at=now))
    s.commit()
    return week
