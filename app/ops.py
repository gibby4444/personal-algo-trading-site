"""Shared operations for the pages, the JSON API, and the cron entrypoints."""

from __future__ import annotations

import csv
import io
import threading
from datetime import date, datetime, timedelta, timezone

import pyotp
from sqlalchemy.orm import Session

from app.audit import audit
from app.backtest.checks import SURVIVORSHIP_NOTE, assert_causal
from app.backtest.engine import run_backtest
from app.backtest.metrics import compute_metrics
from app.config import get_settings
from app.data.normalize import DataQualityError
from app.data.registry import get_provider, load_bars
from app.execution.brokers import cancel_open_orders, flatten_paper_positions, place_order
from app.execution.risk import Decision, daily_realized_pnl, demote, evaluate
from app.logging_config import alert
from app.models import (
    BacktestMetric,
    BacktestRun,
    BacktestTrade,
    Order,
    Pipeline,
    Position,
    RegimeSnapshot,
    RiskState,
    Screener,
    ScreenerResult,
    Strategy,
    new_id,
    utcnow,
)
from app.regime import compute_mood
from app.screeners.engine import run_filters
from app.screeners.universes import tickers, universe
from app.strategies.base import OrderReq
from app.strategies.registry import get_strategy

_lock = threading.Lock()


class DeskError(ValueError):
    pass


def refresh_mood(session: Session, force: bool = False) -> dict:
    existing = session.query(RegimeSnapshot).order_by(RegimeSnapshot.ts.desc()).first()
    if existing and not force and existing.ts >= utcnow() - timedelta(minutes=15):
        return snapshot_dict(existing)
    try:
        with _lock:
            mood = compute_mood(get_provider())
    except Exception as exc:
        if existing:
            payload = snapshot_dict(existing)
            payload["stale"] = True
            payload["error"] = str(exc)
            return payload
        raise DeskError(f"Could not read the market: {exc}") from exc
    row = RegimeSnapshot(
        regime=mood["regime"],
        vix=mood["vix"],
        spy_vs_200dma=mood["spy_vs_200dma"],
        qqq_vs_200dma=mood["qqq_vs_200dma"],
        sentiment=mood["sentiment"],
        breadth=mood["breadth"],
        detail=mood,
    )
    session.add(row)
    session.flush()
    audit(session, "regime_snapshot", "regime_snapshot", row.id, after={"regime": mood["regime"], "vix": mood["vix"]})
    return snapshot_dict(row)


def snapshot_dict(row: RegimeSnapshot) -> dict:
    detail = dict(row.detail or {})
    detail.setdefault("regime", row.regime)
    detail.setdefault("vix", row.vix)
    detail.setdefault("spy_above_200dma", (row.spy_vs_200dma or 0) > 0)
    detail.setdefault("sentiment", row.sentiment)
    detail.setdefault("breadth", row.breadth)
    detail["ts"] = row.ts.isoformat()
    detail["snapshot_id"] = row.id
    return detail


def create_screener(session: Session, name: str, universe_name: str, filters: list, regime_gate: str | None, schedule_cron: str | None) -> Screener:
    name = name.strip()
    if not name:
        raise DeskError("Name the screener.")
    universe(universe_name)
    if session.query(Screener).filter(Screener.name == name).one_or_none():
        raise DeskError(f"A screener named {name} already exists.")
    row = Screener(
        name=name,
        universe=universe_name,
        filters=filters,
        regime_gate=regime_gate or None,
        schedule_cron=schedule_cron or None,
        enabled=True,
    )
    session.add(row)
    session.flush()
    audit(session, "screener_created", "screener", row.id, after={"name": name, "universe": universe_name, "filters": filters})
    return row


