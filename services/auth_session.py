from __future__ import annotations

import os
from collections import Counter
from types import SimpleNamespace
from typing import Any, Callable


_TELETHON_DRY_RUN = os.getenv("TELETHON_DRY_RUN", "0") == "1"

_tg_request_counts: Counter[str] = Counter()


def _record_tg_request(logger: Any, request_name: str, **details: Any) -> None:
    """
    Фиксирует Telethon-запрос и логирует текущий накопленный счёт.
    """
    _tg_request_counts[request_name] += 1
    detail_parts = [f"{key}={value}" for key, value in details.items()]
    detail_text = " ".join(detail_parts)
    logger.info(
        "tg_request %s count=%s%s",
        request_name,
        _tg_request_counts[request_name],
        f" {detail_text}" if detail_text else "",
    )


async def send_login_code(
    *,
    user_id: int,
    phone: str,
    api_id: int,
    api_hash: str,
    session_path: str,
    login_clients: dict[int, Any],
    client_factory: Callable[[str, int, str], Any],
    logger: Any,
    force_sms: bool,
    parse_sent_code_metadata: Callable[[Any], dict],
    mask_phone: Callable[[str], str],
) -> dict:
    logger.info(
        "code_send_start user_id=%s phone=%s session_path=%s",
        user_id,
        mask_phone(phone),
        session_path,
    )
    client = login_clients.get(user_id)
    if client is None:
        client = client_factory(session_path, api_id, api_hash)
        await client.connect()
        login_clients[user_id] = client
    elif not client.is_connected():
        await client.connect()
    logger.info(
        "code_send_request user_id=%s phone=%s force_sms=%s",
        user_id,
        mask_phone(phone),
        force_sms,
    )
    if _TELETHON_DRY_RUN:
        logger.info("telethon dry-run: send_code_request skipped for user_id=%s", user_id)
        _record_tg_request(logger, "send_code_request", user_id=user_id, phone=mask_phone(phone))
        sent = SimpleNamespace(
            type=type("SentCodeTypeDryRun", (), {})(),
            next_type=type("SentCodeTypeDryRunNext", (), {})(),
            timeout=0,
            phone_code_hash="dry-run-hash",
        )
    else:
        # Telethon-обращение к Telegram API за кодом входа.
        _record_tg_request(logger, "send_code_request", user_id=user_id, phone=mask_phone(phone))
        sent = await client.send_code_request(phone, force_sms=force_sms)
    meta = parse_sent_code_metadata(sent)
    logger.info(
        "code_send_response user_id=%s phone=%s type=%s next_type=%s timeout=%s hash=%s",
        user_id,
        mask_phone(phone),
        meta.get("code_type_name"),
        meta.get("next_type_name"),
        meta.get("timeout"),
        meta.get("phone_code_hash") or "n/a",
    )
    return meta


async def complete_login(
    *,
    user_id: int,
    phone: str,
    code: str,
    phone_code_hash: str | None,
    password: str | None,
    api_id: int,
    api_hash: str,
    session_path: str,
    login_clients: dict[int, Any],
    client_factory: Callable[[str, int, str], Any],
    session_password_needed_error: type[Exception],
    logger: Any,
) -> None:
    if _TELETHON_DRY_RUN:
        logger.info("telethon dry-run: complete_login skipped (user=%s)", user_id)
        _record_tg_request(logger, "sign_in_code", user_id=user_id)
        if password:
            _record_tg_request(logger, "sign_in_password", user_id=user_id)
        return
    client = login_clients.get(user_id)
    if client is None:
        client = client_factory(session_path, api_id, api_hash)
        await client.connect()
        login_clients[user_id] = client
    keep_client = False
    try:
        try:
            # первый запрос с кодом подтверждения — Telethon sign_in c типом code.
            _record_tg_request(logger, "sign_in_code", user_id=user_id)
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except session_password_needed_error:
            if not password:
                keep_client = True
                raise
            # запрос пароля 2FA — отдельный Telethon sign_in.
            _record_tg_request(logger, "sign_in_password", user_id=user_id)
            await client.sign_in(password=password)
    finally:
        if not keep_client:
            await client.disconnect()
            login_clients.pop(user_id, None)


async def complete_2fa(
    *,
    user_id: int,
    password: str,
    login_clients: dict[int, Any],
    logger: Any,
) -> None:
    if _TELETHON_DRY_RUN:
        logger.info("telethon dry-run: complete_2fa skipped (user=%s)", user_id)
        _record_tg_request(logger, "sign_in_password", user_id=user_id)
        return
    client = login_clients.get(user_id)
    if client is None:
        raise RuntimeError("2FA session is missing. Restart account linking.")
    if not client.is_connected():
        await client.connect()
    try:
        _record_tg_request(logger, "sign_in_password", user_id=user_id)
        await client.sign_in(password=password)
    finally:
        await client.disconnect()
        login_clients.pop(user_id, None)


async def close_login_client(*, user_id: int, login_clients: dict[int, Any]) -> None:
    client = login_clients.pop(user_id, None)
    if client is not None:
        await client.disconnect()
