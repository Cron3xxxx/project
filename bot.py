import asyncio
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

from telebot import TeleBot, types
from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PasswordHashInvalidError,
)

from config import (
    AI_ENABLED,
    AI_MAX_INPUT_CHARS,
    AI_MAX_MESSAGES,
    AI_MAX_MESSAGE_CHARS,
    AI_MAX_OUTPUT_CHARS,
    BOT_TOKEN,
    TG_API_HASH,
    TG_API_ID,
    TELETHON_SESSION,
    TG_FORCE_SMS,
)
from services import openai_client
from services.ai_formatter import build_ai_answer_message, render_ai_html
from services.auth_flow import (
    apply_sent_code_meta,
    auth_locked,
    clear_auth_failures,
    code_resend_wait,
    mask_phone,
    normalize_phone,
    register_auth_failure,
)
from services.auth_utils import delivery_hint, extract_digits_code, parse_sent_code_metadata
from services.auth_session import (
    close_login_client as close_login_client_service,
    complete_2fa as complete_2fa_service,
    complete_login as complete_login_service,
    send_login_code as send_login_code_service,
)
from services.auth_orchestrator import AuthOrchestrator
from services.date_input import parse_user_date
from services.parsing_orchestrator import ParsingOrchestrator
from services.parsing_service import parse_with_telethon as parse_with_telethon_service
from services.user_storage import (
    create_user,
    ensure_or_create_user,
    get_user,
    load_storage,
    save_storage,
    upsert_user,
)


DATE_FORMAT = "%d-%m-%Y"
MAX_CHANNELS = 20
MAX_TOPIC_LENGTH = 500  # используется как лимит длины запроса
HISTORY_LIMIT_MONTHS = 5
STORAGE_PATH = os.path.join("storage", "data.json")
SESSIONS_DIR = os.path.join("storage", "sessions")
LOGS_DIR = "logs"
AUTH_LOG_PATH = os.path.join(LOGS_DIR, "auth.log")
PARSING_LOG_PATH = os.path.join(LOGS_DIR, "parsing.log")
ASSETS_DIR = "assets"
DRAFTS_DIR = os.path.join("storage", "drafts")

MAX_AUTH_ATTEMPTS = 5
AUTH_LOCK_SECONDS = 10 * 60
CODE_RESEND_COOLDOWN = 60
SESSION_AUTH_TTL_SECONDS = 30

_TELETHON_LOOP = asyncio.new_event_loop()
_LOGIN_CLIENTS: dict[int, TelegramClient] = {}
_SESSION_AUTH_CACHE: dict[int, tuple[float, bool]] = {}


def _telethon_loop_runner() -> None:
    asyncio.set_event_loop(_TELETHON_LOOP)
    _TELETHON_LOOP.run_forever()


threading.Thread(target=_telethon_loop_runner, daemon=True).start()


def _run_telethon(coro):
    return asyncio.run_coroutine_threadsafe(coro, _TELETHON_LOOP).result()