def run_screener(session: Session, screener_id: str) -> dict:
    screener = session.get(Screener, screener_id)
    if screener is None:
        raise DeskError("Screener not found.")
    mood = refresh_mood(session, force=False)
    gate = screener.regime_gate
    if gate and gate != "any" and mood.get("regime") != gate:
        audit(
            session,
            "screener_skipped",
            "screener",
            screener.id,
            after={"reason": "regime", "regime": mood.get("regime"), "gate": gate},
        )
        return {
            "run_id": None,
            "results": [],
            "skipped": True,
            "reason": f"Regime is {mood.get('regime')}. This screener only runs when it is {gate}.",
            "regime": mood.get("regime"),
        }
    end = date.today()
    start = end - timedelta(days=60)
    try:
        bars = load_bars(get_provider(), tickers(screener.universe), start, end, "1d")
    except Exception as exc:
        raise DeskError(f"Data pull failed: {exc}") from exc
    try:
        rows, notes = run_filters(screener.universe, list(screener.filters or []), bars)
    except (TypeError, ValueError, KeyError) as exc:
        raise DeskError(f"Filters could not be applied: {exc}") from exc
    run_id = new_id()
    stamp = utcnow()
    for row in rows:
        session.add(
            ScreenerResult(
                screener_id=screener.id,
                run_id=run_id,
                run_at=stamp,
                ticker=row["ticker"],
                metrics=row,
                regime=mood.get("regime"),
            )
        )
    audit(
        session,
        "screener_run",
        "screener",
        screener.id,
        after={"run_id": run_id, "hits": len(rows), "regime": mood.get("regime")},
    )
    return {
        "run_id": run_id,
        "results": [
            {
                "ticker": row["ticker"],
                "gap_pct": _round(row.get("gap_pct")),
                "rel_vol": _round(row.get("rel_vol")),
                "price": _round(row.get("price")),
                "sector": row.get("sector"),
                "name": row.get("name"),
                "float": row.get("float"),
                "news": row.get("news"),
            }
            for row in rows
        ],
        "notes": notes,
        "skipped": False,
        "regime": mood.get("regime"),
    }


def latest_flags(session: Session, limit: int = 25) -> list[dict]:
    latest = session.query(ScreenerResult).order_by(ScreenerResult.run_at.desc()).first()
    if latest is None:
        return []
    rows = (
        session.query(ScreenerResult)
        .filter(ScreenerResult.run_id == latest.run_id)
        .order_by(ScreenerResult.ticker)
        .limit(limit)
        .all()
    )
    screeners = {item.id: item.name for item in session.query(Screener).all()}
    flags = []
    for row in rows:
        metrics = row.metrics or {}
        flags.append(
            {
                "ticker": row.ticker,
                "screener": screeners.get(row.screener_id, ""),
                "screener_id": row.screener_id,
                "gap_pct": metrics.get("gap_pct"),
                "rel_vol": metrics.get("rel_vol"),
                "price": metrics.get("price"),
                "run_at": row.run_at,
            }
        )
    return flags


def update_params(session: Session, strategy_id: str, params: dict) -> Strategy:
    strategy = _strategy(session, strategy_id)
    if not isinstance(params, dict):
        raise DeskError("Params must be a JSON object.")
    before = dict(strategy.params or {})
    strategy.params = params
    strategy.updated_at = utcnow()
    audit(session, "params_changed", "strategy", strategy.id, before={"params": before}, after={"params": params})
    return strategy


def set_mode(session: Session, strategy_id: str, mode: str, confirm_text: str | None, totp: str | None) -> Strategy:
    strategy = _strategy(session, strategy_id)
    if mode not in {"backtest", "paper", "live"}:
        raise DeskError("Mode must be backtest, paper, or live.")
    if mode == "live":
        reason = live_block_reason(session, strategy, confirm_text, totp)
        if reason:
            audit(
                session,
                "mode_change_rejected",
                "strategy",
                strategy.id,
                before={"mode": strategy.mode},
                after={"mode": "live", "reason": reason},
            )
            raise DeskError(reason)
    before = strategy.mode
    strategy.mode = mode
    strategy.updated_at = utcnow()
    if mode != "backtest":
        strategy.enabled = True
    audit(session, "mode_changed", "strategy", strategy.id, before={"mode": before}, after={"mode": mode})
    return strategy


