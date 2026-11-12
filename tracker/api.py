"""Web API + the dashboard. Every route is GET except the prediction game's guess: that's
the only thing visitors can write, into this tracker's own database. Nothing here talks to
Trading 212. Access control sits in front (Cloudflare Access).
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Path as PathParam, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from pydantic import BaseModel, Field

from . import game, service
from .config import ROOT, Settings, get_settings
from .db import PieSnapshot, get_meta, make_session_factory

WEB = ROOT / "web"


class GuessIn(BaseModel):
    player: str = Field(..., max_length=40)
    guess: float  # percent: 1.5 = +1.5%
CACHE_TTL = 15.0


def create_app(cfg: Settings | None = None, Session_: sessionmaker[Session] | None = None,
               allowed_origins: list[str] | None = None) -> FastAPI:
    cfg = cfg or get_settings()
    SessionF = Session_ or make_session_factory(cfg.database_url)
    app = FastAPI(title="Bot Tracker", docs_url=None, redoc_url=None, openapi_url=None)
    if allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_methods=["GET"])

    cache: dict[str, tuple[float, int, object]] = {}

    @app.middleware("http")
    async def always_fresh_pages(request, call_next):
        # the page and its scripts change when the tracker is updated: browsers must re-check them
        resp = await call_next(request)
        if not request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    def latest_id(s: Session) -> int:
        return s.scalar(select(func.max(PieSnapshot.id))) or 0

    def cached(key: str, build):
        with SessionF() as s:
            version = latest_id(s)
            hit = cache.get(key)
            if hit and hit[1] == version and time.monotonic() - hit[0] < CACHE_TTL:
                return hit[2]
            data = build(s)
        cache[key] = (time.monotonic(), version, data)
        return data

    @app.get("/api/summary")
    def summary():
        return cached("summary", lambda s: service.summary(s, cfg))

    @app.get("/api/history")
    def history(range: str = Query("3M", pattern="^(1D|1W|1M|3M|6M|YTD|1Y|ALL)$")):
        return cached(f"history:{range}", lambda s: service.history(s, cfg, range))

    @app.get("/api/holdings")
    def holdings():
        return cached("holdings", lambda s: service.holdings(s, cfg))

    @app.get("/api/activity")
    def activity():
        return cached("activity", lambda s: service.activity(s, cfg))

    @app.get("/api/analysis")
    def analysis_():
        return cached("analysis", lambda s: service.analysis_payload(s, cfg))

    @app.get("/api/stock/{ticker}")
    def stock(ticker: str = PathParam(..., pattern=r"^[A-Za-z0-9_.\-]{1,32}$")):
        data = cached(f"stock:{ticker}", lambda s: service.stock_detail(s, cfg, ticker))
        if data is None:
            cache.pop(f"stock:{ticker}", None)
            raise HTTPException(404, "not a stock the bot has held")
        return data

    @app.get("/api/stock/{ticker}/intraday")
    def stock_intraday(ticker: str = PathParam(..., pattern=r"^[A-Za-z0-9_.\-]{1,32}$")):
        with SessionF() as s:
            data = service.stock_intraday(s, cfg, ticker)
        if data is None:
            raise HTTPException(404, "not a stock the bot has held")
        return data

    @app.get("/api/rebalances")
    def rebalances():
        return cached("rebalances", lambda s: service.rebalances(s, cfg))

    @app.get("/api/stats")
    def stats():
        return cached("stats", lambda s: service.stats_payload(s, cfg))

    @app.get("/api/volsurface/{ticker}")
    def volsurface(ticker: str = PathParam(..., pattern=r"^[A-Za-z0-9_.\-]{1,32}$")):
        with SessionF() as s:
            data = service.vol_surface(s, ticker)
        if data is None:
            raise HTTPException(404, "not a stock the bot has held")
        return data

    @app.get("/api/news")
    def news():
        return cached("news", lambda s: service.news(s, cfg))

    @app.get("/api/correlations")
    def correlations():
        return cached("correlations", lambda s: service.correlations(s, cfg))

    @app.get("/api/quant")
    def quant_pack():
        return cached("quant", lambda s: service.quant_pack(s, cfg))

    @app.get("/api/riskreturn")
    def risk_return():
        return cached("riskreturn", lambda s: service.risk_return(s, cfg))

    @app.get("/api/montecarlo")
    def monte_carlo():
        return cached("montecarlo", lambda s: service.monte_carlo(s, cfg))

    @app.get("/api/recap")
    def recap():
        return cached("recap", lambda s: service.recap(s, cfg))

    # the prediction game: the one thing friends can write (guesses go in this tracker's own
    # database; nothing ever goes to Trading 212)
    @app.get("/api/game")
    def game_state():
        with SessionF() as s:
            return game.state(s, cfg)

    @app.post("/api/game/guess")
    def game_guess(body: GuessIn):
        with SessionF() as s:
            try:
                game.guess(s, cfg, body.player, body.guess)
            except game.GuessError as exc:
                raise HTTPException(400, str(exc))
            return game.state(s, cfg)

    @app.get("/api/health")
    def health():
        with SessionF() as s:
            return service.health(s, cfg)

    @app.get("/api/stream")
    async def stream():
        """Server-sent events: tells the page when a new snapshot has landed."""
        async def events():
            last = None
            for _ in range(720):  # ~1 h per connection; browsers reconnect automatically
                with SessionF() as s:
                    cur = latest_id(s)
                if cur != last:
                    yield f"event: snapshot\ndata: {json.dumps({'id': cur})}\n\n"
                    last = cur
                else:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(5)
        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    if WEB.exists():
        app.mount("/static", StaticFiles(directory=WEB), name="static")

        @app.get("/")
        def index():
            return FileResponse(WEB / "index.html")

        @app.get("/manifest.webmanifest")
        def manifest():
            return FileResponse(WEB / "manifest.webmanifest", media_type="application/manifest+json")

    @app.exception_handler(HTTPException)
    async def _http(_, exc):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _err(_, exc):  # don't leak internals
        return JSONResponse({"error": "internal error"}, status_code=500)

    return app