def _get_auth_logger() -> logging.Logger:
    os.makedirs(LOGS_DIR, exist_ok=True)
    logger = logging.getLogger("auth")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(AUTH_LOG_PATH, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def _get_parsing_logger() -> logging.Logger:
    os.makedirs(LOGS_DIR, exist_ok=True)
    logger = logging.getLogger("parsing")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(PARSING_LOG_PATH, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def _load_storage() -> dict:
    return load_storage(STORAGE_PATH)


def _save_storage(data: dict) -> None:
    save_storage(STORAGE_PATH, data)


def _draft_path(user_id: int) -> str:
    return os.path.join(DRAFTS_DIR, f"{user_id}.json")


def _save_draft(user_id: int, payload: dict) -> None:
    os.makedirs(DRAFTS_DIR, exist_ok=True)
    path = _draft_path(user_id)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def _load_draft(user_id: int) -> dict | None:
    path = _draft_path(user_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return None


def _ensure_user(user_id: int) -> dict | None:
    return get_user(STORAGE_PATH, user_id)


def _ensure_or_create_user(user_id: int) -> dict:
    return ensure_or_create_user(STORAGE_PATH, user_id, DATE_FORMAT)


def _create_user(user_id: int) -> dict:
    return create_user(STORAGE_PATH, user_id, DATE_FORMAT)


def _upsert_user(user_id: int, user_payload: dict) -> None:
    upsert_user(STORAGE_PATH, user_id, user_payload)



bot = TeleBot(BOT_TOKEN)
user_states: dict[int, dict] = {}  # простое хранение состояния ввода


def _unlinked_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("Аккаунт", "Привязать аккаунт")
    kb.row("FAQ")
    kb.row("/start")
    return kb


def _inline_menu_main(user_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    if _has_user_session(user_id):
        kb.row(types.InlineKeyboardButton("👤 Аккаунт", callback_data="action:account"))
        kb.row(types.InlineKeyboardButton("📊 Парсинг", callback_data="action:start_parse"))
        kb.row(types.InlineKeyboardButton("📝 Написать пост", callback_data="action:write_post"))
        kb.row(types.InlineKeyboardButton("📌 Каналы", callback_data="menu:channels"))
        kb.row(types.InlineKeyboardButton("❓ FAQ", callback_data="action:faq"))
    return kb


def _inline_menu_parsing() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("📌 Каналы", callback_data="menu:channels"))
    kb.row(types.InlineKeyboardButton("⬅️ Назад", callback_data="menu:main"))
    return kb


def _inline_menu_channels() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("➕ Добавить канал", callback_data="action:add_channel"))
    kb.row(types.InlineKeyboardButton("🗑 Удалить канал", callback_data="action:delete_channel"))
    kb.row(types.InlineKeyboardButton("📄 Список каналов", callback_data="action:list_channels"))
    kb.row(types.InlineKeyboardButton("⬅️ Назад", callback_data="menu:main"))
    return kb


def _parse_date(date_str: str) -> datetime | None:
    return parse_user_date(date_str)


def _within_history_limit(date_obj: datetime) -> bool:
    oldest_allowed = datetime.utcnow() - timedelta(days=30 * HISTORY_LIMIT_MONTHS)
    return date_obj >= oldest_allowed


def _valid_channel(channel: str) -> bool:
    # Допустимые форматы: @username, t.me/username, https://t.me/username
    patterns = [
        r"^@[A-Za-z0-9_]{5,}$",
        r"^(https?://)?t\.me/[A-Za-z0-9_]{5,}$",
    ]
    return any(re.match(p, channel) for p in patterns)


def _normalize_channel(channel: str) -> str:
    ch = channel.strip()
    if ch.startswith("https://t.me/") or ch.startswith("http://t.me/") or ch.startswith("t.me/"):
        ch = ch.split("t.me/")[1]
    if not ch.startswith("@"):
        ch = "@" + ch
    return ch


def _extract_sentences(text: str, query: str) -> list[str]:
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    matches = [p for p in parts if query.lower() in p.lower()]
    if matches:
        return matches
    # если нет пунктуации, вернем весь текст
    return [text.strip()]


def _telethon_credentials_ok() -> bool:
    if not TG_API_ID or not TG_API_HASH:
        return False
    try:
        int(TG_API_ID)
    except ValueError:
        return False
    return True


def _session_status_text(user_id: int) -> str:
    return "✅ привязан" if _has_user_session(user_id) else "⚠️ не привязан"


def _main_menu_text(user_id: int, username: str | None = None) -> str:
    display = f"@{username}" if username else str(user_id)
    status = _session_status_text(user_id)
    if _has_user_session(user_id):
        actions = (
            "Что можно сделать:\n"
            "• 📊 Парсинг\n"
            "• 📌 Каналы\n"
            "• ❓ Открыть FAQ"
        )
    else:
        actions = (
            "Что можно сделать:\n"
            "• 👤 Аккаунт\n"
            "• 🔗 Привязать аккаунт\n"
            "• ❓ FAQ"
        )
    return (
        "🤖 Парсер каналов\n"
        f"Пользователь: {display}\n"
        f"Статус MTProto: {status}\n\n"
        f"{actions}"
    )


def _refresh_main_card(user_id: int, chat_id: int) -> None:
    state = user_states.setdefault(user_id, {})
    message_id = state.get("card_msg_id")
    if not message_id:
        card = bot.send_message(chat_id, _main_menu_text(user_id), reply_markup=_inline_menu_main(user_id))
        state["card_msg_id"] = card.message_id
        return
    try:
        _edit_card(chat_id, message_id, _main_menu_text(user_id), _inline_menu_main(user_id))
    except Exception:
        pass


def _edit_card(chat_id: int, message_id: int, text: str, markup: types.InlineKeyboardMarkup) -> bool:
    try:
        bot.edit_message_caption(chat_id, message_id, caption=text, reply_markup=markup)
        return True
    except Exception:
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)
            return True
        except Exception:
            return False


def _edit_card_photo(chat_id: int, message_id: int, filename: str, caption: str, markup=None) -> bool:
    path = os.path.join(ASSETS_DIR, filename)
    try:
        with open(path, "rb") as f:
            media = types.InputMediaPhoto(f, caption=caption)
            bot.edit_message_media(media, chat_id, message_id, reply_markup=markup)
        return True
    except Exception:
        return _edit_card(chat_id, message_id, caption, markup)


def _back_markup() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("⬅️ Назад", callback_data="menu:main"))
    return kb


def _reset_parse_flow(user_id: int, chat_id: int) -> None:
    state = user_states.get(user_id, {})
    if state.get("parse_mode"):
        user_states.pop(user_id, None)
    try:
        bot.clear_step_handler_by_chat_id(chat_id)
    except Exception:
        pass


def _fake_message(call) -> SimpleNamespace:
    return SimpleNamespace(chat=call.message.chat, from_user=call.from_user, text=None)


def _split_text_by_sentences(text: str, max_len: int = 3500) -> list[str]:
    if len(text) <= max_len:
        return [text]
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks: list[str] = []
    current = ""
    for part in parts:
        if not part:
            continue
        if len(part) > max_len:
            # fallback: hard split to avoid losing content
            for i in range(0, len(part), max_len):
                chunks.append(part[i : i + max_len])
            current = ""
            continue
        if not current:
            current = part
            continue
        if len(current) + 1 + len(part) <= max_len:
            current = current + " " + part
        else:
            chunks.append(current)
            current = part
    if current:
        chunks.append(current)
    return chunks


