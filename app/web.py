"""Server-rendered pages. The same operations back the JSON API."""

from __future__ import annotations

import json
import time
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import clear_failures, locked, record_failure, verify_password
from app.config import get_settings
from app.deps import get_db
from app.models import BacktestRun, Screener, Strategy
from app.ops import (
    DeskError,
    backtest_payload,
    create_screener,
    home_context,
    kill_switch,
    library_rows,
    pause_strategy,
    reset_kill_switch,
    run_backtest_request,
    run_pipeline,
    run_screener,
    send_ticker,
    set_mode,
    update_params,
)
from app.screeners.universes import UNIVERSES
from app.templating import render

router = APIRouter()


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _flash(request: Request, message: str) -> None:
    request.session["flash"] = message


@router.get("/login")
def login_form(request: Request):
    if request.session.get("user") == "owner":
        return _redirect("/")
    return render(request, "login.html", {"title": "Sign in", "bare": True})


@router.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    ip = request.client.host if request.client else "local"
    if locked(ip):
        return render(
            request,
            "login.html",
            {"title": "Sign in", "bare": True, "error": "Too many attempts. Try again in 15 minutes."},
            status_code=429,
        )
    password = str(form.get("password") or "")
    if not verify_password(password):
        record_failure(ip)
        return render(
            request,
            "login.html",
            {"title": "Sign in", "bare": True, "error": "Wrong password."},
            status_code=401,
        )
    clear_failures(ip)
    request.session["user"] = "owner"
    request.session["last_seen"] = time.time()
    return _redirect("/")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return _redirect("/login")


@router.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    ctx = home_context(db)
    ctx["title"] = "Market mood"
    return render(request, "home.html", ctx)


@router.post("/mood/refresh")
def mood_refresh(request: Request, db: Session = Depends(get_db)):
    from app.ops import refresh_mood

    try:
        refresh_mood(db, force=True)
        _flash(request, "Market mood refreshed.")
    except DeskError as exc:
        _flash(request, str(exc))
    return _redirect("/")


@router.get("/screeners")
def screeners_page(request: Request, db: Session = Depends(get_db)):
    rows = db.query(Screener).order_by(Screener.name).all()
    selected = request.query_params.get("id") or (rows[0].id if rows else None)
    results = []
    notes: list[str] = []
    if selected:
        from app.models import ScreenerResult

        latest = (
            db.query(ScreenerResult)
            .filter(ScreenerResult.screener_id == selected)
            .order_by(ScreenerResult.run_at.desc())
            .first()
        )
        if latest:
            results = (
                db.query(ScreenerResult)
                .filter(ScreenerResult.run_id == latest.run_id)
                .order_by(ScreenerResult.ticker)
                .all()
            )
    return render(
        request,
        "screeners.html",
        {
            "title": "Screener lab",
            "screeners": rows,
            "selected": selected,
            "results": results,
            "notes": notes,
            "universes": sorted({key for key in UNIVERSES if key != "us_smallcap_biotech"}),
            "strategies": db.query(Strategy).order_by(Strategy.name).all(),
        },
    )


@router.post("/screeners")
async def screeners_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    filters = _filters_from_form(form)
    try:
        row = create_screener(
            db,
            str(form.get("name") or ""),
            str(form.get("universe") or ""),
            filters,
            str(form.get("regime_gate") or "") or None,
            str(form.get("schedule_cron") or "") or None,
        )
    except DeskError as exc:
        _flash(request, str(exc))
        return _redirect("/screeners")
    _flash(request, f"Saved {row.name}.")
    return _redirect(f"/screeners?id={row.id}")


@router.post("/screeners/{screener_id}/run")
def screeners_run(screener_id: str, request: Request, db: Session = Depends(get_db)):
    try:
        result = run_screener(db, screener_id)
    except DeskError as exc:
        _flash(request, str(exc))
        return _redirect(f"/screeners?id={screener_id}")
    if result.get("skipped"):
        _flash(request, result["reason"])
    else:
        _flash(request, f"{len(result['results'])} names passed.")
    return _redirect(f"/screeners?id={screener_id}")


