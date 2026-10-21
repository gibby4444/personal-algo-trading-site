"""Fixtures share one password and the deterministic data provider."""

from __future__ import annotations

import os
import re

os.environ.setdefault("APP_PASSWORD", "correct-horse")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("DATA_PROVIDER", "fixture")
os.environ.setdefault("LIVE_TRADING_ENABLED", "false")
os.environ.setdefault("PAPER_DAYS_REQUIRED", "0")

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "desk.db"))
    monkeypatch.setenv("APP_PASSWORD", "correct-horse")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("DATA_PROVIDER", "fixture")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("PAPER_DAYS_REQUIRED", "0")
    for name in (
        "ALPACA_PAPER_KEY",
        "ALPACA_PAPER_SECRET",
        "ALPACA_LIVE_KEY",
        "ALPACA_LIVE_SECRET",
        "TOTP_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    from app.auth import reset_auth_state
    from app.db import reset_db

    reset_auth_state()
    reset_db()
    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
    reset_db()
    reset_auth_state()


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, html[:500]
    return match.group(1)


def login(client: TestClient, password: str = "correct-horse") -> str:
    page = client.get("/login")
    assert page.status_code == 200
    token = csrf_from(page.text)
    response = client.post(
        "/login",
        data={"password": password, "csrf": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return token
