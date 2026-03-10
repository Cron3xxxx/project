from __future__ import annotations

from typing import Any


def extract_digits_code(raw: str, min_length: int = 4) -> str | None:
    code = "".join(ch for ch in (raw or "").strip() if ch.isdigit())
    if len(code) < min_length:
        return None
    return code


def parse_sent_code_metadata(sent: Any) -> dict:
    code_type = getattr(sent, "type", None)
    code_type_name = code_type.__class__.__name__ if code_type else "unknown"
    next_type = getattr(sent, "next_type", None)
    next_type_name = next_type.__class__.__name__ if next_type else "unknown"
    timeout = getattr(sent, "timeout", None)
    phone_code_hash = getattr(sent, "phone_code_hash", None)
    return {
        "code_type_name": code_type_name,
        "next_type_name": next_type_name,
        "timeout": timeout,
        "phone_code_hash": phone_code_hash,
    }


def delivery_hint(code_type_name: str) -> str:
    mapping = {
        "SentCodeTypeApp": "в приложении Telegram",
        "SentCodeTypeSms": "по SMS",
        "SentCodeTypeCall": "по звонку",
        "SentCodeTypeFlashCall": "через flash-call",
        "SentCodeTypeMissedCall": "через missed-call",
        "SentCodeTypeEmailCode": "на email",
    }
    return mapping.get(code_type_name, "неизвестный канал")
