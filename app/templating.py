"""Jinja templates and the small formatters the pages share."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app.charts import drawdown_svg, equity_svg

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
ET = ZoneInfo("America/New_York")


def _money(value) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "-" if number < 0 else ""
    return f"{sign}${abs(number):,.2f}"


def _pct(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _num(value, digits: int = 2) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _when(value) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ET).strftime("%b %d %H:%M ET")


def _equity(points) -> Markup:
    return Markup(equity_svg(points or []))


def _drawdown(points) -> Markup:
    return Markup(drawdown_svg(points or []))


TEMPLATES.env.filters["money"] = _money
TEMPLATES.env.filters["pct"] = _pct
TEMPLATES.env.filters["num"] = _num
TEMPLATES.env.filters["when"] = _when
TEMPLATES.env.filters["equity_svg"] = _equity
TEMPLATES.env.filters["drawdown_svg"] = _drawdown


def render(request, name: str, context: dict, status_code: int = 200):
    payload = dict(context)
    payload["csrf"] = request.session.get("csrf", "")
    payload["flash"] = request.session.pop("flash", None)
    # FastAPI 0.115+ takes (request, name, context). Older releases take (name, context).
    try:
        return TEMPLATES.TemplateResponse(request, name, payload, status_code=status_code)
    except TypeError:
        payload["request"] = request
        return TEMPLATES.TemplateResponse(name, payload, status_code=status_code)
