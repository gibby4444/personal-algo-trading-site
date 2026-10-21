"""JSON API. Cookie session plus a CSRF header, same as the pages."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.deps import get_db
from app.ops import (
    DeskError,
    backtest_payload,
    create_screener,
    kill_switch,
    orders_payload,
    positions_payload,
    refresh_mood,
    run_backtest_request,
    run_pipeline,
    run_screener,
    set_mode,
    trades_csv,
    update_params,
)
from app.models import Pipeline, Screener, Strategy
from app.schemas import BacktestIn, KillIn, ModeIn, ParamsIn, ScreenerIn

router = APIRouter()


def _fail(exc: DeskError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)


@router.get("/api/mood")
def mood(db: Session = Depends(get_db)):
    try:
        return refresh_mood(db, force=False)
    except DeskError as exc:
        return _fail(exc)


@router.get("/api/screeners")
def list_screeners(db: Session = Depends(get_db)):
    rows = db.query(Screener).order_by(Screener.name).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "universe": row.universe,
            "filters": row.filters,
            "regime_gate": row.regime_gate,
            "schedule_cron": row.schedule_cron,
            "enabled": row.enabled,
        }
        for row in rows
    ]


@router.post("/api/screeners", status_code=201)
def post_screener(body: ScreenerIn, db: Session = Depends(get_db)):
    try:
        row = create_screener(
            db,
            body.name,
            body.universe,
            [item.model_dump() for item in body.filters],
            body.regime_gate,
            body.schedule_cron,
        )
    except DeskError as exc:
        return _fail(exc)
    return {"id": row.id, "name": row.name}


@router.post("/api/screeners/{screener_id}/run")
def post_run_screener(screener_id: str, db: Session = Depends(get_db)):
    try:
        return run_screener(db, screener_id)
    except DeskError as exc:
        return _fail(exc)


@router.get("/api/strategies")
def list_strategies(db: Session = Depends(get_db)):
    from app.ops import library_rows

    payload = []
    for item in library_rows(db):
        strategy = item["strategy"]
        metric = item["metric"]
        payload.append(
            {
                "id": strategy.id,
                "name": strategy.name,
                "code_ref": strategy.code_ref,
                "params": strategy.params,
                "asset_class": strategy.asset_class,
                "mode": strategy.mode,
                "max_position_usd": strategy.max_position_usd,
                "max_daily_loss_usd": strategy.max_daily_loss_usd,
                "enabled": strategy.enabled,
                "description": strategy.description,
                "sharpe": None if metric is None else metric.sharpe,
                "cagr": None if metric is None else metric.cagr,
                "max_dd": None if metric is None else metric.max_dd,
                "win_rate": None if metric is None else metric.win_rate,
                "trades": None if metric is None else metric.trades,
                "pnl_today": item["pnl"],
            }
        )
    return payload


@router.patch("/api/strategies/{strategy_id}")
def patch_strategy(strategy_id: str, body: ParamsIn, db: Session = Depends(get_db)):
    try:
        strategy = update_params(db, strategy_id, body.params)
    except DeskError as exc:
        return _fail(exc)
    return {"id": strategy.id, "params": strategy.params}


@router.post("/api/strategies/{strategy_id}/mode")
def post_mode(strategy_id: str, body: ModeIn, db: Session = Depends(get_db)):
    try:
        strategy = set_mode(db, strategy_id, body.mode, body.confirm_text, body.totp)
    except DeskError as exc:
        return _fail(exc)
    return {"id": strategy.id, "mode": strategy.mode, "name": strategy.name}


@router.post("/api/backtests")
def post_backtest(body: BacktestIn, db: Session = Depends(get_db)):
    try:
        run = run_backtest_request(
            db,
            body.strategy_id,
            body.symbols,
            body.start,
            body.end,
            body.timeframe,
            body.capital,
            body.cost_bps,
        )
    except DeskError as exc:
        return _fail(exc)
    return backtest_payload(db, run.id)


@router.get("/api/backtests/{run_id}")
def get_backtest(run_id: str, db: Session = Depends(get_db)):
    try:
        return backtest_payload(db, run_id)
    except DeskError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=404)


@router.get("/api/backtests/{run_id}/equity")
def get_equity(run_id: str, db: Session = Depends(get_db)):
    try:
        payload = backtest_payload(db, run_id)
    except DeskError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=404)
    return {"run_id": run_id, "equity": payload.get("equity") or [], "drawdown": payload.get("drawdown") or []}


@router.get("/api/backtests/{run_id}/trades.csv")
def get_trades_csv(run_id: str, db: Session = Depends(get_db)):
    try:
        body = trades_csv(db, run_id)
    except DeskError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=404)
    return Response(content=body, media_type="text/csv")


@router.get("/api/positions")
def get_positions(mode: str | None = None, db: Session = Depends(get_db)):
    return positions_payload(db, mode)


@router.get("/api/orders")
def get_orders(strategy_id: str | None = None, db: Session = Depends(get_db)):
    return orders_payload(db, strategy_id)


@router.post("/api/kill-switch")
def post_kill(body: KillIn, db: Session = Depends(get_db)):
    if body.scope != "all":
        return JSONResponse({"detail": "Only scope=all is supported."}, status_code=400)
    return kill_switch(db, flatten=body.flatten)


@router.get("/api/pipelines")
def list_pipelines(db: Session = Depends(get_db)):
    rows = db.query(Pipeline).order_by(Pipeline.name).all()
    names = {row.id: row.name for row in db.query(Strategy).all()}
    screens = {row.id: row.name for row in db.query(Screener).all()}
    return [
        {
            "id": row.id,
            "name": row.name,
            "regime_rule": row.regime_rule,
            "screener": screens.get(row.screener_id),
            "strategy": names.get(row.strategy_id),
            "enabled": row.enabled,
        }
        for row in rows
    ]


@router.post("/api/pipelines/{pipeline_id}/run")
def post_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    try:
        return run_pipeline(db, pipeline_id)
    except DeskError as exc:
        return _fail(exc)
