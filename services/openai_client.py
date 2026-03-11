from __future__ import annotations

from typing import Any, Optional

from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TIMEOUT_SECONDS


_CLIENT: Optional[Any] = None


def _build_client() -> Any:
    try:
        from openai import OpenAI
    except ModuleNotFoundError as e:
        raise RuntimeError("OpenAI SDK is not installed. Run: pip install openai") from e

    if OPENAI_API_KEY:
        return OpenAI(api_key=OPENAI_API_KEY, timeout=OPENAI_TIMEOUT_SECONDS)
    return OpenAI(timeout=OPENAI_TIMEOUT_SECONDS)


def _get_client() -> Any:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    _CLIENT = _build_client()
    return _CLIENT


def _extract_text(resp) -> str:
    # Preferred: SDK exposes output_text on Responses
    text = getattr(resp, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    # Fallback: traverse output content
    try:
        parts: list[str] = []
        for item in getattr(resp, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                chunk = getattr(content, "text", None)
                if isinstance(chunk, str) and chunk:
                    parts.append(chunk)
        if parts:
            return "\n".join(parts).strip()
    except Exception:
        pass

    # Last resort: stringify
    return str(resp)


def generate_answer(*, user_prompt: str, system_prompt: str) -> str:
    client = _get_client()
    resp = client.responses.create(
        model=OPENAI_MODEL,
        instructions=system_prompt,
        input=user_prompt,
    )
    return _extract_text(resp)
