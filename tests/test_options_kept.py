from datetime import timedelta

from tracker import market
from tracker.db import StockCache, make_session_factory

GOOD = {"available": True, "spot": 100.0, "moneyness": [1.0], "expiries": [{"days": 30}], "iv": [[0.5]], "as_of": "2026-11-13T10:00:00Z"}


def _session(tmp_path):
    return make_session_factory(f"sqlite:///{tmp_path / 'o.db'}")()


def _age(s, key, minutes):
    row = s.get(StockCache, key)
    row.fetched_at -= timedelta(minutes=minutes)
    s.commit()


def test_last_good_option_prices_stay_when_yahoo_has_none(tmp_path, monkeypatch):
    s = _session(tmp_path)
    monkeypatch.setitem(market.FETCHERS, "vol", lambda sym: GOOD)
    assert market.get(s, "vol", "AAA")["available"]
    _age(s, "vol:AAA", 5 * 60)                                         # older than the 4-hour TTL
    monkeypatch.setitem(market.FETCHERS, "vol", lambda sym: {"available": False})
    d = market.get(s, "vol", "AAA")
    assert d["available"] and d["iv"] == [[0.5]] and d["_kept"]         # the last good surface, marked
    _age(s, "vol:AAA", 11)                                              # kept data is retried after 10 minutes
    assert market.cached(s, "vol:AAA")[1] is False
    fresh = {**GOOD, "iv": [[0.6]], "as_of": "2026-11-13T15:00:00Z"}
    monkeypatch.setitem(market.FETCHERS, "vol", lambda sym: fresh)
    d = market.get(s, "vol", "AAA")
    assert d["iv"] == [[0.6]] and not d.get("_kept")                    # live data replaces it


def test_errors_also_keep_the_last_good_prices(tmp_path, monkeypatch):
    s = _session(tmp_path)
    monkeypatch.setitem(market.FETCHERS, "vol", lambda sym: GOOD)
    market.get(s, "vol", "AAA")
    _age(s, "vol:AAA", 5 * 60)

    def boom(sym):
        raise RuntimeError("Yahoo is down")
    monkeypatch.setitem(market.FETCHERS, "vol", boom)
    assert market.get(s, "vol", "AAA")["_kept"]
    assert market.get(s, "vol", "AAA", allow_fetch=False)["available"]


def test_no_good_prices_ever_is_still_unavailable(tmp_path, monkeypatch):
    s = _session(tmp_path)
    monkeypatch.setitem(market.FETCHERS, "vol", lambda sym: {"available": False})
    assert market.get(s, "vol", "AAA") == {"available": False}