def _truncate_text(text: str, max_len: int) -> str:
    if max_len <= 0 or len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _render_ai_html(text: str) -> str:
    return render_ai_html(text)


def _send_long_text(chat_id: int, text: str, parse_mode: str | None = None) -> None:
    for chunk in _split_text_by_sentences(text):
        bot.send_message(chat_id, chunk, parse_mode=parse_mode)


def _send_asset_photo(chat_id: int, filename: str, caption: str, reply_markup=None):
    path = os.path.join(ASSETS_DIR, filename)
    try:
        with open(path, "rb") as f:
            if len(caption) > 1000:
                msg = bot.send_photo(chat_id, f, reply_markup=reply_markup)
                _send_long_text(chat_id, caption)
                return msg
            return bot.send_photo(chat_id, f, caption=caption, reply_markup=reply_markup)
    except Exception:
        _send_long_text(chat_id, caption)
        return None


def _user_session_path(user_id: int) -> str:
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    return os.path.join(SESSIONS_DIR, f"{user_id}.session")


def _has_user_session(user_id: int) -> bool:
    session_path = _user_session_path(user_id)
    if not os.path.exists(session_path):
        return False
    if not _telethon_credentials_ok():
        return False
    cached = _SESSION_AUTH_CACHE.get(user_id)
    now = time.time()
    if cached and now - cached[0] < SESSION_AUTH_TTL_SECONDS:
        return cached[1]
    try:
        ok = _run_telethon(_is_session_authorized(user_id))
    except Exception as e:  # noqa: BLE001
        _get_auth_logger().warning("session_check_failed user_id=%s error=%s", user_id, e)
        ok = False
    _SESSION_AUTH_CACHE[user_id] = (now, ok)
    if not ok:
        _delete_user_session_file(user_id, "session_not_authorized")
    return ok


async def _is_session_authorized(user_id: int) -> bool:
    api_id = int(TG_API_ID)
    api_hash = TG_API_HASH
    session_path = _user_session_path(user_id)
    if not os.path.exists(session_path):
        return False
    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()
    try:
        return await client.is_user_authorized()
    finally:
        await client.disconnect()


def _delete_user_session_file(user_id: int, reason: str) -> None:
    auth_log = _get_auth_logger()
    session_path = _user_session_path(user_id)
    if not os.path.exists(session_path):
        return
    try:
        os.remove(session_path)
        _SESSION_AUTH_CACHE.pop(user_id, None)
        auth_log.info("session_deleted user_id=%s reason=%s path=%s", user_id, reason, session_path)
    except Exception as e:  # noqa: BLE001
        auth_log.warning(
            "session_delete_failed user_id=%s reason=%s path=%s error=%s",
            user_id,
            reason,
            session_path,
            e,
        )


def _normalize_phone(phone: str) -> str | None:
    return normalize_phone(phone)


def _mask_phone(phone: str) -> str:
    return mask_phone(phone)


def _auth_locked(state: dict) -> int:
    return auth_locked(state)


def _register_auth_failure(state: dict) -> None:
    register_auth_failure(
        state,
        max_auth_attempts=MAX_AUTH_ATTEMPTS,
        auth_lock_seconds=AUTH_LOCK_SECONDS,
    )


def _clear_auth_failures(state: dict) -> None:
    clear_auth_failures(state)


def _reset_link_flow(user_id: int) -> None:
    user_states.pop(user_id, None)
    try:
        _run_telethon(_close_login_client(user_id))
    except Exception:
        pass


async def _send_login_code(user_id: int, phone: str) -> dict:
    return await send_login_code_service(
        user_id=user_id,
        phone=phone,
        api_id=int(TG_API_ID),
        api_hash=TG_API_HASH,
        session_path=_user_session_path(user_id),
        login_clients=_LOGIN_CLIENTS,
        client_factory=TelegramClient,
        logger=_get_auth_logger(),
        force_sms=TG_FORCE_SMS,
        parse_sent_code_metadata=parse_sent_code_metadata,
        mask_phone=_mask_phone,
    )


async def _complete_login(
    user_id: int,
    phone: str,
    code: str,
    phone_code_hash: str | None = None,
    password: str | None = None,
) -> None:
    await complete_login_service(
        user_id=user_id,
        phone=phone,
        code=code,
        phone_code_hash=phone_code_hash,
        password=password,
        api_id=int(TG_API_ID),
        api_hash=TG_API_HASH,
        session_path=_user_session_path(user_id),
        login_clients=_LOGIN_CLIENTS,
        client_factory=TelegramClient,
        session_password_needed_error=SessionPasswordNeededError,
        logger=_get_auth_logger(),
    )


async def _complete_2fa(user_id: int, password: str) -> None:
    await complete_2fa_service(
        user_id=user_id,
        password=password,
        login_clients=_LOGIN_CLIENTS,
        logger=_get_auth_logger(),
    )


async def _close_login_client(user_id: int) -> None:
    await close_login_client_service(user_id=user_id, login_clients=_LOGIN_CLIENTS)