def live_block_reason(session: Session, strategy: Strategy, confirm_text: str | None, totp: str | None) -> str | None:
    settings = get_settings()
    state = session.get(RiskState, 1)
    if state and state.killed:
        return "Kill switch is on. Reset it before changing modes."
    if not settings.live_trading_enabled:
        return "Live mode is off. Set LIVE_TRADING_ENABLED=true to unlock the switch."
    if not settings.live_keys_present:
        return "Live mode needs ALPACA_LIVE_KEY and ALPACA_LIVE_SECRET. Neither is used to send orders in this build."
    expected = f"GO LIVE {strategy.name}"
    if (confirm_text or "").strip() != expected:
        return f'Type "{expected}" to confirm.'
    if settings.totp_secret:
        if not totp or not pyotp.TOTP(settings.totp_secret).verify(totp.strip(), valid_window=1):
            return "That TOTP code does not match TOTP_SECRET."
    done = (
        session.query(BacktestRun)
        .filter(BacktestRun.strategy_id == strategy.id, BacktestRun.status == "done")
        .count()
    )
    if done < 1:
        return "Run a completed backtest for this strategy before switching it to live."
    required = settings.paper_days_required
    if required > 0:
        days = _paper_days(session, strategy)
        if days < required:
            return (
                f"Paper trading so far covers {days} day(s). "
                f"PAPER_DAYS_REQUIRED is {required}."
            )
    return None


def _paper_days(session: Session, strategy: Strategy) -> int:
    from app.models import Trade

    rows = session.query(Trade.ts).filter(Trade.strategy_id == strategy.id).all()
    days = {row[0].astimezone(timezone.utc).date() for row in rows if row[0] is not None}
    return len(days)


def pause_strategy(session: Session, strategy_id: str, paused: bool) -> Strategy:
    strategy = _strategy(session, strategy_id)
    before = strategy.enabled
    strategy.enabled = not paused
    strategy.updated_at = utcnow()
    audit(
        session,
        "strategy_paused" if paused else "strategy_resumed",
        "strategy",
        strategy.id,
        before={"enabled": before},
        after={"enabled": strategy.enabled},
    )
    return strategy


