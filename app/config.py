"""Environment configuration. Secrets stay in the environment, never in code."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def on_railway() -> bool:
    return bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"))


def normalize_database_url(url: str) -> str:
    """Railway still hands out postgres:// URLs. SQLAlchemy wants an explicit driver."""
    value = url.strip()
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value[len("postgres://") :]
    if value.startswith("postgresql://") and "+" not in value.split("://", 1)[0]:
        return "postgresql+psycopg://" + value[len("postgresql://") :]
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_password: str | None
    session_secret: str
    session_secret_configured: bool
    cookie_secure: bool
    live_trading_enabled: bool
    alpaca_paper_key: str | None
    alpaca_paper_secret: str | None
    alpaca_live_key: str | None
    alpaca_live_secret: str | None
    alpaca_paper_base_url: str
    alpaca_data_base_url: str
    massive_api_key: str | None
    databento_api_key: str | None
    databento_dataset: str
    thetadata_user: str | None
    thetadata_pass: str | None
    thetadata_base_url: str
    ccxt_exchange: str
    ccxt_api_key: str | None
    ccxt_api_secret: str | None
    totp_secret: str | None
    alert_webhook: str | None
    data_provider: str
    max_total_exposure_usd: float
    max_orders_per_minute: int
    paper_days_required: int
    default_cost_bps: float
    max_daily_loss_usd: float

    @property
    def auth_configured(self) -> bool:
        return bool(self.app_password)

    @property
    def live_keys_present(self) -> bool:
        return bool(self.alpaca_live_key and self.alpaca_live_secret)

    @property
    def live_flag_open(self) -> bool:
        """The mode switch may move to live. Order placement still refuses."""
        return self.live_trading_enabled and self.live_keys_present

    @property
    def paper_keys_present(self) -> bool:
        return bool(self.alpaca_paper_key and self.alpaca_paper_secret)


def get_settings() -> Settings:
    raw_db = os.getenv("DATABASE_URL", "").strip()
    if not raw_db:
        raw_db = "sqlite:///./data/local.db"
    railway = on_railway()
    session_secret = os.getenv("SESSION_SECRET", "").strip()
    secret_configured = bool(session_secret)
    if not session_secret:
        # A hardcoded secret is only for local smoke tests. Railway must set one
        # or login refuses to issue a cookie.
        session_secret = "dev-only-session-secret"

    return Settings(
        database_url=normalize_database_url(raw_db),
        app_password=os.getenv("APP_PASSWORD", "").strip() or None,
        session_secret=session_secret,
        session_secret_configured=secret_configured,
        cookie_secure=_bool("COOKIE_SECURE", default=railway),
        live_trading_enabled=_bool("LIVE_TRADING_ENABLED", False),
        alpaca_paper_key=_blank("ALPACA_PAPER_KEY"),
        alpaca_paper_secret=_blank("ALPACA_PAPER_SECRET"),
        alpaca_live_key=_blank("ALPACA_LIVE_KEY"),
        alpaca_live_secret=_blank("ALPACA_LIVE_SECRET"),
        alpaca_paper_base_url=os.getenv(
            "ALPACA_PAPER_BASE_URL", "https://paper-api.alpaca.markets"
        ).rstrip("/"),
        alpaca_data_base_url=os.getenv("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets").rstrip(
            "/"
        ),
        massive_api_key=_blank("MASSIVE_API_KEY"),
        databento_api_key=_blank("DATABENTO_API_KEY"),
        databento_dataset=os.getenv("DATABENTO_DATASET", "GLBX.MDP3").strip() or "GLBX.MDP3",
        thetadata_user=_blank("THETADATA_USER"),
        thetadata_pass=_blank("THETADATA_PASS"),
        thetadata_base_url=os.getenv("THETADATA_BASE_URL", "http://127.0.0.1:25510").rstrip("/"),
        ccxt_exchange=os.getenv("CCXT_EXCHANGE", "binance").strip() or "binance",
        ccxt_api_key=_blank("CCXT_API_KEY"),
        ccxt_api_secret=_blank("CCXT_API_SECRET"),
        totp_secret=_blank("TOTP_SECRET"),
        alert_webhook=_blank("ALERT_WEBHOOK"),
        data_provider=os.getenv("DATA_PROVIDER", "yfinance").strip().lower() or "yfinance",
        max_total_exposure_usd=float(os.getenv("MAX_TOTAL_EXPOSURE_USD", "100000")),
        max_orders_per_minute=int(os.getenv("MAX_ORDERS_PER_MINUTE", "10")),
        paper_days_required=int(os.getenv("PAPER_DAYS_REQUIRED", "5")),
        default_cost_bps=float(os.getenv("COST_BPS", "5")),
        max_daily_loss_usd=float(os.getenv("MAX_DAILY_LOSS_USD", "500")),
    )


def _blank(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def closed_reason() -> str | None:
    """Why the desk is refusing visitors. None means the password is configured."""
    settings = get_settings()
    if not settings.auth_configured:
        return (
            "APP_PASSWORD is not set. This desk stays closed until you set that "
            "environment variable and restart the service."
        )
    if on_railway() and not settings.session_secret_configured:
        return (
            "SESSION_SECRET is not set. Refusing to sign cookies with a built-in "
            "secret. Set SESSION_SECRET on the Railway service and redeploy."
        )
    return None