async def _parse_with_telethon(
    user_id: int,
    channels: list[dict],
    query: str,
    date_from: datetime,
    date_to: datetime,
    progress_cb=None,
) -> tuple[int, list[str]]:
    api_id = int(TG_API_ID)
    api_hash = TG_API_HASH
    user_session_path = _user_session_path(user_id)
    return await parse_with_telethon_service(
        api_id=api_id,
        api_hash=api_hash,
        session_path=user_session_path if os.path.exists(user_session_path) else "",
        fallback_session=None,
        channels=channels,
        query=query,
        date_from=date_from,
        date_to=date_to,
        ai_max_messages=AI_MAX_MESSAGES,
        ai_max_message_chars=AI_MAX_MESSAGE_CHARS,
        progress_cb=progress_cb,
    )


_AUTH_ORCHESTRATOR = AuthOrchestrator(
    user_states=user_states,
    session_auth_cache=_SESSION_AUTH_CACHE,
    code_resend_cooldown=CODE_RESEND_COOLDOWN,
    bot=bot,
    get_auth_logger=_get_auth_logger,
    telethon_credentials_ok=_telethon_credentials_ok,
    has_user_session=_has_user_session,
    auth_locked=_auth_locked,
    register_auth_failure=_register_auth_failure,
    clear_auth_failures=_clear_auth_failures,
    normalize_phone=_normalize_phone,
    mask_phone=_mask_phone,
    run_telethon=_run_telethon,
    send_login_code=_send_login_code,
    complete_login=_complete_login,
    complete_2fa=_complete_2fa,
    reset_link_flow=_reset_link_flow,
    delete_user_session_file=_delete_user_session_file,
    refresh_main_card=_refresh_main_card,
    extract_digits_code=extract_digits_code,
    apply_sent_code_meta=apply_sent_code_meta,
    code_resend_wait=code_resend_wait,
    delivery_hint=delivery_hint,
    session_password_needed_error=SessionPasswordNeededError,
    phone_code_expired_error=PhoneCodeExpiredError,
    phone_code_invalid_error=PhoneCodeInvalidError,
    password_hash_invalid_error=PasswordHashInvalidError,
    reply_keyboard_remove_factory=types.ReplyKeyboardRemove,
)

_PARSING_ORCHESTRATOR = ParsingOrchestrator(
    user_states=user_states,
    bot=bot,
    date_format=DATE_FORMAT,
    history_limit_months=HISTORY_LIMIT_MONTHS,
    max_topic_length=MAX_TOPIC_LENGTH,
    telethon_session=None,
    get_logger=_get_parsing_logger,
    reset_parse_flow=_reset_parse_flow,
    ensure_or_create_user=_ensure_or_create_user,
    telethon_credentials_ok=_telethon_credentials_ok,
    has_user_session=_has_user_session,
    parse_date=_parse_date,
    within_history_limit=_within_history_limit,
    send_asset_photo=_send_asset_photo,
    edit_card_photo=_edit_card_photo,
    back_markup=_back_markup,
    inline_menu_channels=_inline_menu_channels,
    on_complete_parsing=lambda message, user, state, date_from_raw, date_to_raw, date_from, date_to: _complete_parsing(
        message,
        user,
        state,
        date_from_raw,
        date_to_raw,
        date_from,
        date_to,
    ),
)


@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    if _has_user_session(message.from_user.id):
        card = bot.send_message(
            message.chat.id,
            _main_menu_text(message.from_user.id, message.from_user.username),
            reply_markup=_inline_menu_main(message.from_user.id),
        )
        user_states.setdefault(message.from_user.id, {})["card_msg_id"] = card.message_id
    else:
        bot.send_message(
            message.chat.id,
            _main_menu_text(message.from_user.id, message.from_user.username),
            reply_markup=_unlinked_keyboard(),
        )


@bot.message_handler(func=lambda m: m.text == "Привязать аккаунт")
def handle_link_account(message):
    _start_link_flow(message)


def _start_link_flow(message) -> None:
    _AUTH_ORCHESTRATOR.start_link_flow(message)


def _handle_link_phone(message):
    _AUTH_ORCHESTRATOR.handle_link_phone(message)


def _handle_link_code(message):
    _AUTH_ORCHESTRATOR.handle_link_code(message)


def _handle_link_password(message):
    _AUTH_ORCHESTRATOR.handle_link_password(message)


_AUTH_ORCHESTRATOR.set_handlers(
    handle_phone=_handle_link_phone,
    handle_code=_handle_link_code,
    handle_password=_handle_link_password,
)

def _account_text(message) -> str:
    user = _ensure_or_create_user(message.from_user.id)
    channels = user.get("channels", [])
    last_parse = user.get("last_parse") or "нет данных"
    last_query = user.get("last_query") or "не задан"
    last_range = user.get("last_range") or {}
    last_range_text = "нет" if not last_range.get("from") else f"{last_range.get('from')} - {last_range.get('to')}"
    return (
        "👤 Профиль\n"
        f"ID: {message.from_user.id}\n"
        f"Статус MTProto: {_session_status_text(message.from_user.id)}\n"
        f"Дата регистрации: {user.get('registered_at')}\n\n"
        "📊 Активность\n"
        f"Последний парсинг: {last_parse}\n"
        f"Последний запрос: {last_query}\n"
        f"Последний диапазон: {last_range_text}\n\n"
        ""
    )


