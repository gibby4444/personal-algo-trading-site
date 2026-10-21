"""FastAPI app. One process serves the pages, the JSON API, and /health."""

from __future__ import annotations

import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from starlette.middleware.sessions import SessionMiddleware

from app.api import router as api_router
from app.auth import IDLE_SEC, idle_expired
from app.config import closed_reason, get_settings
from app.db import init_db, ping, session_scope
from app.logging_config import configure_logging
from app.ops import DeskError, refresh_mood
from app.templating import render
from app.web import router as web_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Algo desk", lifespan=lifespan)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if path == "/health" or path.startswith("/static"):
            return await call_next(request)

        if "csrf" not in request.session:
            request.session["csrf"] = secrets.token_urlsafe(32)

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            # The route builds its own Request from the scope, so the body has to
            # be put back or the form arrives empty after this middleware reads it.
            body = await request.body()

            async def replay() -> dict:
                return {"type": "http.request", "body": body, "more_body": False}

            request.scope["receive"] = replay
            token = request.headers.get("x-csrf-token")
            if not token:
                ctype = request.headers.get("content-type", "")
                if "application/json" not in ctype:
                    form = await request.form()
                    token = form.get("csrf")
            if not token or token != request.session.get("csrf"):
                if path.startswith("/api"):
                    return JSONResponse({"detail": "CSRF check failed."}, status_code=403)
                return render(
                    request,
                    "login.html",
                    {"title": "Sign in", "bare": True, "error": "The form expired. Refresh and try again."},
                    status_code=403,
                )

        reason = closed_reason()
        if reason:
            if path.startswith("/api"):
                return JSONResponse({"detail": reason}, status_code=403)
            return render(request, "locked.html", {"title": "Closed", "bare": True, "reason": reason}, status_code=403)

        if request.session.get("user") == "owner":
            last = request.session.get("last_seen")
            if idle_expired(last):
                request.session.clear()
                request.session["csrf"] = secrets.token_urlsafe(32)
            else:
                request.session["last_seen"] = time.time()
                return await call_next(request)

        if path.startswith("/login"):
            return await call_next(request)
        if path.startswith("/api"):
            return JSONResponse({"detail": "Login required."}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="algo_session",
        max_age=IDLE_SEC,
        same_site="strict",
        https_only=settings.cookie_secure,
    )
    static_dir = Path(__file__).resolve().parent.parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(web_router)
    app.include_router(api_router)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    @app.get("/health")
    def health():
        ok = ping()
        return JSONResponse(
            {
                "status": "ok" if ok else "degraded",
                "db": "ok" if ok else "down",
                "auth_configured": get_settings().auth_configured,
            },
            status_code=200 if ok else 503,
        )

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        session = websocket.scope.get("session") or {}
        if closed_reason() or session.get("user") != "owner":
            await websocket.close(code=1008)
            return
        await websocket.accept()
        with session_scope() as db:
            try:
                mood = refresh_mood(db, force=False)
            except DeskError as exc:
                mood = {"error": str(exc)}
        await websocket.send_json({"type": "mood", "data": mood})

    return app


app = create_app()