def run_backtest_request(
    session: Session,
    strategy_id: str,
    symbols: list[str],
    start: date,
    end: date,
    timeframe: str,
    capital: float,
    cost_bps: float,
) -> BacktestRun:
    strategy = _strategy(session, strategy_id)
    symbols = _clean_symbols(symbols)
    if not symbols:
        raise DeskError("Enter at least one symbol.")
    if len(symbols) > 15:
        raise DeskError("Fifteen symbols is the limit for one run.")
    if start >= end:
        raise DeskError("The start date must be before the end date.")
    if (end - start).days > 365 * 20:
        raise DeskError("Keep the window inside 20 years.")
    if timeframe not in {"1d", "1h", "1m"}:
        raise DeskError("Timeframe must be 1d, 1h, or 1m.")
    if capital <= 0:
        raise DeskError("Capital must be positive.")
    impl = get_strategy(strategy.code_ref)
    needed = impl.symbols(symbols, strategy.params or {})
    run = BacktestRun(
        strategy_id=strategy.id,
        params=dict(strategy.params or {}),
        symbols=needed,
        start_date=start,
        end_date=end,
        timeframe=timeframe,
        capital=capital,
        cost_bps=cost_bps,
        status="running",
        started_at=utcnow(),
    )
    session.add(run)
    session.flush()
    try:
        bars = load_bars(get_provider(), needed, start, end, timeframe)
        for symbol in needed:
            if symbol not in bars or bars[symbol].empty:
                raise DeskError(f"No bars for {symbol}.")
        assert_causal(impl, bars, strategy.params or {})
        weights = impl.signals(bars, strategy.params or {})
        result = run_backtest(bars, weights, initial_capital=capital, cost_bps=cost_bps)
        stats = compute_metrics(result.equity, result.trades)
        checks = {
            "no_lookahead": True,
            "nan_check": True,
            "survivorship_note": SURVIVORSHIP_NOTE,
            "fill": "signal at close, fill at next open",
            "cost_bps": cost_bps,
        }
        equity_points = [
            {"ts": ts.isoformat(), "equity": round(float(value), 2)}
            for ts, value in result.equity.items()
        ]
        drawdown_points = [
            {"ts": ts.isoformat(), "drawdown": round(float(value), 6)}
            for ts, value in result.drawdown.items()
        ]
        session.add(
            BacktestMetric(
                run_id=run.id,
                sharpe=stats["sharpe"],
                sortino=stats["sortino"],
                cagr=stats["cagr"],
                max_dd=stats["max_dd"],
                win_rate=stats["win_rate"],
                trades=stats["trades"],
                profit_factor=stats["profit_factor"],
                equity_curve_url=f"/api/backtests/{run.id}/equity",
                equity_curve=equity_points,
                drawdown=drawdown_points,
                checks=checks,
                warnings=result.warnings,
            )
        )
        for trade in result.trades:
            session.add(
                BacktestTrade(
                    run_id=run.id,
                    symbol=trade.symbol,
                    entry_ts=trade.entry_ts,
                    exit_ts=trade.exit_ts,
                    qty=trade.qty,
                    pnl=trade.pnl,
                    entry_price=trade.entry_price,
                    exit_price=trade.exit_price,
                )
            )
        run.status = "done"
        run.finished_at = utcnow()
        audit(session, "backtest_done", "backtest_run", run.id, after={"symbols": needed, "sharpe": stats["sharpe"]})
    except (DeskError, DataQualityError, KeyError, ValueError) as exc:
        run.status = "failed"
        run.error = str(exc)
        run.finished_at = utcnow()
        audit(session, "backtest_failed", "backtest_run", run.id, after={"error": str(exc)})
    except Exception as exc:
        run.status = "failed"
        run.error = str(exc)
        run.finished_at = utcnow()
        audit(session, "backtest_failed", "backtest_run", run.id, after={"error": str(exc)})
        alert("backtest_failed", {"run_id": run.id, "error": str(exc)})
    return run


def backtest_payload(session: Session, run_id: str) -> dict:
    run = session.get(BacktestRun, run_id)
    if run is None:
        raise DeskError("Backtest not found.")
    metric = session.get(BacktestMetric, run.id)
    strategy = session.get(Strategy, run.strategy_id)
    payload = {
        "run_id": run.id,
        "status": run.status,
        "strategy_id": run.strategy_id,
        "strategy": strategy.name if strategy else None,
        "symbols": run.symbols,
        "start": run.start_date.isoformat() if run.start_date else None,
        "end": run.end_date.isoformat() if run.end_date else None,
        "timeframe": run.timeframe,
        "error": run.error,
        "trades_url": f"/api/backtests/{run.id}/trades.csv",
    }
    if metric:
        payload["metrics"] = {
            "sharpe": metric.sharpe,
            "sortino": metric.sortino,
            "cagr": metric.cagr,
            "max_dd": metric.max_dd,
            "win_rate": metric.win_rate,
            "trades": metric.trades,
            "profit_factor": metric.profit_factor,
        }
        payload["equity_curve_url"] = metric.equity_curve_url
        payload["checks"] = metric.checks
        payload["warnings"] = metric.warnings
        payload["equity"] = metric.equity_curve
        payload["drawdown"] = metric.drawdown
    return payload


def trades_csv(session: Session, run_id: str) -> str:
    rows = session.query(BacktestTrade).filter(BacktestTrade.run_id == run_id).all()
    if session.get(BacktestRun, run_id) is None:
        raise DeskError("Backtest not found.")
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["symbol", "entry_ts", "exit_ts", "qty", "entry_price", "exit_price", "pnl"])
    for row in rows:
        writer.writerow(
            [
                row.symbol,
                row.entry_ts.isoformat() if row.entry_ts else "",
                row.exit_ts.isoformat() if row.exit_ts else "",
                row.qty,
                row.entry_price,
                row.exit_price,
                row.pnl,
            ]
        )
    return buf.getvalue()