@bot.message_handler(func=lambda m: m.text == "Аккаунт")
def handle_account_button(message):
    if _has_user_session(message.from_user.id):
        bot.send_message(message.chat.id, "Аккаунт доступен в карточке.", reply_markup=None)
        return
    _send_asset_photo(message.chat.id, "2.png", _account_text(message), reply_markup=_unlinked_keyboard())


@bot.message_handler(func=lambda m: m.text == "Парсинг")
def handle_parsing_menu(message):
    if not _has_user_session(message.from_user.id):
        bot.send_message(
            message.chat.id,
            "⚠️ Сначала привяжите аккаунт, чтобы использовать парсинг.",
            reply_markup=None,
        )
        return
    bot.send_message(message.chat.id, "📊 Меню парсинга", reply_markup=_inline_menu_parsing())


@bot.message_handler(func=lambda m: m.text == "Начать парсинг")
def handle_parsing(message):
    _start_parsing_flow(message.from_user.id, message.chat.id)


def _start_parsing_flow(user_id: int, chat_id: int, card_message_id: int | None = None) -> None:
    _PARSING_ORCHESTRATOR.start_parsing_flow(user_id, chat_id, card_message_id=card_message_id)


def _handle_parse_query(message):
    _PARSING_ORCHESTRATOR.handle_parse_query(message)


def _handle_parse_date_from(message):
    _PARSING_ORCHESTRATOR.handle_parse_date_from(message)


def _handle_parse_date_to(message):
    _PARSING_ORCHESTRATOR.handle_parse_date_to(message)


_PARSING_ORCHESTRATOR.set_handlers(
    handle_query=_handle_parse_query,
    handle_date_from=_handle_parse_date_from,
    handle_date_to=_handle_parse_date_to,
)

def _complete_parsing(message, user, state, date_from_raw: str, date_to_raw: str, date_from: datetime, date_to: datetime):
    parsing_log = _get_parsing_logger()
    channels = user.get("channels", [])
    if not channels:
        bot.send_message(message.chat.id, "Нет настроенных каналов.", reply_markup=_back_markup())
        user_states.pop(message.from_user.id, None)
        return
    query = state.get("query")
    if not query:
        bot.send_message(message.chat.id, "Запрос не задан. Начните заново.", reply_markup=_back_markup())
        user_states.pop(message.from_user.id, None)
        return

    oldest_allowed = datetime.utcnow() - timedelta(days=30 * HISTORY_LIMIT_MONTHS)
    progress_msg = bot.send_message(message.chat.id, "⏳ Парсинг: 0%")
    progress_message_id = progress_msg.message_id
    progress_state = {"percent": -1, "ts": 0.0}

    def progress_cb(done: int, total: int) -> None:
        if total <= 0:
            return
        percent = int(done * 100 / total)
        now = time.time()
        if percent == progress_state["percent"] or now - progress_state["ts"] < 1.0:
            return
        progress_state["percent"] = percent
        progress_state["ts"] = now
        try:
            bot.edit_message_text(f"⏳ Парсинг: {percent}%", message.chat.id, progress_message_id)
        except Exception:
            pass

    try:
        parsing_log.info(
            "event=PARSE_EXEC_START user_id=%s channels=%s date_from=%s date_to=%s query_len=%s",
            message.from_user.id,
            len(channels),
            date_from_raw,
            date_to_raw,
            len(query),
        )
        total_found, logs = _run_telethon(
            _parse_with_telethon(message.from_user.id, channels, query, date_from, date_to, progress_cb=progress_cb)
        )
        user["last_parse"] = f"{date_from_raw} - {date_to_raw}"
        user["last_query"] = query
        user["last_range"] = {"from": date_from_raw, "to": date_to_raw}
        _upsert_user(message.from_user.id, user)

        draft_context = _truncate_text("\n\n".join(logs), AI_MAX_INPUT_CHARS)
        _save_draft(
            message.from_user.id,
            {
                "created_at": datetime.utcnow().isoformat() + "Z",
                "query": query,
                "date_from": date_from_raw,
                "date_to": date_to_raw,
                "total_messages": total_found,
                "materials": draft_context,
            },
        )

        summary_lines = []
        for line in logs:
            summary_lines.append(line.split("\n", 1)[0] if "\n" in line else line)
        summary_text = "\n".join(summary_lines) if summary_lines else "Без подробностей."
        _send_asset_photo(
            message.chat.id,
            "1.png",
            "✅ Парсинг завершён\n"
            f"Запрос: {query}\n"
            f"Диапазон: {date_from_raw} - {date_to_raw}\n"
            f"Собрано сообщений: {total_found}\n"
            f"Глубина истории: не старше {oldest_allowed.strftime(DATE_FORMAT)}\n\n"
            f"Краткая сводка:\n{summary_text}\n\n"
            "Материалы скрыты. Ответ сформирует ИИ.",
        )

        if AI_ENABLED:
            try:
                context_text = _truncate_text("\n\n".join(logs), AI_MAX_INPUT_CHARS)
                system_prompt = (
                    "You answer based on Telegram materials. "
                    "Do not invent facts. If there is not enough data, say so."
                )
                user_prompt = (
                    f"User query: {query}\n"
                    f"Date range: {date_from_raw} - {date_to_raw}\n"
                    f"Collected messages: {total_found}\n\n"
                    "Materials:\n"
                    f"{context_text}\n\n"
                    "Provide the best possible answer."
                )
                ai_text = openai_client.generate_answer(user_prompt=user_prompt, system_prompt=system_prompt)
                ai_text = _truncate_text(ai_text, AI_MAX_OUTPUT_CHARS)
                if ai_text.strip():
                    _send_long_text(message.chat.id, build_ai_answer_message(ai_text), parse_mode="HTML")
            except Exception as e:  # noqa: BLE001
                parsing_log.warning("event=PARSE_AI_ERROR user_id=%s error=%s", message.from_user.id, e)
                bot.send_message(message.chat.id, f"Ошибка AI-ответа: {e}")

        parsing_log.info("event=PARSE_EXEC_SUCCESS user_id=%s total_found=%s", message.from_user.id, total_found)
    except Exception as e:  # noqa: BLE001
        parsing_log.warning("event=PARSE_EXEC_FAILED user_id=%s error=%s", message.from_user.id, e)
        bot.send_message(message.chat.id, f"Ошибка парсинга: {e}", reply_markup=_back_markup())
    finally:
        try:
            bot.delete_message(message.chat.id, progress_message_id)
        except Exception:
            pass
    user_states.pop(message.from_user.id, None)

