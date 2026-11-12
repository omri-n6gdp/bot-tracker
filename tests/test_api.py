import json
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from tracker.api import create_app
from tracker.config import Settings
from tracker.db import InstrumentName, make_session_factory
from tracker.demo import TICKERS, seed


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    url = f"sqlite:///{tmp_path_factory.mktemp('db') / 'demo.db'}"
    seed(days=150, url=url, today=date(2026, 11, 11))
    cfg = Settings(database_url=url, stale_after_s=10**9)
    S = make_session_factory(url)
    with S() as s:  # a name for something outside the pie must never be served
        s.add(InstrumentName(ticker="SECRET_US_EQ", name="My Other Holding"))
        s.commit()
    return TestClient(create_app(cfg, S))


ENDPOINTS = ["/api/summary", "/api/history?range=1D", "/api/history?range=3M", "/api/history?range=ALL",
             "/api/holdings", "/api/rebalances", "/api/stats", "/api/health", "/api/activity", "/api/analysis",
             "/api/stock/NVDA_US_EQ", "/api/news"]


def test_all_endpoints_ok(client):
    for ep in ENDPOINTS:
        assert client.get(ep).status_code == 200, ep


def test_summary_shape(client):
    s = client.get("/api/summary").json()
    assert s["ready"] and s["value"] > 0
    assert set(s["returns"]) >= {"1D", "1W", "1M", "3M", "YTD", "ALL"}
    # net deposits = external money only (the deposits ledger), not trade flows
    assert s["deposits_source"] == "ledger"
    assert s["net_deposits"] == pytest.approx(1750.0)
    assert s["profit"] == pytest.approx(s["value"] - 1750.0)


def test_unit_price_matches_value_over_units(client):
    pts = client.get("/api/history?range=ALL").json()["points"]
    assert pts[0]["pie"] == 0.0
    assert pts[0]["benchmark"] == pytest.approx(0.0)


def test_no_leak_of_non_pie_holdings(client):
    allowed = set(TICKERS)
    body = "".join(client.get(ep).text for ep in ENDPOINTS)
    assert "SECRET" not in body and "My Other Holding" not in body
    tickers = {h["ticker"] for h in client.get("/api/holdings").json()["holdings"]}
    assert tickers <= allowed


def test_everything_is_read_only(client):
    for ep in ENDPOINTS:
        path = ep.split("?")[0]
        assert client.post(path).status_code == 405
        assert client.delete(path).status_code == 405


def test_bad_range_rejected(client):
    assert client.get("/api/history?range=10Y").status_code == 422


def test_no_api_docs_exposed(client):
    for p in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(p).status_code == 404


def test_history_has_benchmark_switch_data(client):
    h = client.get("/api/history?range=ALL").json()
    keys = [b["key"] for b in h["benchmarks"]]
    assert keys and h["has_deposits"]
    p0, pn = h["points"][0], h["points"][-1]
    assert p0["bench"][keys[0]] == pytest.approx(0.0)
    assert p0["bench_value"][keys[0]] == pytest.approx(p0["value"])  # same money at the start
    assert pn["deposits"] == pytest.approx(1750.0)


def test_stock_page_only_for_pie_stocks(client):
    ok = client.get("/api/stock/NVDA_US_EQ").json()
    assert ok["ticker"] == "NVDA_US_EQ" and ok["trades"] is not None
    assert client.get("/api/stock/SECRET_US_EQ").status_code == 404
    assert client.get("/api/stock/..%2F..%2Fetc").status_code in (404, 422)


def test_analysis_payload(client):
    a = client.get("/api/analysis").json()
    assert a["ready"] and a["verdict"]
    assert {"returns", "risk", "ratios", "significance", "vs", "moves", "histogram"} <= set(a)