def run_pipeline(session: Session, pipeline_id: str) -> dict:
    pipeline = session.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise DeskError("Pipeline not found.")
    strategy = _strategy(session, pipeline.strategy_id)
    screener = session.get(Screener, pipeline.screener_id)
    if screener is None:
        raise DeskError("The pipeline's screener is missing.")
    if not pipeline.enabled:
        return {"stopped": "pipeline_disabled", "orders": []}
    mood = refresh_mood(session, force=False)
    required = (pipeline.regime_rule or {}).get("require")
    if required and mood.get("regime") != required:
        audit(
            session,
            "pipeline_stopped",
            "pipeline",
            pipeline.id,
            after={"stage": "regime", "regime": mood.get("regime"), "require": required},
        )
        return {
            "stopped": "regime",
            "regime": mood.get("regime"),
            "require": required,
            "orders": [],
            "message": f"Stopped at the regime filter. Market is {mood.get('regime')}; this pipeline wants {required}.",
        }
    screened = run_screener(session, screener.id)
    if screened.get("skipped"):
        return {"stopped": "screener", "orders": [], "message": screened.get("reason"), "results": []}
    candidates = screened["results"]
    if not candidates:
        audit(session, "pipeline_stopped", "pipeline", pipeline.id, after={"stage": "screener", "hits": 0})
        return {"stopped": "no_candidates", "orders": [], "message": "The screener returned no names.", "results": []}
    return _send_candidates(session, strategy, candidates, pipeline_id=pipeline.id, mood=mood)


def send_ticker(session: Session, strategy_id: str, ticker: str) -> dict:
    strategy = _strategy(session, strategy_id)
    ticker = ticker.strip().upper()
    if not ticker:
        raise DeskError("Missing ticker.")
    mood = refresh_mood(session, force=False)
    end = date.today()
    try:
        bars = load_bars(get_provider(), [ticker], end - timedelta(days=15), end, "1d")
    except Exception as exc:
        raise DeskError(str(exc)) from exc
    frame = bars[ticker]
    price = float(frame["close"].iloc[-1])
    prev = float(frame["close"].iloc[-2])
    gap_pct = (float(frame["open"].iloc[-1]) / prev - 1) * 100 if prev else None
    candidates = [{"ticker": ticker, "price": price, "gap_pct": gap_pct}]
    return _send_candidates(session, strategy, candidates, pipeline_id=None, mood=mood)


def _send_candidates(session, strategy: Strategy, candidates: list[dict], pipeline_id: str | None, mood: dict) -> dict:
    impl = get_strategy(strategy.code_ref)
    symbols = impl.symbols([row["ticker"] for row in candidates], strategy.params or {})
    end = date.today()
    try:
        bars = load_bars(get_provider(), symbols, end - timedelta(days=500), end, "1d")
    except Exception as exc:
        raise DeskError(f"Could not load bars for orders: {exc}") from exc
    requests = impl.orders_for_pipeline(
        bars=bars,
        params=strategy.params or {},
        candidates=candidates,
        max_position_usd=float(strategy.max_position_usd),
    )
    placed = []
    for req in requests:
        price = _price_for(bars, req, candidates)
        decision = evaluate(session, strategy, req.symbol, req.qty, price or 0)
        if not decision.ok:
            _reject(session, strategy, req, pipeline_id, decision)
            if decision.demote:
                demote(session, strategy, decision.reason)
            placed.append({"symbol": req.symbol, "status": "rejected", "reason": decision.reason})
            continue
        try:
            row = place_order(
                session,
                strategy,
                req,
                pipeline_id=pipeline_id,
                last_price=price or 0,
            )
        except Exception as exc:
            placed.append({"symbol": req.symbol, "status": "rejected", "reason": str(exc)})
            continue
        placed.append(
            {
                "symbol": row.symbol,
                "side": row.side,
                "qty": row.qty,
                "status": row.status,
                "broker": row.broker,
                "reason": row.reason,
                "fill_price": row.fill_price,
            }
        )
    audit(
        session,
        "pipeline_finished",
        "pipeline" if pipeline_id else "strategy",
        pipeline_id or strategy.id,
        after={"regime": mood.get("regime"), "candidates": [c["ticker"] for c in candidates], "orders": placed},
    )
    return {
        "stopped": None,
        "regime": mood.get("regime"),
        "results": candidates,
        "orders": placed,
        "message": _order_message(placed),
    }