@bot.message_handler(func=lambda m: m.text == "Список каналов")
def handle_list_channels(message):
    if not _has_user_session(message.from_user.id):
        bot.send_message(
            message.chat.id,
            "⚠️ Сначала привяжите аккаунт, чтобы управлять каналами.",
            reply_markup=None,
        )
        return
    state = user_states.get(message.from_user.id, {})
    if not state.get("channels_menu"):
        user_states[message.from_user.id] = {"channels_menu": True}
        bot.send_message(message.chat.id, "📌 Меню каналов", reply_markup=_inline_menu_channels())
        return
    user = _ensure_or_create_user(message.from_user.id)
    channels = user.get("channels", [])
    if not channels:
        bot.send_message(message.chat.id, "Каналы не добавлены. Используйте кнопку 'Добавить канал'.")
        return
    lines = []
    for idx, ch in enumerate(channels, start=1):
        lines.append(f"{idx}. {ch.get('channel')}")
    text = "📌 Список каналов:\n" + "\n".join(lines)
    bot.send_message(message.chat.id, text)


@bot.message_handler(func=lambda m: m.text == "Удалить канал")
def handle_delete_channel(message):
    if not _has_user_session(message.from_user.id):
        bot.send_message(
            message.chat.id,
            "⚠️ Сначала привяжите аккаунт, чтобы управлять каналами.",
            reply_markup=None,
        )
        return
    user = _ensure_or_create_user(message.from_user.id)
    channels = user.get("channels", [])
    if not channels:
        bot.send_message(message.chat.id, "Каналы не добавлены.")
        return
    lines = [f"{idx}. {ch.get('channel')}" for idx, ch in enumerate(channels, start=1)]
    text = "🗑 Выберите номер канала для удаления:\n" + "\n".join(lines)
    user_states[message.from_user.id] = {"delete_mode": True, "channels_menu": True}
    msg = bot.send_message(message.chat.id, text)
    bot.register_next_step_handler(msg, _handle_delete_choice)


def _handle_delete_choice(message):
    state = user_states.get(message.from_user.id)
    if not state or not state.get("delete_mode"):
        bot.send_message(message.chat.id, "Состояние сброшено, начните заново.")
        user_states.pop(message.from_user.id, None)
        return
    try:
        idx = int((message.text or "").strip())
    except ValueError:
        bot.send_message(message.chat.id, "Введите корректный номер.")
        user_states.pop(message.from_user.id, None)
        return
    user = _ensure_or_create_user(message.from_user.id)
    channels = user.get("channels", [])
    if idx < 1 or idx > len(channels):
        bot.send_message(message.chat.id, "Некорректный номер.")
        user_states.pop(message.from_user.id, None)
        return
    removed = channels.pop(idx - 1)
    data = _load_storage()
    _upsert_user(message.from_user.id, user)
    user_states.pop(message.from_user.id, None)
    bot.send_message(
        message.chat.id,
        f"Канал удалён: {removed.get('channel')}",
    )


@bot.message_handler(func=lambda m: m.text == "Редактировать канал")
def handle_edit_channel(message):
    if not _has_user_session(message.from_user.id):
        bot.send_message(
            message.chat.id,
            "⚠️ Сначала привяжите аккаунт, чтобы управлять каналами.",
            reply_markup=None,
        )
        return
    user = _ensure_or_create_user(message.from_user.id)
    channels = user.get("channels", [])
    if not channels:
        bot.send_message(message.chat.id, "Каналы не добавлены.", reply_markup=_inline_menu_channels())
        return
    lines = [f"{idx}. {ch.get('channel')}" for idx, ch in enumerate(channels, start=1)]
    text = "✏️ Выберите номер канала для редактирования:\n" + "\n".join(lines)
    user_states[message.from_user.id] = {"edit_mode": True}
    msg = bot.send_message(message.chat.id, text, reply_markup=_inline_menu_channels())
    bot.register_next_step_handler(msg, _handle_edit_choice)


