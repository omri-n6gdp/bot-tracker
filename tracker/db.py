"""Database models. SQLite by default; set DATABASE_URL for Postgres.

Raw tables record what Trading 212 reported and are never edited.
`daily_values` is computed and can be wiped and rebuilt at any time.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String,
    UniqueConstraint, create_engine, event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # stored as naive UTC


class Base(DeclarativeBase):
    pass


class PieSnapshot(Base):
    __tablename__ = "pie_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True, default=utcnow)
    value: Mapped[float] = mapped_column(Float)            # holdings + cash, GBP
    holdings_value: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float, default=0.0)
    invested: Mapped[float | None] = mapped_column(Float, nullable=True)
    result: Mapped[float | None] = mapped_column(Float, nullable=True)  # unrealised P/L
    is_daily_close: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    source: Mapped[str] = mapped_column(String(16), default="pie")  # pie | fallback | demo
    holdings: Mapped[list["HoldingSnapshot"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )


class HoldingSnapshot(Base):
    __tablename__ = "holding_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("pie_snapshots.id"), index=True)
    ticker: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    invested: Mapped[float | None] = mapped_column(Float, nullable=True)
    result: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    snapshot: Mapped[PieSnapshot] = relationship(back_populates="holdings")


class Rebalance(Base):
    """A change in the pie's target weights, i.e. the bot updating the pie."""
    __tablename__ = "rebalances"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    source: Mapped[str] = mapped_column(String(16), default="live")  # live | backfill
    weights_before: Mapped[dict] = mapped_column(JSON)
    weights_after: Mapped[dict] = mapped_column(JSON)
    changes: Mapped[list] = mapped_column(JSON)  # [{ticker, before, after, delta, kind}]


class TargetWeights(Base):
    """Latest known target weights (baseline for rebalance detection)."""
    __tablename__ = "target_weights"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime)
    weights: Mapped[dict] = mapped_column(JSON)


class BotTrade(Base):
    """A filled order that belongs to the bot pie (from order history). Drives money in/out."""
    __tablename__ = "bot_trades"
    fill_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(Integer, index=True)
    batch: Mapped[str] = mapped_column(String(40))
    filled_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ticker: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))
    quantity: Mapped[float] = mapped_column(Float)   # signed
    gbp: Mapped[float] = mapped_column(Float)        # signed: + money into the pie's holdings


class Dividend(Base):
    __tablename__ = "dividends"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(64), unique=True)
    paid_on: Mapped[datetime] = mapped_column(DateTime, index=True)
    ticker: Mapped[str] = mapped_column(String(32))
    amount: Mapped[float] = mapped_column(Float)


class Contribution(Base):
    """Money you put into (+) or took out of (-) the bot pie. Synced from contributions.csv."""
    __tablename__ = "contributions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(String(200), default="")


class InstrumentName(Base):
    __tablename__ = "instrument_names"
    ticker: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)


class BenchmarkPrice(Base):
    __tablename__ = "benchmark_prices"
    __table_args__ = (UniqueConstraint("ticker", "date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(32))
    date: Mapped[date] = mapped_column(Date, index=True)
    close: Mapped[float] = mapped_column(Float)


class DailyValue(Base):
    """Computed: one row per day with a close. Rebuilt by engine.rebuild()."""
    __tablename__ = "daily_values"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[float] = mapped_column(Float)
    units: Mapped[float] = mapped_column(Float)
    unit_price: Mapped[float] = mapped_column(Float)
    flow: Mapped[float] = mapped_column(Float, default=0.0)


class StockCache(Base):
    """Cached Yahoo data per instrument: profile, news, price history."""
    __tablename__ = "stock_cache"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)   # e.g. "profile:APP"
    data: Mapped[dict] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)


class Prediction(Base):
    """A player's guess at the pie's return for one week (the prediction game)."""
    __tablename__ = "predictions"
    __table_args__ = (UniqueConstraint("week", "player"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    week: Mapped[date] = mapped_column(Date, index=True)      # the Monday
    player: Mapped[str] = mapped_column(String(40))
    guess: Mapped[float] = mapped_column(Float)               # a fraction: 0.015 = +1.5%
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Meta(Base):
    """Key/value store: pie name, poll health, last error per source."""
    __tablename__ = "meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


def make_engine(url: str):
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(url, future=True)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")   # poller writes while API reads
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    Base.metadata.create_all(eng)
    _migrate(eng)
    return eng


def _migrate(eng) -> None:
    """Small in-place upgrades for databases made by earlier versions."""
    from sqlalchemy import inspect, text
    cols = {c["name"] for c in inspect(eng).get_columns("rebalances")}
    with eng.begin() as conn:
        if "source" not in cols:
            conn.execute(text("ALTER TABLE rebalances ADD COLUMN source VARCHAR(16) DEFAULT 'live'"))
        # performance is measured on holdings only (pie cash is shown separately)
        conn.execute(text("UPDATE pie_snapshots SET value = holdings_value WHERE value != holdings_value"))


def make_session_factory(url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=make_engine(url), expire_on_commit=False, future=True)


def get_meta(s: Session, key: str, default=None):
    row = s.get(Meta, key)
    return row.value if row else default


def set_meta(s: Session, key: str, value) -> None:
    row = s.get(Meta, key)
    if row:
        row.value = value
    else:
        s.add(Meta(key=key, value=value))
