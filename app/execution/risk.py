"""Position, exposure, daily-loss, and rate limits. The kill switch stops new orders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.audit import audit
from app.config import get_settings
from app.logging_config import alert
from app.models import Order, Position, RiskState, Strategy, Trade

ET = ZoneInfo("America/New_York")


@dataclass
class Decision:
    ok: bool
    reason: str = ""
    demote: bool = False


def evaluate(session: Session, strategy: Strategy, symbol: str, qty: float, price: float) -> Decision:
    state = session.get(RiskState, 1)
    if state and state.killed:
        return Decision(False, "Kill switch is on. New orders are blocked.")
    if not strategy.enabled:
        return Decision(False, "Strategy is paused.")
    if strategy.mode == "backtest":
        return Decision(False, "Strategy is in backtest mode, so it cannot send orders.")
    if qty <= 0 or price <= 0:
        return Decision(False, "Quantity and price must be positive.")
    notional = qty * price
    if notional > float(strategy.max_position_usd) + 1e-6:
        return Decision(
            False,
            f"Order is ${notional:,.0f}, above the ${strategy.max_position_usd:,.0f} position cap.",
        )
    exposure = _exposure(session) + notional
    limit = get_settings().max_total_exposure_usd
    if exposure > limit + 1e-6:
        return Decision(False, f"Total exposure would be ${exposure:,.0f}, above ${limit:,.0f}.")
    recent = (
        session.query(Order)
        .filter(Order.submitted_at >= datetime.now(timezone.utc) - timedelta(minutes=1))
        .filter(Order.status != "rejected")
        .count()
    )
    if recent >= get_settings().max_orders_per_minute:
        return Decision(False, "Order rate limit hit. Wait a minute.")
    loss = daily_realized_pnl(session, strategy.id)
    if loss <= -float(strategy.max_daily_loss_usd):
        return Decision(
            False,
            f"Strategy daily loss ${loss:,.2f} reached the ${strategy.max_daily_loss_usd:,.0f} limit.",
            demote=True,
        )
    book = daily_realized_pnl(session, None)
    overall = state.max_daily_loss_usd if state else get_settings().max_daily_loss_usd
    if book <= -float(overall):
        return Decision(
            False,
            f"Desk daily loss ${book:,.2f} reached the ${overall:,.0f} limit.",
            demote=True,
        )
    return Decision(True)


def demote(session: Session, strategy: Strategy, reason: str, actor: str = "owner") -> None:
    before = {"mode": strategy.mode, "enabled": strategy.enabled}
    strategy.mode = "paper"
    strategy.enabled = False
    strategy.updated_at = datetime.now(timezone.utc)
    audit(
        session,
        "loss_limit",
        "strategy",
        strategy.id,
        before=before,
        after={"mode": "paper", "enabled": False, "reason": reason},
        actor=actor,
    )
    alert("loss_limit", {"strategy": strategy.name, "reason": reason})


def daily_realized_pnl(session: Session, strategy_id: str | None) -> float:
    start = datetime.now(ET).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    query = session.query(Trade).filter(Trade.ts >= start)
    if strategy_id:
        query = query.filter(Trade.strategy_id == strategy_id)
    return float(sum(row.pnl or 0 for row in query.all()))


def _exposure(session: Session) -> float:
    rows = session.query(Position).filter(Position.qty > 0).all()
    return float(sum(row.qty * row.avg_price for row in rows))