def _handle_edit_choice(message):
    state = user_states.get(message.from_user.id)
    if not state or not state.get("edit_mode"):
        bot.send_message(message.chat.id, "Состояние сброшено, начните заново.", reply_markup=_inline_menu_channels())
        user_states.pop(message.from_user.id, None)
        return
    try:
        idx = int((message.text or "").strip())
    except ValueError:
        bot.send_message(message.chat.id, "Введите корректный номер.", reply_markup=_inline_menu_channels())
        user_states.pop(message.from_user.id, None)
        return
    user = _ensure_or_create_user(message.from_user.id)
    channels = user.get("channels", [])
    if idx < 1 or idx > len(channels):
        bot.send_message(message.chat.id, "Некорректный номер.", reply_markup=_inline_menu_channels())
        user_states.pop(message.from_user.id, None)
        return
    state["edit_index"] = idx - 1
    msg = bot.send_message(message.chat.id, "Введите новое значение канала (@username или t.me/username).")
    bot.register_next_step_handler(msg, _handle_edit_channel_value)


def _handle_edit_channel_value(message):
    new_channel = (message.text or "").strip()
    if not _valid_channel(new_channel):
        bot.send_message(
            message.chat.id,
            "Неверный формат канала. Используйте @username или ссылку t.me/username. Редактирование прервано.",
            reply_markup=_inline_menu_channels(),
        )
        user_states.pop(message.from_user.id, None)
        return
    state = user_states.get(message.from_user.id)
    if not state or "edit_index" not in state:
        bot.send_message(message.chat.id, "Состояние сброшено, начните заново.", reply_markup=_inline_menu_channels())
        user_states.pop(message.from_user.id, None)
        return
    user = _ensure_or_create_user(message.from_user.id)
    channels = user.get("channels", [])
    idx = state.get("edit_index")
    if idx is None or idx < 0 or idx >= len(channels):
        bot.send_message(message.chat.id, "Некорректный индекс. Редактирование прервано.", reply_markup=_inline_menu_channels())
        user_states.pop(message.from_user.id, None)
        return

    channels[idx]["channel"] = new_channel

    data = _load_storage()
    _upsert_user(message.from_user.id, user)
    user_states.pop(message.from_user.id, None)

    bot.send_message(
        message.chat.id,
        f"✅ Канал обновлён: {new_channel}",
        reply_markup=_inline_menu_channels(),
    )


def _handle_post_request(message):
    state = user_states.get(message.from_user.id)
    if not state or not state.get("post_mode") or state.get("step") != "post_request":
        bot.send_message(message.chat.id, "Состояние сброшено, начните заново.")
        user_states.pop(message.from_user.id, None)
        return
    draft = _load_draft(message.from_user.id)
    if not draft:
        bot.send_message(
            message.chat.id,
            "Черновик не найден. Сначала выполните парсинг, чтобы собрать материалы.",
        )
        user_states.pop(message.from_user.id, None)
        return
    user_req = (message.text or "").strip()
    system_prompt = (
        "Ты пишешь пост для Telegram на основе материалов. "
        "Сам решай, нужен ли заголовок, эмодзи, хэштеги и структура. "
        "Не выдумывай факты, опирайся только на материалы. "
        "Выбери самое важное и пиши кратко."
    )
    user_prompt = (
        f"Запрос пользователя (что именно должно быть в посте): {user_req or 'без дополнительных требований'}\n"
        f"Исходный запрос парсинга: {draft.get('query', '')}\n"
        f"Диапазон дат: {draft.get('date_from', '')} - {draft.get('date_to', '')}\n"
        f"Собрано сообщений: {draft.get('total_messages', 0)}\n\n"
        "Материалы:\n"
        f"{draft.get('materials', '')}\n\n"
        "Сформируй готовый пост для Telegram."
    )
    try:
        ai_text = openai_client.generate_answer(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
        )
        ai_text = _truncate_text(ai_text, AI_MAX_OUTPUT_CHARS)
        if ai_text.strip():
            _send_long_text(message.chat.id, build_ai_answer_message(ai_text), parse_mode="HTML")
    except Exception as e:  # noqa: BLE001
        bot.send_message(message.chat.id, f"Ошибка генерации поста: {e}")
    finally:
        user_states.pop(message.from_user.id, None)


@bot.message_handler(func=lambda m: m.text == "Добавить канал")
def handle_add_channel(message):
    if not _has_user_session(message.from_user.id):
        bot.send_message(
            message.chat.id,
            "⚠️ Сначала привяжите аккаунт, чтобы управлять каналами.",
            reply_markup=None,
        )
        return
    user = _ensure_or_create_user(message.from_user.id)
    if len(user.get("channels", [])) >= MAX_CHANNELS:
        bot.send_message(message.chat.id, "Достигнут лимит каналов.", reply_markup=_inline_menu_channels())
        return
    user_states[message.from_user.id] = {"add_mode": True, "channels_menu": True}
    msg = bot.send_message(message.chat.id, "➕ Отправьте ссылку/username канала для парсинга.")
    bot.register_next_step_handler(msg, _handle_channel_input)


