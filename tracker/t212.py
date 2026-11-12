"""Read-only Trading 212 client.

Only GET endpoints exist here on purpose: the tracker can never trade.
Field names follow the official OpenAPI spec (docs.trading212.com/_bundle/api.json).
Every Trading 212 call in the project goes through this module, so an API change
means one fix in one place.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterator

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .config import Credentials

log = logging.getLogger(__name__)

API = "/api/v0/equity"

# Minimum seconds between calls to each endpoint (published limits + margin).
# The bot shares these limits per account, so stay well inside them.
MIN_INTERVAL = {
    "summary": 6.0,      # 1 / 5s
    "positions": 2.0,    # 1 / 1s
    "pies": 35.0,        # 1 / 30s
    "pie": 6.0,          # 1 / 5s
    "dividends": 4.0,    # 20 / min
    "orders": 4.0,       # 20 / min
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class InvestmentResult(_Model):
    priceAvgInvestedValue: float | None = None
    priceAvgResult: float | None = None
    priceAvgResultCoef: float | None = None
    priceAvgValue: float | None = None


class PieInstrument(_Model):
    ticker: str
    ownedQuantity: float | None = None
    currentShare: float | None = None
    expectedShare: float | None = None
    result: InvestmentResult = Field(default_factory=InvestmentResult)
    issues: list[Any] | None = None


class PieSettings(_Model):
    id: int | None = None
    name: str | None = None
    creationDate: str | float | None = None  # live API returns a Unix timestamp
    endDate: str | float | None = None
    goal: float | None = None
    initialInvestment: float | None = None


class PieDetail(_Model):
    instruments: list[PieInstrument] = Field(default_factory=list)
    settings: PieSettings = Field(default_factory=PieSettings)


class DividendDetails(_Model):
    gained: float | None = None
    inCash: float | None = None
    reinvested: float | None = None


class PieSummary(_Model):
    id: int
    cash: float | None = None
    dividendDetails: DividendDetails | None = None
    progress: float | None = None
    result: InvestmentResult = Field(default_factory=InvestmentResult)
    status: str | None = None


class Instrument(_Model):
    ticker: str | None = None
    name: str | None = None
    currency: str | None = None
    isin: str | None = None


class WalletImpact(_Model):
    currency: str | None = None
    currentValue: float | None = None
    totalCost: float | None = None
    unrealizedProfitLoss: float | None = None


class Position(_Model):
    instrument: Instrument = Field(default_factory=Instrument)
    quantity: float | None = None
    quantityInPies: float | None = None
    currentPrice: float | None = None
    averagePricePaid: float | None = None
    walletImpact: WalletImpact = Field(default_factory=WalletImpact)

    @property
    def ticker(self) -> str | None:
        return self.instrument.ticker

    @property
    def price_in_account_ccy(self) -> float | None:
        """Price per share in the account currency (GBP), FX included."""
        if self.walletImpact.currentValue is None or not self.quantity:
            return None
        return self.walletImpact.currentValue / self.quantity


class Dividend(_Model):
    reference: str | None = None
    ticker: str | None = None
    amount: float | None = None
    paidOn: str | float | None = None
    quantity: float | None = None
    type: str | None = None


class Fill(_Model):
    id: int | None = None
    filledAt: str | None = None
    price: float | None = None
    quantity: float | None = None
    type: str | None = None
    walletImpact: dict | None = None


class OrderInfo(_Model):
    id: int | None = None
    ticker: str | None = None
    side: str | None = None
    status: str | None = None
    createdAt: str | None = None
    initiatedFrom: str | None = None
    currency: str | None = None
    filledQuantity: float | None = None
    filledValue: float | None = None
    instrument: Instrument = Field(default_factory=Instrument)


class HistoricalOrder(_Model):
    order: OrderInfo = Field(default_factory=OrderInfo)
    fill: Fill | None = None


class T212Error(RuntimeError):
    def __init__(self, status: int, path: str, detail: str = ""):
        super().__init__(f"Trading 212 {status} on {path} {detail}".strip())
        self.status = status
        self.path = path


class T212Client:
    def __init__(
        self,
        creds: Credentials,
        base_url: str = "https://live.trading212.com",
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 4,
        sleep=time.sleep,
        throttle: bool = True,
    ):
        self._http = httpx.Client(
            base_url=base_url,
            auth=(creds.key, creds.secret),
            timeout=20.0,
            transport=transport,
            headers={"User-Agent": "bot-tracker/0.1 (read-only)"},
        )
        self._max_retries = max_retries
        self._sleep = sleep
        self._throttle = throttle
        self._last_call: dict[str, float] = {}

    def close(self) -> None:
        self._http.close()

    # -- plumbing -------------------------------------------------------
    def _wait_turn(self, bucket: str) -> None:
        if not self._throttle:
            return
        gap = MIN_INTERVAL.get(bucket, 1.0)
        last = self._last_call.get(bucket)
        if last is not None:
            wait = gap - (time.monotonic() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_call[bucket] = time.monotonic()

    def _get(self, path: str, bucket: str, params: dict | None = None) -> Any:
        for attempt in range(self._max_retries + 1):
            self._wait_turn(bucket)
            try:
                r = self._http.get(path, params=params)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise T212Error(0, path, f"network error: {exc.__class__.__name__}") from exc
                self._sleep(min(2 ** attempt, 30))
                continue
            remaining = r.headers.get("x-ratelimit-remaining")
            if remaining is not None:
                log.debug("%s rate-limit remaining=%s", path, remaining)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504) and attempt < self._max_retries:
                self._sleep(self._retry_after(r, attempt))
                continue
            raise T212Error(r.status_code, path)
        raise T212Error(0, path, "retries exhausted")  # pragma: no cover

    @staticmethod
    def _retry_after(r: httpx.Response, attempt: int) -> float:
        for h in ("retry-after",):
            if r.headers.get(h):
                try:
                    return min(float(r.headers[h]), 120.0)
                except ValueError:
                    pass
        reset = r.headers.get("x-ratelimit-reset")
        if reset:
            try:
                return max(0.0, min(float(reset) - time.time(), 120.0)) + 0.5
            except ValueError:
                pass
        return min(2 ** attempt * 2, 60)

    # -- endpoints ------------------------------------------------------
    def account_summary(self) -> dict:
        return self._get(f"{API}/account/summary", "summary")

    def positions(self) -> list[Position]:
        return [Position.model_validate(p) for p in self._get(f"{API}/positions", "positions")]

    def pies(self) -> list[PieSummary]:
        return [PieSummary.model_validate(p) for p in self._get(f"{API}/pies", "pies")]

    def pie(self, pie_id: int) -> PieDetail:
        return PieDetail.model_validate(self._get(f"{API}/pies/{pie_id}", "pie"))

    def orders(self, limit: int = 50, max_pages: int = 400, ticker: str | None = None) -> Iterator[HistoricalOrder]:
        """Filled and unfilled historical orders, newest first; follows nextPagePath."""
        path: str | None = f"{API}/history/orders"
        params: dict | None = {"limit": limit, **({"ticker": ticker} if ticker else {})}
        for _ in range(max_pages):
            if not path:
                return
            data = self._get(path, "orders", params)
            for item in data.get("items", []):
                yield HistoricalOrder.model_validate(item)
            path, params = data.get("nextPagePath"), None

    def dividends(self, limit: int = 50, max_pages: int = 20) -> Iterator[Dividend]:
        """Newest first; follows nextPagePath."""
        path: str | None = f"{API}/history/dividends"
        params: dict | None = {"limit": limit}
        for _ in range(max_pages):
            if not path:
                return
            data = self._get(path, "dividends", params)
            for item in data.get("items", []):
                yield Dividend.model_validate(item)
            path, params = data.get("nextPagePath"), None
