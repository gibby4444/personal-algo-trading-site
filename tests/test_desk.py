"""Auth, the mood → screener → strategy path, and the live-order lock."""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import normalize_database_url
from tests.conftest import csrf_from, login


def test_database_url_rewrite():
    assert normalize_database_url("postgres://u:p@h:5432/db").startswith("postgresql+psycopg://")
    assert normalize_database_url("postgresql://u:p@h/db").startswith("postgresql+psycopg://")
    assert normalize_database_url("sqlite:///./data/local.db") == "sqlite:///./data/local.db"


def test_health_and_closed_desk(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "closed.db"))
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("DATA_PROVIDER", "fixture")
    from app.auth import reset_auth_state
    from app.db import reset_db
    from app.main import create_app

    reset_auth_state()
    reset_db()
    app = create_app()
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["auth_configured"] is False
        page = client.get("/")
        assert page.status_code == 403
        assert "APP_PASSWORD" in page.text
        api = client.get("/api/mood")
        assert api.status_code == 403
        assert "APP_PASSWORD" in api.json()["detail"]
    reset_db()


def test_login_and_mood_contract(client):
    locked = client.get("/api/mood")
    assert locked.status_code == 401
    bad = client.get("/login")
    token = csrf_from(bad.text)
    wrong = client.post("/login", data={"password": "nope", "csrf": token}, follow_redirects=False)
    assert wrong.status_code == 401
    login(client)
    home = client.get("/")
    assert home.status_code == 200
    assert "Market mood" in home.text
    assert "TQQQ/GLD 200-DMA Rotation" in home.text
    mood = client.get("/api/mood").json()
    for key in ("regime", "vix", "spy_above_200dma", "sentiment", "ts"):
        assert key in mood
    assert mood["regime"] == "risk_on"
    assert mood["spy_above_200dma"] is True
    assert mood["vix"] < 20


def test_screener_pipeline_and_backtest(client):
    login(client)
    screeners = client.get("/api/screeners").json()
    small = next(row for row in screeners if row["name"] == "SmallBioGap")
    page = client.get("/screeners")
    token = csrf_from(page.text)
    run = client.post(f"/api/screeners/{small['id']}/run", headers={"x-csrf-token": token})
    assert run.status_code == 200
    body = run.json()
    assert body["results"]
    assert body["results"][0]["ticker"] == "SAVA"
    assert body["results"][0]["gap_pct"] > 10
    assert body["results"][0]["rel_vol"] > 3

    pipes = client.get("/api/pipelines").json()
    pipeline = pipes[0]
    ran = client.post(f"/api/pipelines/{pipeline['id']}/run", headers={"x-csrf-token": token})
    assert ran.status_code == 200
    payload = ran.json()
    assert payload["stopped"] is None
    assert any(order["status"] == "filled" and order["broker"] == "sim" for order in payload["orders"])
    positions = client.get("/api/positions?mode=paper").json()
    assert any(row["symbol"] == "SAVA" for row in positions)

    strategies = client.get("/api/strategies").json()
    sma = next(row for row in strategies if row["name"] == "SMA Crossover")
    rotation = next(row for row in strategies if "TQQQ" in row["name"])
    end = date.today()
    start = end - timedelta(days=900)
    back = client.post(
        "/api/backtests",
        headers={"x-csrf-token": token},
        json={
            "strategy_id": sma["id"],
            "symbols": ["TSLA", "QQQ"],
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timeframe": "1d",
            "capital": 25000,
            "cost_bps": 5,
        },
    )
    assert back.status_code == 200
    result = back.json()
    assert result["status"] == "done"
    assert result["checks"]["no_lookahead"] is True
    assert result["checks"]["nan_check"] is True
    assert result["metrics"]["sharpe"] is not None
    assert result["metrics"]["trades"] >= 1
    curve = client.get(result["equity_curve_url"]).json()
    assert len(curve["equity"]) > 10
    csv_body = client.get(result["trades_url"]).text
    assert "symbol" in csv_body

    golden = client.post(
        "/api/backtests",
        headers={"x-csrf-token": token},
        json={
            "strategy_id": rotation["id"],
            "symbols": ["QQQ"],
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timeframe": "1d",
        },
    )
    assert golden.status_code == 200
    assert golden.json()["status"] == "done"
    page = client.get(f"/backtests/{result['run_id']}")
    assert page.status_code == 200
    assert "No lookahead" in page.text


