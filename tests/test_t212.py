import base64

import httpx
import pytest

from tracker.config import Credentials, load_credentials
from tracker.t212 import T212Client, T212Error

CREDS = Credentials("KEY123", "SECRET456")


def make(handler, **kw):
    return T212Client(CREDS, transport=httpx.MockTransport(handler), sleep=lambda s: None, throttle=False, **kw)


def test_basic_auth_header_and_get_only():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"currency": "GBP"})

    make(handler).account_summary()
    req = seen[0]
    assert req.method == "GET"
    assert req.headers["authorization"] == "Basic " + base64.b64encode(b"KEY123:SECRET456").decode()


def test_client_has_no_write_methods():
    public = {m for m in dir(T212Client) if not m.startswith("_")}
    assert public == {"account_summary", "positions", "pies", "pie", "dividends", "orders", "close"}


def test_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"retry-after": "1"})
        return httpx.Response(200, json=[])

    assert make(handler).pies() == []
    assert calls["n"] == 3


def test_401_raises_without_retry_and_without_secret_in_message():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(401)

    with pytest.raises(T212Error) as e:
        make(handler).pie(1)
    assert calls["n"] == 1 and e.value.status == 401
    assert "SECRET" not in str(e.value) and "KEY123" not in str(e.value)


def test_parses_pie_detail_with_spec_fields():
    payload = {
        "instruments": [{"ticker": "AAPL_US_EQ", "ownedQuantity": 1.5, "currentShare": 0.6, "expectedShare": 0.5,
                         "result": {"priceAvgValue": 300.0, "priceAvgInvestedValue": 250.0, "priceAvgResult": 50.0},
                         "issues": []}],
        "settings": {"id": 7, "name": "Bot", "unknownNewField": True},
    }
    d = make(lambda r: httpx.Response(200, json=payload)).pie(7)
    assert d.settings.name == "Bot"
    assert d.instruments[0].expectedShare == 0.5
    assert d.instruments[0].result.priceAvgValue == 300.0


def test_dividends_follow_next_page_path():
    pages = {
        "/api/v0/equity/history/dividends": {"items": [{"reference": "a"}], "nextPagePath": "/api/v0/equity/history/dividends?limit=50&cursor=9"},
    }

    def handler(req):
        if "cursor=9" in str(req.url):
            return httpx.Response(200, json={"items": [{"reference": "b"}], "nextPagePath": None})
        return httpx.Response(200, json=pages[req.url.path])

    refs = [d.reference for d in make(handler).dividends()]
    assert refs == ["a", "b"]


def test_position_price_in_gbp_uses_wallet_impact():
    from tracker.t212 import Position
    p = Position.model_validate({"instrument": {"ticker": "X_US_EQ"}, "quantity": 2, "currentPrice": 100,
                                 "walletImpact": {"currentValue": 160.0}})
    assert p.price_in_account_ccy == 80.0


def test_load_credentials_from_keys_file(tmp_path, monkeypatch):
    monkeypatch.delenv("T212_API_KEY", raising=False)
    monkeypatch.delenv("T212_API_SECRET", raising=False)
    f = tmp_path / "API Keys"
    f.write_text("# Trading 212 — id + secret\nabc\n\ndef\n", encoding="utf-8")
    c = load_credentials(f)
    assert (c.key, c.secret) == ("abc", "def")
    assert "abc" not in repr(c) and "def" not in repr(c)


@pytest.mark.parametrize("text", [
    "# Trading 212 — id + secret, Basic auth\nTRADING212_API_KEY=abc_123\nTRADING212_API_SECRET=def_456\n",
    "TRADING212_API_SECRET='def_456'\nTRADING212_API_KEY=\"abc_123\"\n",
    "export T212_API_KEY = abc_123\nexport T212_API_SECRET = def_456\n",
    "﻿abc_123\ndef_456\n",
])
def test_load_credentials_formats(tmp_path, monkeypatch, text):
    monkeypatch.delenv("T212_API_KEY", raising=False)
    monkeypatch.delenv("T212_API_SECRET", raising=False)
    f = tmp_path / "API Keys"
    f.write_text(text, encoding="utf-8")
    c = load_credentials(f)
    assert (c.key, c.secret) == ("abc_123", "def_456")


def test_bare_base64_secret_with_padding_is_not_a_label(tmp_path, monkeypatch):
    monkeypatch.delenv("T212_API_KEY", raising=False)
    monkeypatch.delenv("T212_API_SECRET", raising=False)
    f = tmp_path / "API Keys"
    f.write_text("abcDEF123\nZm9vYmFy==\n", encoding="utf-8")
    assert load_credentials(f).secret == "Zm9vYmFy=="


def test_pie_settings_accept_epoch_creation_date():
    from tracker.t212 import PieDetail
    d = PieDetail.model_validate({"instruments": [], "settings": {"name": "X", "creationDate": 1785063279.0, "endDate": None}})
    assert d.settings.name == "X"
