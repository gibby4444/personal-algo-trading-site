"""Cron entrypoints. Railway runs one command and then the process exits.

    python -m app.jobs premarket
    python -m app.jobs regime
    python -m app.jobs eod
    python -m app.jobs nightly

Times in the README are UTC. Railway cron does not know America/New_York.
"""

from __future__ import annotations

import logging
import sys

from app.db import init_db, session_scope
from app.logging_config import configure_logging
from app.ops import DeskError, nightly_backtests, refresh_mood, run_due_pipelines, run_due_screeners

log = logging.getLogger("app.jobs")


def _safe(label: str, fn, session):
    try:
        return fn(session)
    except DeskError as exc:
        log.error("%s failed: %s", label, exc)
        return []


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(__doc__.strip())
        return 0 if args else 2
    command = args[0]
    init_db()
    with session_scope() as session:
        if command == "premarket":
            screened = _safe("screeners", run_due_screeners, session)
            pipes = _safe("pipelines", run_due_pipelines, session)
            log.info("premarket screeners=%s pipelines=%s", len(screened), len(pipes))
        elif command == "regime":
            mood = refresh_mood(session, force=True)
            log.info("regime %s vix=%s", mood.get("regime"), mood.get("vix"))
        elif command == "eod":
            mood = refresh_mood(session, force=True)
            log.info("eod regime %s", mood.get("regime"))
        elif command == "nightly":
            run_ids = nightly_backtests(session)
            log.info("nightly backtests %s", run_ids)
        else:
            print(f"Unknown job {command!r}. Use premarket, regime, eod, or nightly.")
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
