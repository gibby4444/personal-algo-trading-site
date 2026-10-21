"""Single-user password login. The hash is computed at process start and compared with argon2."""

from __future__ import annotations

import time
from collections import defaultdict

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.config import get_settings

_hasher = PasswordHasher()
_cached: tuple[str, str] | None = None
_failures: dict[str, list[float]] = defaultdict(list)

LOCK_AFTER = 8
LOCK_WINDOW_SEC = 15 * 60
IDLE_SEC = 12 * 60 * 60


def reset_auth_state() -> None:
    global _cached
    _cached = None
    _failures.clear()


def _password_hash() -> str | None:
    global _cached
    password = get_settings().app_password
    if not password:
        return None
    if _cached is None or _cached[0] != password:
        _cached = (password, _hasher.hash(password))
    return _cached[1]


def verify_password(provided: str) -> bool:
    hashed = _password_hash()
    if not hashed:
        return False
    try:
        return bool(_hasher.verify(hashed, provided))
    except VerifyMismatchError:
        return False


def locked(ip: str) -> bool:
    now = time.time()
    recent = [ts for ts in _failures[ip] if now - ts < LOCK_WINDOW_SEC]
    _failures[ip] = recent
    return len(recent) >= LOCK_AFTER


def record_failure(ip: str) -> None:
    _failures[ip].append(time.time())


def clear_failures(ip: str) -> None:
    _failures.pop(ip, None)


def idle_expired(last_seen: float | None, now: float | None = None) -> bool:
    if last_seen is None:
        return False
    current = time.time() if now is None else now
    return current - last_seen > IDLE_SEC