def test_page_files_are_never_served_stale(client):
    for path in ("/", "/static/app.js", "/static/style.css"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"
    assert "no-cache" not in client.get("/api/summary").headers.get("cache-control", "")


# ---- news, volatility surface, correlations, analysts ----

from tracker.db import StockCache, set_meta  # noqa: E402


def test_news_merges_headlines_across_holdings(client_fresh):
    c, S = client_fresh
    held = [h["ticker"] for h in c.get("/api/holdings").json()["holdings"] if h["value"] > 0][:2]
    story = {"title": "Chips rally", "url": "https://example.com/a?x=1", "publisher": "Wire", "published": "2026-11-11T10:00:00Z"}
    now = datetime(2026, 11, 11, 12, 0)
    with S() as s:
        set_meta(s, "yahoo_map", {held[0]: "AAA", held[1]: "BBB"})
        s.add(StockCache(key="news:AAA", data={"items": [story, {**story, "title": "Older", "url": "https://example.com/b", "published": "2026-11-10T09:00:00Z"}]}, fetched_at=now))
        s.add(StockCache(key="news:BBB", data={"items": [{**story, "url": "https://example.com/a?x=2"}]}, fetched_at=now))
        s.commit()
    items = c.get("/api/news").json()["items"]
    assert [i["title"] for i in items] == ["Chips rally", "Older"]
    assert len(items[0]["symbols"]) == 2


@pytest.fixture()
def client_fresh(tmp_path):
    url = f"sqlite:///{tmp_path / 'demo.db'}"
    seed(days=60, url=url, today=date(2026, 11, 11))
    S = make_session_factory(url)
    return TestClient(create_app(Settings(database_url=url, stale_after_s=10**9), S)), S


def test_volsurface_only_for_pie_stocks(client, monkeypatch):
    from tracker import market
    monkeypatch.setattr(market, "yahoo_symbol", lambda s, t: "FAKE")
    monkeypatch.setitem(market.FETCHERS, "vol", lambda sym: {"available": True, "spot": 10.0, "iv": [[0.5]]})
    assert client.get("/api/volsurface/SECRET_US_EQ").status_code == 404
    held = client.get("/api/holdings").json()["holdings"][0]["ticker"]
    r = client.get(f"/api/volsurface/{held}")
    assert r.status_code == 200 and r.json()["available"] is True and r.json()["spot"] == 10.0


def test_correlations_are_symmetric_with_ones_on_the_diagonal(client):
    d = client.get("/api/correlations").json()
    n = len(d["symbols"])
    assert n >= 2 and len(d["matrix"]) == n
    for i in range(n):
        assert d["matrix"][i][i] == 1.0
        for j in range(n):
            a, b = d["matrix"][i][j], d["matrix"][j][i]
            assert a == b and (a is None or -1.0 <= a <= 1.0)


def test_analyst_rating_comes_from_the_score_when_yahoo_gives_none():
    from tracker.service import _analyst
    a = _analyst({"recommendationKey": "none", "recommendationMean": 1.4, "targetMeanPrice": 20.0, "targetLowPrice": 15.0,
                  "targetHighPrice": 30.0, "currentPrice": 10.0, "numberOfAnalystOpinions": 9, "currency": "USD"})
    assert a["rating"] == "Strong buy" and a["upside"] == pytest.approx(1.0) and a["low_pct"] == pytest.approx(0.5)
    b = _analyst({"recommendationKey": "none", "targetMeanPrice": 20.0, "currentPrice": 10.0, "numberOfAnalystOpinions": 2})
    assert b["rating"] is None and b["thin"] is True
    assert _analyst({"currentPrice": 10.0}) is None
    assert _analyst({"recommendationKey": "underperform", "recommendationMean": 3.8, "currentPrice": 5})["rating"] == "Underperform"


def test_today_chart_skips_readings_before_the_pie_had_money(tmp_path):
    """Overnight the new pie read £0; the 1D line is drawn relative to its first point,
    so a £0 first point made the whole line flat at 0%."""
    from tracker import service
    from tracker.db import PieSnapshot
    url = f"sqlite:///{tmp_path / 't.db'}"
    S = make_session_factory(url)
    with S() as s:
        for hh, v in [(1, 0.0), (2, 0.0), (15, 900.0), (16, 910.0)]:
            s.add(PieSnapshot(ts=datetime(2026, 11, 12, hh), value=v, holdings_value=v))
        s.commit()
        pts = service.history(s, Settings(database_url=url), "1D")["points"]
    assert [p["value"] for p in pts if not p.get("held")] == [900.0, 910.0]
