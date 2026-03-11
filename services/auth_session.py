from __future__ import annotations

from typing import Any, Callable


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
) -> None:
    client = login_clients.get(user_id)
    if client is None:
        client = client_factory(session_path, api_id, api_hash)
        await client.connect()
        login_clients[user_id] = client
    keep_client = False
    try:
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except session_password_needed_error:
            if not password:
                keep_client = True
                raise
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
) -> None:
    client = login_clients.get(user_id)
    if client is None:
        raise RuntimeError("2FA session is missing. Restart account linking.")
    if not client.is_connected():
        await client.connect()
    try:
        await client.sign_in(password=password)
    finally:
        await client.disconnect()
        login_clients.pop(user_id, None)


async def close_login_client(*, user_id: int, login_clients: dict[int, Any]) -> None:
    client = login_clients.pop(user_id, None)
    if client is not None:
        await client.disconnect()
