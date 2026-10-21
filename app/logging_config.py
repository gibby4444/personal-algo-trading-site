"""One JSON object per log line, which is what Railway's log viewer expects."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_algo_configured", False):
        return
    root.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    root._algo_configured = True  # type: ignore[attr-defined]


def alert(event: str, detail: dict) -> None:
    """Log an operational event and POST it if ALERT_WEBHOOK is set."""
    log = logging.getLogger("app.alert")
    log.warning("%s %s", event, json.dumps(detail, default=str))
    from app.config import get_settings

    url = get_settings().alert_webhook
    if not url:
        return
    try:
        import httpx

        httpx.post(url, json={"event": event, "detail": detail}, timeout=5.0)
    except Exception:
        log.exception("alert webhook failed")