def _reject(session, strategy, req: OrderReq, pipeline_id, decision: Decision) -> None:
    from app.models import Order

    row = Order(
        strategy_id=strategy.id,
        pipeline_id=pipeline_id,
        mode=strategy.mode if strategy.mode in {"paper", "live"} else "paper",
        broker="sim",
        symbol=req.symbol,
        side=req.side,
        qty=req.qty,
        type=req.type,
        status="rejected",
        reason=decision.reason,
        submitted_at=utcnow(),
    )
    session.add(row)
    session.flush()
    audit(
        session,
        "order_rejected",
        "order",
        row.id,
        after={"symbol": req.symbol, "reason": decision.reason},
    )


def kill_switch(session: Session, flatten: bool, actor: str = "owner") -> dict:
    state = session.get(RiskState, 1)
    if state is None:
        state = RiskState(id=1, killed=True, max_daily_loss_usd=get_settings().max_daily_loss_usd)
        session.add(state)
    state.killed = True
    state.killed_at = utcnow()
    state.updated_at = utcnow()
    changed = []
    for strategy in session.query(Strategy).filter(Strategy.mode == "live").all():
        before = {"mode": strategy.mode, "enabled": strategy.enabled}
        strategy.mode = "paper"
        strategy.enabled = False
        changed.append(strategy.name)
        audit(session, "kill_switch", "strategy", strategy.id, before=before, after={"mode": "paper", "enabled": False}, actor=actor)
    canceled = cancel_open_orders(session, actor=actor)
    flattened = 0
    note = "Live positions were not flattened. This build does not send real orders."
    if flatten:
        flattened = len(flatten_paper_positions(session, actor=actor))
    audit(
        session,
        "kill_switch",
        "risk_state",
        "1",
        after={"flatten": flatten, "canceled": canceled, "strategies": changed, "paper_flattened": flattened},
        actor=actor,
    )
    alert("kill_switch", {"canceled": canceled, "strategies": changed, "flatten": flatten})
    return {
        "killed": True,
        "canceled": canceled,
        "strategies_moved_to_paper": changed,
        "paper_positions_flattened": flattened,
        "note": note,
    }


def reset_kill_switch(session: Session, confirm_text: str) -> None:
    if confirm_text.strip() != "RESET KILL SWITCH":
        raise DeskError('Type "RESET KILL SWITCH" to resume.')
    state = session.get(RiskState, 1)
    if state is None:
        return
    state.killed = False
    state.updated_at = utcnow()
    audit(session, "kill_switch_reset", "risk_state", "1", after={"killed": False})


def home_context(session: Session) -> dict:
    try:
        mood = refresh_mood(session, force=False)
        mood_error = mood.get("error")
    except DeskError as exc:
        mood = None
        mood_error = str(exc)
    strategies = session.query(Strategy).order_by(Strategy.name).all()
    pnl = {row.id: daily_realized_pnl(session, row.id) for row in strategies}
    state = session.get(RiskState, 1)
    return {
        "mood": mood,
        "mood_error": mood_error,
        "flags": latest_flags(session),
        "strategies": strategies,
        "pipelines": session.query(Pipeline).order_by(Pipeline.name).all(),
        "screeners": {row.id: row for row in session.query(Screener).all()},
        "pnl": pnl,
        "daily_loss": daily_realized_pnl(session, None),
        "loss_limit": state.max_daily_loss_usd if state else get_settings().max_daily_loss_usd,
        "killed": bool(state and state.killed),
    }