def _handle_channel_input(message):
    channel = (message.text or "").strip()
    if not channel:
        bot.send_message(message.chat.id, "Пустое значение. Повторите добавление.")
        user_states.pop(message.from_user.id, None)
        return
    if not _valid_channel(channel):
        bot.send_message(
            message.chat.id,
            "Неверный формат канала. Используйте @username или ссылку t.me/username.",
        )
        user_states.pop(message.from_user.id, None)
        return
    state = user_states.get(message.from_user.id)
    if not state or not state.get("add_mode"):
        bot.send_message(message.chat.id, "Состояние сброшено, начните заново.")
        user_states.pop(message.from_user.id, None)
        return

    user = _ensure_or_create_user(message.from_user.id)

    channel_entry = {"channel": channel}
    user.setdefault("channels", []).append(channel_entry)
    data = _load_storage()
    _upsert_user(message.from_user.id, user)
    user_states.pop(message.from_user.id, None)
    bot.send_message(
        message.chat.id,
        f"✅ Канал добавлен: {channel_entry['channel']}",
    )


@bot.message_handler(func=lambda m: m.text == "FAQ")
def handle_faq(message):
    _send_asset_photo(
        message.chat.id,
        "3.png",
        "❓ FAQ\n"
        "Подробная справка:\n"
        "https://telegra.ph/FAQ-02-03-13",
    )


@bot.message_handler(func=lambda m: m.text == "Написать пост")
def handle_write_post(message):
    if not _has_user_session(message.from_user.id):
        bot.send_message(
            message.chat.id,
            "⚠️ Сначала привяжите аккаунт и выполните парсинг.",
            reply_markup=None,
        )
        return
    draft = _load_draft(message.from_user.id)
    if not draft:
        bot.send_message(
            message.chat.id,
            "Черновик не найден. Сначала выполните парсинг, чтобы собрать материалы.",
            reply_markup=None,
        )
        return
    user_states[message.from_user.id] = {"post_mode": True, "step": "post_request"}
    msg = bot.send_message(
        message.chat.id,
        "✍️ Опишите, что именно должно быть в посте для Telegram (тема, тон, длина). "
        "Если ничего не указать, ИИ сам выберет формат.",
    )
    bot.register_next_step_handler(msg, _handle_post_request)


@bot.callback_query_handler(func=lambda call: True)
def handle_inline(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    data = call.data or ""
    user_states.setdefault(user_id, {})["card_msg_id"] = message_id
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    if data == "menu:main":
        _reset_parse_flow(user_id, chat_id)
        ok = _edit_card(chat_id, message_id, _main_menu_text(user_id, call.from_user.username), _inline_menu_main(user_id))
        if not ok:
            card = bot.send_message(chat_id, _main_menu_text(user_id, call.from_user.username), reply_markup=_inline_menu_main(user_id))
            user_states.setdefault(user_id, {})["card_msg_id"] = card.message_id
        return
    if data == "menu:channels":
        _edit_card(chat_id, message_id, "📌 Меню каналов", _inline_menu_channels())
        return
    if data == "action:link":
        _start_link_flow(_fake_message(call))
        return
    if data == "action:faq":
        ok = _edit_card_photo(
            chat_id,
            message_id,
            "3.png",
            "❓ FAQ\nПодробная справка:\nhttps://telegra.ph/FAQ-02-03-13",
            markup=_inline_menu_main(user_id),
        )
        if not ok:
            card = _send_asset_photo(
                chat_id,
                "3.png",
                "❓ FAQ\nПодробная справка:\nhttps://telegra.ph/FAQ-02-03-13",
                reply_markup=_inline_menu_main(user_id),
            )
            if card:
                user_states.setdefault(user_id, {})["card_msg_id"] = card.message_id
        return
    if data == "action:account":
        ok = _edit_card_photo(chat_id, message_id, "2.png", _account_text(_fake_message(call)), markup=_inline_menu_main(user_id))
        if not ok:
            card = _send_asset_photo(chat_id, "2.png", _account_text(_fake_message(call)), reply_markup=_inline_menu_main(user_id))
            if card:
                user_states.setdefault(user_id, {})["card_msg_id"] = card.message_id
        return
    if data == "action:start_parse":
        _start_parsing_flow(user_id, chat_id, card_message_id=message_id)
        return
    if data == "action:write_post":
        handle_write_post(_fake_message(call))
        return
    if data == "action:list_channels":
        user_states.setdefault(user_id, {})["channels_menu"] = True
        handle_list_channels(_fake_message(call))
        return
    if data == "action:add_channel":
        handle_add_channel(_fake_message(call))
        return
    if data == "action:delete_channel":
        handle_delete_channel(_fake_message(call))
        return


if __name__ == "__main__":
    bot.set_my_commands([types.BotCommand("start", "Открыть меню")])
    bot.infinity_polling()

