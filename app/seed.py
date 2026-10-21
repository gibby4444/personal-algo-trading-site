"""Example strategies, the small-cap biotech gap screener, and the pipeline that joins them."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Pipeline, RiskState, Screener, Strategy

# 12:00 UTC is 8:00 ET while the US is on daylight time. See the README for winter.
PREMARKET_CRON = "0 12 * * 1-5"


def seed(session: Session) -> None:
    if session.get(RiskState, 1) is None:
        session.add(RiskState(id=1, killed=False, max_daily_loss_usd=get_settings().max_daily_loss_usd))
    if session.query(Strategy).count():
        return

    rotation = Strategy(
        name="TQQQ/GLD 200-DMA Rotation",
        description=(
            "If QQQ is above its 200-day average, hold TQQQ. Otherwise hold gold (GLD). "
            "Signal at the close, fill at the next open."
        ),
        code_ref="app.strategies.rotation:TqqqGoldRotation",
        params={"ma_window": 200, "risk_symbol": "QQQ", "risk_on_symbol": "TQQQ", "risk_off_symbol": "GLD"},
        asset_class="etf",
        mode="paper",
        max_position_usd=10000,
        max_daily_loss_usd=500,
    )
    gap = Strategy(
        name="Gap-Up Momentum",
        description=(
            "Daily gap-and-go: gap above 10% that held the open, then buy the next open. "
            "The intraday version (break of the first 5-minute high, out by 15:55) needs minute bars."
        ),
        code_ref="app.strategies.gap:GapUpMomentum",
        params={"gap_min": 0.10, "hold_days": 1, "price_min": 2, "price_max": 20, "stop_pct": 0.05, "target_pct": 0.10},
        asset_class="stock",
        mode="paper",
        max_position_usd=5000,
        max_daily_loss_usd=500,
    )
    sma = Strategy(
        name="SMA Crossover",
        description="Long when the 20-day average is above the 50-day average. Flat otherwise.",
        code_ref="app.strategies.sma:SmaCrossover",
        params={"fast": 20, "slow": 50},
        asset_class="stock",
        mode="paper",
        max_position_usd=10000,
        max_daily_loss_usd=500,
    )
    session.add_all([rotation, gap, sma])
    session.flush()

    screener = Screener(
        name="SmallBioGap",
        universe="us_smallcap",
        filters=[
            {"field": "price", "op": "between", "value": [2, 20]},
            {"field": "gap_pct", "op": ">", "value": 10},
            {"field": "rel_vol", "op": ">", "value": 3},
            {"field": "sector", "op": "=", "value": "Biotech"},
        ],
        regime_gate="risk_on",
        schedule_cron=PREMARKET_CRON,
        enabled=True,
    )
    session.add(screener)
    session.flush()
    session.add(
        Pipeline(
            name="Biotech gap-and-go",
            regime_rule={"require": "risk_on", "allow": ["long_momentum"]},
            screener_id=screener.id,
            strategy_id=gap.id,
            enabled=True,
        )
    )