@router.get("/strategies")
def strategies_page(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    return render(
        request,
        "strategies.html",
        {
            "title": "Strategy library",
            "rows": library_rows(db),
            "live_open": settings.live_flag_open,
            "paper_days_required": settings.paper_days_required,
            "totp_on": bool(settings.totp_secret),
        },
    )


@router.post("/strategies/{strategy_id}/mode")
async def strategies_mode(strategy_id: str, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        strategy = set_mode(
            db,
            strategy_id,
            str(form.get("mode") or ""),
            str(form.get("confirm_text") or ""),
            str(form.get("totp") or ""),
        )
    except DeskError as exc:
        _flash(request, str(exc))
        return _redirect("/strategies")
    extra = ""
    if strategy.mode == "live":
        extra = " Live mode is flagged. Real orders stay off in this build."
    _flash(request, f"{strategy.name} is now {strategy.mode}.{extra}")
    return _redirect("/strategies")


@router.post("/strategies/{strategy_id}/pause")
async def strategies_pause(strategy_id: str, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    paused = str(form.get("paused") or "") == "1"
    try:
        strategy = pause_strategy(db, strategy_id, paused)
    except DeskError as exc:
        _flash(request, str(exc))
        return _redirect("/")
    _flash(request, f"{strategy.name} {'paused' if paused else 'resumed'}.")
    nxt = str(form.get("next") or "/strategies")
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/strategies"
    return _redirect(nxt)


@router.post("/strategies/{strategy_id}/params")
async def strategies_params(strategy_id: str, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        params = json.loads(str(form.get("params") or "{}"))
        strategy = update_params(db, strategy_id, params)
    except (DeskError, json.JSONDecodeError) as exc:
        _flash(request, f"Params were not saved. {exc}")
        return _redirect("/strategies")
    _flash(request, f"Updated params for {strategy.name}.")
    return _redirect("/strategies")


@router.get("/backtests")
def backtests_page(request: Request, db: Session = Depends(get_db)):
    strategies = db.query(Strategy).order_by(Strategy.name).all()
    runs = db.query(BacktestRun).order_by(BacktestRun.started_at.desc()).limit(20).all()
    names = {row.id: row.name for row in strategies}
    today = date.today()
    return render(
        request,
        "backtests.html",
        {
            "title": "Backtest runner",
            "strategies": strategies,
            "runs": runs,
            "names": names,
            "selected": request.query_params.get("strategy") or (strategies[0].id if strategies else ""),
            "start": (today - timedelta(days=365 * 3)).isoformat(),
            "end": today.isoformat(),
        },
    )


@router.post("/backtests")
async def backtests_run(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        run = run_backtest_request(
            db,
            str(form.get("strategy_id") or ""),
            [str(form.get("symbols") or "")],
            date.fromisoformat(str(form.get("start"))),
            date.fromisoformat(str(form.get("end"))),
            str(form.get("timeframe") or "1d"),
            float(form.get("capital") or 25000),
            float(form.get("cost_bps") or 5),
        )
    except (DeskError, ValueError) as exc:
        _flash(request, str(exc))
        return _redirect("/backtests")
    return _redirect(f"/backtests/{run.id}")


@router.get("/backtests/{run_id}")
def backtest_detail(run_id: str, request: Request, db: Session = Depends(get_db)):
    try:
        payload = backtest_payload(db, run_id)
    except DeskError as exc:
        _flash(request, str(exc))
        return _redirect("/backtests")
    from app.models import BacktestTrade

    trades = (
        db.query(BacktestTrade).filter(BacktestTrade.run_id == run_id).order_by(BacktestTrade.entry_ts).limit(200).all()
    )
    return render(
        request,
        "backtest_detail.html",
        {"title": "Backtest", "run": payload, "trades": trades},
    )


@router.post("/pipelines/{pipeline_id}/run")
def pipelines_run(pipeline_id: str, request: Request, db: Session = Depends(get_db)):
    try:
        result = run_pipeline(db, pipeline_id)
    except DeskError as exc:
        _flash(request, str(exc))
        return _redirect("/")
    _flash(request, result.get("message") or "Pipeline finished.")
    return _redirect("/")


@router.post("/actions/send")
async def actions_send(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        result = send_ticker(db, str(form.get("strategy_id") or ""), str(form.get("ticker") or ""))
    except DeskError as exc:
        _flash(request, str(exc))
        return _redirect("/")
    _flash(request, result.get("message") or "Sent.")
    return _redirect("/")


@router.post("/kill-switch")
async def kill(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    if str(form.get("confirm") or "") != "1":
        _flash(request, "Check the box to confirm the kill switch.")
        return _redirect("/")
    result = kill_switch(db, flatten=str(form.get("flatten") or "") == "1")
    _flash(
        request,
        f"Kill switch on. Canceled {result['canceled']} open orders. "
        f"{result['note']}",
    )
    return _redirect("/")


@router.post("/kill-switch/reset")
async def kill_reset(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        reset_kill_switch(db, str(form.get("confirm_text") or ""))
    except DeskError as exc:
        _flash(request, str(exc))
        return _redirect("/")
    _flash(request, "Kill switch reset. Live strategies stay in paper until you switch them again.")
    return _redirect("/")


def _filters_from_form(form) -> list[dict]:
    filters = []
    price_min = str(form.get("price_min") or "").strip()
    price_max = str(form.get("price_max") or "").strip()
    if price_min or price_max:
        filters.append(
            {
                "field": "price",
                "op": "between",
                "value": [float(price_min or 0), float(price_max or 1_000_000)],
            }
        )
    gap = str(form.get("gap_pct") or "").strip()
    if gap:
        filters.append({"field": "gap_pct", "op": ">", "value": float(gap)})
    rel = str(form.get("rel_vol") or "").strip()
    if rel:
        filters.append({"field": "rel_vol", "op": ">", "value": float(rel)})
    sector = str(form.get("sector") or "").strip()
    if sector:
        filters.append({"field": "sector", "op": "=", "value": sector})
    return filters
