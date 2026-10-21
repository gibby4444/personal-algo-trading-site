"""Broker adapters.

Alpaca paper orders are sent only to a URL that contains "paper".
There is no function that posts to the live trading host. Live mode records
the intent and then refuses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.audit import audit
from app.config import get_settings
from app.models import Order, Position, Strategy, Trade, utcnow
from app.strategies.base import OrderReq

COST_BPS = 5.0


class LiveTradingDisabled(RuntimeError):
    pass


class BrokerError(RuntimeError):
    pass


@dataclass
class Account:
    equity: float
    cash: float
    buying_power: float
    mode: str


def paper_url_is_safe(url: str) -> bool:
    return "paper" in url.lower()


def place_order(
    session: Session,
    strategy: Strategy,
    order: OrderReq,
    *,
    pipeline_id: str | None,
    last_price: float,
    actor: str = "owner",
) -> Order:
    """Persist an order. Paper goes to Alpaca paper or the local simulator. Live never leaves the process."""
    if strategy.mode == "live":
        row = _row(
            strategy,
            order,
            pipeline_id,
            status="rejected",
            broker="alpaca",
            reason="Real orders are disabled. This build never posts to a live trading endpoint.",
            price=None,
        )
        session.add(row)
        audit(
            session,
            "order_rejected",
            "order",
            row.id,
            after={"symbol": order.symbol, "side": order.side, "qty": order.qty, "mode": "live"},
            actor=actor,
        )
        return row

    settings = get_settings()
    if settings.paper_keys_present:
        return _place_alpaca_paper(session, strategy, order, pipeline_id, actor)
    return _place_sim(session, strategy, order, pipeline_id, last_price, actor)


def _place_sim(session, strategy, order: OrderReq, pipeline_id, last_price: float, actor: str) -> Order:
    if last_price <= 0:
        raise BrokerError(f"No price for {order.symbol}")
    fee_rate = COST_BPS / 10_000.0
    fill = float(last_price)
    fee = abs(order.qty) * fill * fee_rate
    now = utcnow()
    row = _row(
        strategy,
        order,
        pipeline_id,
        status="filled",
        broker="sim",
        reason=None,
        price=fill,
        when=now,
    )
    session.add(row)
    session.flush()
    pnl = _apply_fill(session, strategy, row, fill, fee)
    session.add(
        Trade(
            order_id=row.id,
            strategy_id=strategy.id,
            symbol=order.symbol,
            qty=order.qty if order.side == "buy" else -order.qty,
            price=fill,
            fee=fee,
            pnl=pnl,
            ts=now,
        )
    )
    audit(
        session,
        "order_filled",
        "order",
        row.id,
        after={
            "symbol": order.symbol,
            "side": order.side,
            "qty": order.qty,
            "price": fill,
            "fee": fee,
            "broker": "sim",
            "mode": "paper",
        },
        actor=actor,
    )
    return row


def _place_alpaca_paper(session, strategy, order: OrderReq, pipeline_id, actor: str) -> Order:
    settings = get_settings()
    if not paper_url_is_safe(settings.alpaca_paper_base_url):
        raise LiveTradingDisabled(
            "Refusing the order. ALPACA_PAPER_BASE_URL does not look like the paper endpoint."
        )
    payload = {
        "symbol": order.symbol,
        "qty": order.qty,
        "side": order.side,
        "type": order.type,
        "time_in_force": "day",
    }
    if order.type == "limit" and order.limit_price is not None:
        payload["limit_price"] = order.limit_price
    url = f"{settings.alpaca_paper_base_url}/v2/orders"
    try:
        response = httpx.post(
            url,
            headers={
                "APCA-API-KEY-ID": settings.alpaca_paper_key or "",
                "APCA-API-SECRET-KEY": settings.alpaca_paper_secret or "",
            },
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError as exc:
        raise BrokerError(f"Alpaca paper rejected the order: {exc}") from exc
    status = body.get("status") or "accepted"
    fill = body.get("filled_avg_price")
    filled_at = utcnow() if fill else None
    row = _row(
        strategy,
        order,
        pipeline_id,
        status=status,
        broker="alpaca",
        reason=None,
        price=float(fill) if fill else None,
        when=filled_at or utcnow(),
        broker_order_id=body.get("id"),
    )
    if not fill:
        row.filled_at = None
    session.add(row)
    audit(
        session,
        "order_submitted",
        "order",
        row.id,
        after={"symbol": order.symbol, "side": order.side, "qty": order.qty, "broker": "alpaca-paper", "status": status},
        actor=actor,
    )
    return row


def cancel_open_orders(session: Session, actor: str = "owner") -> int:
    """Cancel local open orders. Alpaca paper cancels are attempted when paper keys exist."""
    open_rows = (
        session.query(Order)
        .filter(Order.status.in_(["new", "accepted", "pending_new", "open"]))
        .all()
    )
    settings = get_settings()
    count = 0
    for row in open_rows:
        if row.broker == "alpaca" and row.broker_order_id and settings.paper_keys_present:
            if not paper_url_is_safe(settings.alpaca_paper_base_url):
                raise LiveTradingDisabled("Refusing cancel. Paper base URL is not a paper host.")
            try:
                httpx.delete(
                    f"{settings.alpaca_paper_base_url}/v2/orders/{row.broker_order_id}",
                    headers={
                        "APCA-API-KEY-ID": settings.alpaca_paper_key or "",
                        "APCA-API-SECRET-KEY": settings.alpaca_paper_secret or "",
                    },
                    timeout=15,
                )
            except httpx.HTTPError:
                pass
        before = row.status
        row.status = "canceled"
        audit(
            session,
            "order_canceled",
            "order",
            row.id,
            before={"status": before},
            after={"status": "canceled"},
            actor=actor,
        )
        count += 1
    return count


def flatten_paper_positions(session: Session, actor: str = "owner") -> list[Order]:
    """Sell simulated paper positions. Live positions are left untouched on purpose."""
    rows = session.query(Position).filter(Position.mode == "paper", Position.qty > 0).all()
    placed = []
    strategies = {item.id: item for item in session.query(Strategy).all()}
    for position in rows:
        strategy = strategies.get(position.strategy_id)
        if strategy is None or strategy.mode == "live":
            continue
        order = OrderReq(symbol=position.symbol, side="sell", qty=float(position.qty))
        placed.append(
            _place_sim(
                session,
                strategy,
                order,
                pipeline_id=None,
                last_price=float(position.avg_price),
                actor=actor,
            )
        )
    return placed


def _apply_fill(session: Session, strategy: Strategy, order: Order, fill: float, fee: float) -> float:
    position = (
        session.query(Position)
        .filter(
            Position.strategy_id == strategy.id,
            Position.symbol == order.symbol,
            Position.mode == "paper",
            Position.broker == "sim",
        )
        .one_or_none()
    )
    signed = order.qty if order.side == "buy" else -order.qty
    pnl = 0.0
    if position is None:
        position = Position(
            strategy_id=strategy.id,
            mode="paper",
            broker="sim",
            symbol=order.symbol,
            qty=0,
            avg_price=0,
            unrealized_pnl=0,
        )
        session.add(position)
    if signed > 0:
        new_qty = position.qty + signed
        if new_qty <= 0:
            position.avg_price = 0
        else:
            position.avg_price = (position.avg_price * position.qty + fill * signed) / new_qty
        position.qty = new_qty
        pnl = -fee
    else:
        closing = min(position.qty, abs(signed))
        pnl = closing * (fill - position.avg_price) - fee
        position.qty = position.qty + signed
        if position.qty <= 1e-9:
            position.qty = 0
            position.avg_price = 0
    position.unrealized_pnl = 0
    position.updated_at = datetime.now(timezone.utc)
    return pnl


def _row(
    strategy: Strategy,
    order: OrderReq,
    pipeline_id: str | None,
    *,
    status: str,
    broker: str,
    reason: str | None,
    price: float | None,
    when: datetime | None = None,
    broker_order_id: str | None = None,
) -> Order:
    stamp = when or utcnow()
    return Order(
        strategy_id=strategy.id,
        pipeline_id=pipeline_id,
        mode="paper" if strategy.mode != "live" else "live",
        broker=broker,
        broker_order_id=broker_order_id,
        symbol=order.symbol,
        side=order.side,
        qty=order.qty,
        type=order.type,
        limit_price=order.limit_price,
        stop=order.stop,
        target=order.target,
        status=status,
        reason=reason,
        submitted_at=stamp,
        filled_at=stamp if status == "filled" else None,
        fill_price=price,
    )


def sim_account(session: Session) -> Account:
    positions = session.query(Position).filter(Position.broker == "sim").all()
    equity = sum(row.qty * row.avg_price for row in positions)
    return Account(equity=equity, cash=0.0, buying_power=0.0, mode="paper")
