from __future__ import annotations

import time


def normalize_phone(phone: str) -> str | None:
    raw = (phone or "").strip()
    if not raw:
        return None
    if raw.startswith("+"):
        num = raw[1:]
        if num.isdigit():
            return "+" + num
        return None
    if raw.isdigit():
        return "+" + raw
    return None


def mask_phone(phone: str) -> str:
    if not phone or len(phone) < 6:
        return "***"
    return f"{phone[:3]}***{phone[-2:]}"


def auth_locked(state: dict, now_ts: int | None = None) -> int:
    lock_until = int(state.get("lock_until", 0) or 0)
    now = int(time.time()) if now_ts is None else int(now_ts)
    if lock_until and now < lock_until:
        return lock_until - now
    return 0


def register_auth_failure(
    state: dict,
    *,
    max_auth_attempts: int,
    auth_lock_seconds: int,
    now_ts: int | None = None,
) -> None:
    attempts = int(state.get("auth_attempts", 0)) + 1
    state["auth_attempts"] = attempts
    if attempts >= max_auth_attempts:
        now = int(time.time()) if now_ts is None else int(now_ts)
        state["lock_until"] = now + auth_lock_seconds


def clear_auth_failures(state: dict) -> None:
    state.pop("auth_attempts", None)
    state.pop("lock_until", None)


def code_resend_wait(state: dict, *, cooldown_seconds: int, now_ts: int | None = None) -> int:
    last_sent = int(state.get("last_code_sent_at", 0) or 0)
    now = int(time.time()) if now_ts is None else int(now_ts)
    if last_sent and now - last_sent < cooldown_seconds:
        return cooldown_seconds - (now - last_sent)
    return 0


def apply_sent_code_meta(state: dict, send_meta: dict, now_ts: int | None = None) -> None:
    state["phone_code_hash"] = send_meta.get("phone_code_hash")
    state["code_type_name"] = send_meta.get("code_type_name")
    now = int(time.time()) if now_ts is None else int(now_ts)
    state["last_code_sent_at"] = now