def library_rows(session: Session) -> list[dict]:
    rows = []
    for strategy in session.query(Strategy).order_by(Strategy.name).all():
        metric = (
            session.query(BacktestMetric)
            .join(BacktestRun, BacktestRun.id == BacktestMetric.run_id)
            .filter(BacktestRun.strategy_id == strategy.id, BacktestRun.status == "done")
            .order_by(BacktestRun.finished_at.desc())
            .first()
        )
        rows.append({"strategy": strategy, "metric": metric, "pnl": daily_realized_pnl(session, strategy.id)})
    return rows


def run_due_screeners(session: Session) -> list[dict]:
    out = []
    for screener in session.query(Screener).filter(Screener.enabled.is_(True)).all():
        out.append({"screener": screener.name, **run_screener(session, screener.id)})
    return out


def run_due_pipelines(session: Session) -> list[dict]:
    out = []
    for pipeline in session.query(Pipeline).filter(Pipeline.enabled.is_(True)).all():
        out.append({"pipeline": pipeline.name, **run_pipeline(session, pipeline.id)})
    return out


def nightly_backtests(session: Session) -> list[str]:
    end = date.today()
    start = end - timedelta(days=365 * 3)
    ids = []
    for strategy in session.query(Strategy).filter(Strategy.enabled.is_(True)).all():
        impl = get_strategy(strategy.code_ref)
        symbols = impl.symbols(["QQQ"], strategy.params or {})
        run = run_backtest_request(session, strategy.id, symbols, start, end, "1d", 25000, get_settings().default_cost_bps)
        ids.append(run.id)
    return ids


def _strategy(session: Session, strategy_id: str) -> Strategy:
    strategy = session.get(Strategy, strategy_id)
    if strategy is None:
        raise DeskError("Strategy not found.")
    return strategy


def _clean_symbols(symbols: list[str]) -> list[str]:
    cleaned = []
    for raw in symbols:
        for part in str(raw).replace(";", ",").split(","):
            symbol = part.strip().upper()
            if not symbol:
                continue
            if not all(ch.isalnum() or ch in "^=./-" for ch in symbol) or len(symbol) > 20:
                raise DeskError(f"Symbol {symbol!r} is not usable.")
            cleaned.append(symbol)
    return list(dict.fromkeys(cleaned))


def _price_for(bars, req: OrderReq, candidates: list[dict]) -> float | None:
    if req.symbol in bars and not bars[req.symbol].empty:
        return float(bars[req.symbol]["close"].iloc[-1])
    for row in candidates:
        if row["ticker"] == req.symbol and row.get("price"):
            return float(row["price"])
    return None


def _order_message(placed: list[dict]) -> str:
    if not placed:
        return "No orders were proposed."
    filled = sum(1 for row in placed if row.get("status") == "filled")
    rejected = sum(1 for row in placed if row.get("status") == "rejected")
    return f"{filled} filled, {rejected} rejected, {len(placed)} total."


def _round(value):
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def positions_payload(session: Session, mode: str | None = None) -> list[dict]:
    query = session.query(Position)
    if mode:
        query = query.filter(Position.mode == mode)
    return [
        {
            "strategy_id": row.strategy_id,
            "mode": row.mode,
            "broker": row.broker,
            "symbol": row.symbol,
            "qty": row.qty,
            "avg_price": row.avg_price,
            "unrealized_pnl": row.unrealized_pnl,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in query.all()
    ]


def orders_payload(session: Session, strategy_id: str | None = None) -> list[dict]:
    query = session.query(Order).order_by(Order.submitted_at.desc())
    if strategy_id:
        query = query.filter(Order.strategy_id == strategy_id)
    rows = []
    for row in query.limit(200).all():
        rows.append(
            {
                "id": row.id,
                "strategy_id": row.strategy_id,
                "pipeline_id": row.pipeline_id,
                "mode": row.mode,
                "broker": row.broker,
                "symbol": row.symbol,
                "side": row.side,
                "qty": row.qty,
                "type": row.type,
                "status": row.status,
                "reason": row.reason,
                "fill_price": row.fill_price,
                "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
            }
        )
    return rows