def test_live_mode_is_locked_and_never_places_a_real_order(client, monkeypatch):
    login(client)
    token = csrf_from(client.get("/strategies").text)
    strategy = next(row for row in client.get("/api/strategies").json() if row["name"] == "Gap-Up Momentum")
    denied = client.post(
        f"/api/strategies/{strategy['id']}/mode",
        headers={"x-csrf-token": token},
        json={"mode": "live", "confirm_text": f"GO LIVE {strategy['name']}"},
    )
    assert denied.status_code == 400
    assert "LIVE_TRADING_ENABLED" in denied.json()["detail"]

    end = date.today()
    client.post(
        "/api/backtests",
        headers={"x-csrf-token": token},
        json={
            "strategy_id": strategy["id"],
            "symbols": ["TSLA"],
            "start": (end - timedelta(days=500)).isoformat(),
            "end": end.isoformat(),
            "timeframe": "1d",
        },
    )
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("ALPACA_LIVE_KEY", "live-key")
    monkeypatch.setenv("ALPACA_LIVE_SECRET", "live-secret")

    def explode(*args, **kwargs):
        raise AssertionError(f"HTTP should not have been called: {args} {kwargs}")

    monkeypatch.setattr(httpx, "post", explode)
    monkeypatch.setattr(httpx, "delete", explode)
    allowed = client.post(
        f"/api/strategies/{strategy['id']}/mode",
        headers={"x-csrf-token": token},
        json={"mode": "live", "confirm_text": f"GO LIVE {strategy['name']}"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["mode"] == "live"
    sent = client.post(
        "/actions/send",
        data={"csrf": token, "strategy_id": strategy["id"], "ticker": "SAVA"},
        follow_redirects=False,
    )
    assert sent.status_code == 303
    orders = client.get("/api/orders").json()
    assert any(row["status"] == "rejected" and "never posts" in (row["reason"] or "") for row in orders)


def test_paper_url_must_look_like_paper():
    from app.execution.brokers import paper_url_is_safe
    from app.execution.stubs import RobinhoodBroker, WebullBroker
    from app.strategies.base import OrderReq

    assert paper_url_is_safe("https://paper-api.alpaca.markets")
    assert not paper_url_is_safe("https://api.alpaca.markets")
    order = OrderReq(symbol="AAPL", side="buy", qty=1)
    with pytest.raises(NotImplementedError):
        RobinhoodBroker().submit(order)
    with pytest.raises(NotImplementedError):
        WebullBroker().submit(order)


def test_kill_switch_and_audit(client, monkeypatch):
    login(client)
    token = csrf_from(client.get("/").text)
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("ALPACA_LIVE_KEY", "k")
    monkeypatch.setenv("ALPACA_LIVE_SECRET", "s")
    strategy = client.get("/api/strategies").json()[0]
    end = date.today()
    done = client.post(
        "/api/backtests",
        headers={"x-csrf-token": token},
        json={
            "strategy_id": strategy["id"],
            "symbols": ["QQQ"],
            "start": (end - timedelta(days=800)).isoformat(),
            "end": end.isoformat(),
        },
    )
    assert done.json()["status"] == "done"
    switched = client.post(
        f"/api/strategies/{strategy['id']}/mode",
        headers={"x-csrf-token": token},
        json={"mode": "live", "confirm_text": f"GO LIVE {strategy['name']}"},
    )
    assert switched.status_code == 200
    killed = client.post("/api/kill-switch", headers={"x-csrf-token": token}, json={"scope": "all", "flatten": False})
    assert killed.status_code == 200
    assert killed.json()["killed"] is True
    again = client.get("/api/strategies").json()
    target = next(row for row in again if row["id"] == strategy["id"])
    assert target["mode"] == "paper"
    assert target["enabled"] is False


def test_csrf_blocks_a_post_without_a_token(client):
    login(client)
    response = client.post("/api/kill-switch", json={"scope": "all"})
    assert response.status_code == 403


def test_jobs_regime(client):
    from app.jobs import main

    assert main(["regime"]) == 0
    login(client)
    mood = client.get("/api/mood").json()
    assert mood["regime"] in {"risk_on", "risk_off", "mixed"}
