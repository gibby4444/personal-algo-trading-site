"""Postgres tables from the spec. JSON columns stand in for jsonb/arrays so SQLite works too."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    inspect,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class UTCDateTime(TypeDecorator):
    """Aware UTC timestamps. SQLite drops tzinfo, so it is restored on read."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # type: ignore[no-untyped-def]
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
        if value is None:
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class PortableJSON(TypeDecorator):
    """JSONB on Postgres, JSON everywhere else."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):  # type: ignore[no-untyped-def]
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class Base(DeclarativeBase):
    pass


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    code_ref: Mapped[str] = mapped_column(String(200))
    params: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    asset_class: Mapped[str] = mapped_column(String(16), default="stock")
    mode: Mapped[str] = mapped_column(String(16), default="paper")
    max_position_usd: Mapped[float] = mapped_column(Float, default=5000)
    max_daily_loss_usd: Mapped[float] = mapped_column(Float, default=500)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow
    )


class Screener(Base):
    __tablename__ = "screeners"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    universe: Mapped[str] = mapped_column(String(80))
    filters: Mapped[list] = mapped_column(PortableJSON, default=list)
    regime_gate: Mapped[str | None] = mapped_column(String(32), nullable=True)
    schedule_cron: Mapped[str | None] = mapped_column(String(80), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ScreenerResult(Base):
    __tablename__ = "screener_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    screener_id: Mapped[str] = mapped_column(ForeignKey("screeners.id"))
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    run_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    ticker: Mapped[str] = mapped_column(String(32))
    metrics: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    regime: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Pipeline(Base):
    __tablename__ = "pipelines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    regime_rule: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    screener_id: Mapped[str] = mapped_column(ForeignKey("screeners.id"))
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    strategy_id: Mapped[str | None] = mapped_column(ForeignKey("strategies.id"), nullable=True)
    pipeline_id: Mapped[str | None] = mapped_column(ForeignKey("pipelines.id"), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="paper")
    broker: Mapped[str] = mapped_column(String(32), default="sim")
    broker_order_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float)
    type: Mapped[str] = mapped_column(String(16), default="market")
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop: Mapped[float | None] = mapped_column(Float, nullable=True)
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="new")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    strategy_id: Mapped[str | None] = mapped_column(ForeignKey("strategies.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32))
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float, default=0)
    pnl: Mapped[float] = mapped_column(Float, default=0)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"))
    mode: Mapped[str] = mapped_column(String(16), default="paper")
    broker: Mapped[str] = mapped_column(String(32), default="sim")
    symbol: Mapped[str] = mapped_column(String(32))
    qty: Mapped[float] = mapped_column(Float, default=0)
    avg_price: Mapped[float] = mapped_column(Float, default=0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"))
    params: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    symbols: Mapped[list] = mapped_column(PortableJSON, default=list)
    start_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    timeframe: Mapped[str] = mapped_column(String(16), default="1d")
    capital: Mapped[float] = mapped_column(Float, default=25000)
    cost_bps: Mapped[float] = mapped_column(Float, default=5)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class BacktestMetric(Base):
    __tablename__ = "backtest_metrics"

    run_id: Mapped[str] = mapped_column(ForeignKey("backtest_runs.id"), primary_key=True)
    sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    sortino: Mapped[float | None] = mapped_column(Float, nullable=True)
    cagr: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_dd: Mapped[float | None] = mapped_column(Float, nullable=True)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    trades: Mapped[int] = mapped_column(Integer, default=0)
    profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    equity_curve_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    equity_curve: Mapped[list] = mapped_column(PortableJSON, default=list)
    drawdown: Mapped[list] = mapped_column(PortableJSON, default=list)
    checks: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    warnings: Mapped[list] = mapped_column(PortableJSON, default=list)


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("backtest_runs.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    entry_ts: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    exit_ts: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    qty: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)


class AuditLog(Base):
    """Append-only. There is no update or delete path."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(80), default="owner")
    action: Mapped[str] = mapped_column(String(80))
    entity: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    before: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)


class RegimeSnapshot(Base):
    __tablename__ = "regime_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    regime: Mapped[str] = mapped_column(String(32))
    vix: Mapped[float | None] = mapped_column(Float, nullable=True)
    spy_vs_200dma: Mapped[float | None] = mapped_column(Float, nullable=True)
    qqq_vs_200dma: Mapped[float | None] = mapped_column(Float, nullable=True)
    sentiment: Mapped[float | None] = mapped_column(Float, nullable=True)
    breadth: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail: Mapped[dict] = mapped_column(PortableJSON, default=dict)


class RiskState(Base):
    __tablename__ = "risk_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    killed: Mapped[bool] = mapped_column(Boolean, default=False)
    killed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    max_daily_loss_usd: Mapped[float] = mapped_column(Float, default=500)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


def column_type_ddl(column, dialect) -> str:  # type: ignore[no-untyped-def]
    return column.type.compile(dialect=dialect)


def missing_column_names(engine) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    """Used by startup migrations. Returns (table, column) pairs that create_all missed."""
    insp = inspect(engine)
    missing: list[tuple[str, str]] = []
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {col["name"] for col in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in existing:
                missing.append((table.name, col.name))
    return missing
