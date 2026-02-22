import asyncio
import json
import logging
import os
import re
import threading
import html
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

from telebot import TeleBot, types
from telethon import TelegramClient
from telethon.errors import (
    RPCError,
    SessionPasswordNeededError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PasswordHashInvalidError,
)
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.channels import JoinChannelRequest

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


DATE_FORMAT = "%d-%m-%Y"
MAX_CHANNELS = 20
MAX_TOPIC_LENGTH = 500  # используется как лимит длины запроса
HISTORY_LIMIT_MONTHS = 5
STORAGE_PATH = os.path.join("storage", "data.json")
SESSIONS_DIR = os.path.join("storage", "sessions")
LOGS_DIR = "logs"
AUTH_LOG_PATH = os.path.join(LOGS_DIR, "auth.log")
ASSETS_DIR = "assets"
DRAFTS_DIR = os.path.join("storage", "drafts")

MAX_AUTH_ATTEMPTS = 5
AUTH_LOCK_SECONDS = 10 * 60
CODE_RESEND_COOLDOWN = 60
SESSION_AUTH_TTL_SECONDS = 120

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


def _load_storage() -> dict:
    if not os.path.exists(STORAGE_PATH):
        return {"users": {}}
    try:
        with open(STORAGE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        # Если файл повреждён/пустой, начинаем с чистого хранилища
        return {"users": {}}


def _save_storage(data: dict) -> None:
    os.makedirs(os.path.dirname(STORAGE_PATH), exist_ok=True)
    with open(STORAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _draft_path(user_id: int) -> str:
    return os.path.join(DRAFTS_DIR, f"{user_id}.json")


def _save_draft(user_id: int, payload: dict) -> None:
    os.makedirs(DRAFTS_DIR, exist_ok=True)
    with open(_draft_path(user_id), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


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
    data = _load_storage()
    user = data["users"].get(str(user_id))
    if user:
        return user
    return None


def _ensure_or_create_user(user_id: int) -> dict:
    user = _ensure_user(user_id)
    if user:
        return user
    return _create_user(user_id)


def _create_user(user_id: int) -> dict:
    data = _load_storage()
    users = data.setdefault("users", {})
    now = datetime.utcnow().strftime(DATE_FORMAT)
    users[str(user_id)] = {
        "registered_at": now,
        "channels": [],
        "last_query": "",
        "last_range": {"from": None, "to": None},
        "last_parse": None,
    }
    _save_storage(data)
    return users[str(user_id)]



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
    try:
        return datetime.strptime(date_str, DATE_FORMAT)
    except ValueError:
        return None


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
    lines = text.splitlines()
    out_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            content = stripped[3:].strip()
            out_lines.append(f"<b>{html.escape(content)}</b>")
            continue
        if stripped.startswith("# "):
            content = stripped[2:].strip()
            out_lines.append(f"<b>{html.escape(content)}</b>")
            continue
        if stripped.startswith("**") and stripped.endswith("**") and len(stripped) > 4:
            content = stripped[2:-2].strip()
            out_lines.append(f"<b>{html.escape(content)}</b>")
            continue
        out_lines.append(html.escape(line))
    return "\n".join(out_lines)


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
    raw = phone.strip()
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


def _mask_phone(phone: str) -> str:
    if not phone or len(phone) < 6:
        return "***"
    return f"{phone[:3]}***{phone[-2:]}"


def _auth_locked(state: dict) -> int:
    lock_until = state.get("lock_until", 0)
    now = int(time.time())
    if lock_until and now < lock_until:
        return lock_until - now
    return 0


def _register_auth_failure(state: dict) -> None:
    attempts = int(state.get("auth_attempts", 0)) + 1
    state["auth_attempts"] = attempts
    if attempts >= MAX_AUTH_ATTEMPTS:
        state["lock_until"] = int(time.time()) + AUTH_LOCK_SECONDS


def _clear_auth_failures(state: dict) -> None:
    state.pop("auth_attempts", None)
    state.pop("lock_until", None)


def _reset_link_flow(user_id: int) -> None:
    user_states.pop(user_id, None)
    try:
        _run_telethon(_close_login_client(user_id))
    except Exception:
        pass


async def _send_login_code(user_id: int, phone: str) -> None:
    api_id = int(TG_API_ID)
    api_hash = TG_API_HASH
    session_path = _user_session_path(user_id)
    auth_log = _get_auth_logger()
    auth_log.info(
        "code_send_start user_id=%s phone=%s session_path=%s",
        user_id,
        _mask_phone(phone),
        session_path,
    )
    client = _LOGIN_CLIENTS.get(user_id)
    if client is None:
        client = TelegramClient(session_path, api_id, api_hash)
        await client.connect()
        _LOGIN_CLIENTS[user_id] = client
    elif not client.is_connected():
        await client.connect()
    auth_log.info(
        "code_send_request user_id=%s phone=%s force_sms=%s",
        user_id,
        _mask_phone(phone),
        TG_FORCE_SMS,
    )
    sent = await client.send_code_request(phone, force_sms=TG_FORCE_SMS)
    code_type = getattr(sent, "type", None)
    code_type_name = code_type.__class__.__name__ if code_type else "unknown"
    next_type = getattr(sent, "next_type", None)
    next_type_name = next_type.__class__.__name__ if next_type else "unknown"
    timeout = getattr(sent, "timeout", None)
    auth_log.info(
        "code_send_response user_id=%s phone=%s type=%s next_type=%s timeout=%s hash=%s",
        user_id,
        _mask_phone(phone),
        code_type_name,
        next_type_name,
        timeout,
        getattr(sent, "phone_code_hash", "n/a"),
    )


async def _complete_login(user_id: int, phone: str, code: str, password: str | None = None) -> None:
    api_id = int(TG_API_ID)
    api_hash = TG_API_HASH
    session_path = _user_session_path(user_id)
    client = _LOGIN_CLIENTS.get(user_id)
    if client is None:
        client = TelegramClient(session_path, api_id, api_hash)
        await client.connect()
        _LOGIN_CLIENTS[user_id] = client
    keep_client = False
    try:
        try:
            await client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError:
            if not password:
                keep_client = True
                raise
            await client.sign_in(password=password)
    finally:
        if not keep_client:
            await client.disconnect()
            _LOGIN_CLIENTS.pop(user_id, None)


async def _complete_2fa(user_id: int, password: str) -> None:
    client = _LOGIN_CLIENTS.get(user_id)
    if client is None:
        raise RuntimeError("Сессия для 2FA не найдена. Начните привязку заново.")
    if not client.is_connected():
        await client.connect()
    try:
        await client.sign_in(password=password)
    finally:
        await client.disconnect()
        _LOGIN_CLIENTS.pop(user_id, None)


async def _close_login_client(user_id: int) -> None:
    client = _LOGIN_CLIENTS.pop(user_id, None)
    if client is not None:
        await client.disconnect()


async def _parse_with_telethon(
    user_id: int,
    channels: list[dict],
    query: str,
    date_from: datetime,
    date_to: datetime,
    progress_cb=None,
) -> tuple[int, list[str]]:
    """
    Возвращает (total_found, logs_per_channel)
    """
    api_id = int(TG_API_ID)
    api_hash = TG_API_HASH
    session_path = _user_session_path(user_id)
    if os.path.exists(session_path):
        client = TelegramClient(session_path, api_id, api_hash)
    else:
        # fallback на старую строковую сессию, если она задана
        if not TELETHON_SESSION:
            raise RuntimeError("Нет файла сессии пользователя и не задан TELETHON_SESSION.")
        client = TelegramClient(StringSession(TELETHON_SESSION), api_id, api_hash)
    await client.start()
    total = 0
    logs = []
    total_collected = 0
    limit_reached = False
    total_channels = len(channels)
    processed = 0
    for ch in channels:
        name = _normalize_channel(ch.get("channel", ""))
        found = 0
        checked = 0
        joined = False
        samples: list[str] = []
        if not name:
            logs.append("пустое имя канала, пропуск")
            continue
        try:
            entity = await client.get_entity(name)
            entity_username = getattr(entity, "username", None)
            # Пытаемся присоединиться к публичному каналу, если ещё не в нём
            try:
                await client(JoinChannelRequest(entity))
                joined = True
            except Exception:
                pass  # если уже внутри или нельзя присоединиться — продолжаем

            async for msg in client.iter_messages(entity, offset_date=date_to + timedelta(days=1)):
                msg_dt = msg.date
                if msg_dt is None:
                    continue
                msg_dt_naive = msg_dt.replace(tzinfo=None)
                if msg_dt_naive < date_from:
                    break
                if msg_dt_naive > date_to + timedelta(days=1):
                    continue
                text = (msg.message or msg.raw_text or "")
                if msg.date:
                    msg_date = msg.date.strftime("%Y-%m-%d %H:%M")
                else:
                    msg_date = "unknown"
                checked += 1
                if text.strip():
                    found += 1
                    total_collected += 1
                    if len(samples) < 50:
                        clean = _truncate_text(text.replace("\n", " "), AI_MAX_MESSAGE_CHARS)
                        if entity_username and msg.id:
                            link = f"https://t.me/{entity_username}/{msg.id}"
                        else:
                            link = "n/a"
                        header = f"[{msg_date}] {name} | {link}"
                        samples.append(f"{header}\n{clean}")
                    if total_collected >= AI_MAX_MESSAGES:
                        limit_reached = True
                        break
        except RPCError as e:
            logs.append(f"{name}: ошибка RPC {e.__class__.__name__}")
            continue
        except Exception as e:  # noqa: BLE001
            logs.append(f"{name}: ошибка {e}")
            continue
        total += found
        join_note = "join ok" if joined else "join skipped/failed"
        line = f"{name}: просмотрено {checked}, собрано {found} ({join_note})"
        if samples:
            line += "\nпримеры:\n" + "\n".join(samples)
        logs.append(line)
        processed += 1
        if progress_cb:
            progress_cb(processed, total_channels)
        if limit_reached:
            logs.append(f"Достигнут лимит сообщений: {AI_MAX_MESSAGES}. Остальные каналы пропущены.")
            break
    await client.disconnect()
    return total, logs


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
    auth_log = _get_auth_logger()
    if not _telethon_credentials_ok():
        bot.send_message(
            message.chat.id,
            "Не заданы Telegram API креды (TG_API_ID/TG_API_HASH). Укажите их в .env.",
            reply_markup=None,
        )
        return
    if _has_user_session(message.from_user.id):
        bot.send_message(message.chat.id, "Аккаунт уже привязан.")
        return
    if user_states.get(message.from_user.id, {}).get("link_mode"):
        bot.send_message(message.chat.id, "Привязка уже начата. Следуйте предыдущим шагам.")
        return
    user_states[message.from_user.id] = {"link_mode": True}
    auth_log.info("link_start user_id=%s", message.from_user.id)
    msg = bot.send_message(message.chat.id, "Введите номер телефона в формате +79991112233.")
    bot.register_next_step_handler(msg, _handle_link_phone)


def _handle_link_phone(message):
    auth_log = _get_auth_logger()
    state = user_states.get(message.from_user.id)
    auth_log.info("phone_input user_id=%s", message.from_user.id)
    if not state or not state.get("link_mode"):
        bot.send_message(message.chat.id, "Состояние сброшено, начните заново.")
        user_states.pop(message.from_user.id, None)
        return
    remaining = _auth_locked(state)
    if remaining:
        auth_log.warning("auth_locked user_id=%s remaining=%s", message.from_user.id, remaining)
        bot.send_message(message.chat.id, f"Превышен лимит попыток. Повторите через {remaining} сек.")
        return
    last_sent = int(state.get("last_code_sent_at", 0))
    now = int(time.time())
    if last_sent and now - last_sent < CODE_RESEND_COOLDOWN:
        wait = CODE_RESEND_COOLDOWN - (now - last_sent)
        auth_log.info("code_resend_blocked user_id=%s wait=%s", message.from_user.id, wait)
        bot.send_message(message.chat.id, f"Код уже отправлен. Повторите через {wait} сек.")
        return
    phone = _normalize_phone(message.text or "")
    if not phone:
        auth_log.warning("phone_invalid user_id=%s", message.from_user.id)
        bot.send_message(message.chat.id, "Неверный формат телефона. Пример: +79991112233")
        user_states.pop(message.from_user.id, None)
        return
    auth_log.info("phone_normalized user_id=%s phone=%s", message.from_user.id, _mask_phone(phone))
    try:
        _run_telethon(_send_login_code(message.from_user.id, phone))
    except Exception as e:  # noqa: BLE001
        _register_auth_failure(state)
        remaining = _auth_locked(state)
        auth_log.warning("code_send_failed user_id=%s phone=%s error=%s", message.from_user.id, _mask_phone(phone), e)
        bot.send_message(message.chat.id, f"Ошибка отправки кода: {e}")
        if remaining:
            bot.send_message(message.chat.id, f"Лимит попыток исчерпан. Повторите через {remaining} сек.")
        _delete_user_session_file(message.from_user.id, "code_send_failed")
        _reset_link_flow(message.from_user.id)
        return
    state["phone"] = phone
    state["last_code_sent_at"] = int(time.time())
    auth_log.info("code_sent user_id=%s phone=%s", message.from_user.id, _mask_phone(phone))
    msg = bot.send_message(
        message.chat.id,
        "Код отправлен. Введите его, но НЕ отправляйте как чистые цифры.\n"
        "Например: 1a2b3c4d5 (бот сам уберёт буквы).",
    )
    bot.register_next_step_handler(msg, _handle_link_code)


def _handle_link_code(message):
    auth_log = _get_auth_logger()
    state = user_states.get(message.from_user.id)
    if not state or not state.get("link_mode") or "phone" not in state:
        bot.send_message(message.chat.id, "Состояние сброшено, начните заново.")
        _delete_user_session_file(message.from_user.id, "link_state_missing_code")
        _reset_link_flow(message.from_user.id)
        return
    remaining = _auth_locked(state)
    if remaining:
        bot.send_message(message.chat.id, f"Превышен лимит попыток. Повторите через {remaining} сек.")
        return
    raw = (message.text or "").strip()
    code = "".join(ch for ch in raw if ch.isdigit())
    if len(code) < 4:
        bot.send_message(
            message.chat.id,
            "Код не распознан. Введите его с любыми буквами между цифрами, например: 1a2b3c4d5",
            reply_markup=None,
        )
        _delete_user_session_file(message.from_user.id, "code_too_short")
        _reset_link_flow(message.from_user.id)
        return
    try:
        _run_telethon(_complete_login(message.from_user.id, state["phone"], code))
        _clear_auth_failures(state)
        _SESSION_AUTH_CACHE[message.from_user.id] = (time.time(), True)
        _reset_link_flow(message.from_user.id)
        _refresh_main_card(message.from_user.id, message.chat.id)
        try:
            bot.send_message(
                message.chat.id,
                "Аккаунт привязан.",
                reply_markup=types.ReplyKeyboardRemove(),
            )
        except Exception:
            pass
        auth_log.info("link_success user_id=%s phone=%s", message.from_user.id, _mask_phone(state["phone"]))
        bot.send_message(message.chat.id, "Аккаунт успешно привязан.", reply_markup=None)
        return
    except SessionPasswordNeededError:
        state["code"] = code
        auth_log.info("2fa_required user_id=%s phone=%s", message.from_user.id, _mask_phone(state["phone"]))
        msg = bot.send_message(message.chat.id, "Включена 2FA. Введите пароль от аккаунта.")
        bot.register_next_step_handler(msg, _handle_link_password)
        return
    except (PhoneCodeExpiredError, PhoneCodeInvalidError) as e:
        auth_log.warning("code_invalid user_id=%s phone=%s error=%s", message.from_user.id, _mask_phone(state["phone"]), e)
        _delete_user_session_file(message.from_user.id, "code_invalid")
        try:
            _run_telethon(_send_login_code(message.from_user.id, state["phone"]))
            state["last_code_sent_at"] = int(time.time())
            msg = bot.send_message(
                message.chat.id,
                "Код недействителен/истёк. Отправил новый код. Введите его в формате 1a2b3c4d5.",
            )
            bot.register_next_step_handler(msg, _handle_link_code)
            return
        except Exception as send_err:  # noqa: BLE001
            _register_auth_failure(state)
            remaining = _auth_locked(state)
            auth_log.warning(
                "code_resend_failed user_id=%s phone=%s error=%s",
                message.from_user.id,
                _mask_phone(state["phone"]),
                send_err,
            )
            bot.send_message(message.chat.id, f"Ошибка отправки нового кода: {send_err}")
            if remaining:
                bot.send_message(message.chat.id, f"Лимит попыток исчерпан. Повторите через {remaining} сек.")
            _delete_user_session_file(message.from_user.id, "code_resend_failed")
            _reset_link_flow(message.from_user.id)
            return
    except Exception as e:  # noqa: BLE001
        _register_auth_failure(state)
        remaining = _auth_locked(state)
        auth_log.warning("code_verify_failed user_id=%s phone=%s error=%s", message.from_user.id, _mask_phone(state["phone"]), e)
        bot.send_message(message.chat.id, f"Ошибка входа: {e}")
        if remaining:
            bot.send_message(message.chat.id, f"Лимит попыток исчерпан. Повторите через {remaining} сек.")
        _delete_user_session_file(message.from_user.id, "code_verify_failed")
        _reset_link_flow(message.from_user.id)


def _handle_link_password(message):
    auth_log = _get_auth_logger()
    state = user_states.get(message.from_user.id)
    if not state or not state.get("link_mode") or "phone" not in state or "code" not in state:
        bot.send_message(message.chat.id, "Состояние сброшено, начните заново.")
        _delete_user_session_file(message.from_user.id, "link_state_missing_2fa")
        _reset_link_flow(message.from_user.id)
        return
    remaining = _auth_locked(state)
    if remaining:
        bot.send_message(message.chat.id, f"Превышен лимит попыток. Повторите через {remaining} сек.")
        return
    password = (message.text or "").strip()
    if not password:
        bot.send_message(message.chat.id, "Пароль не может быть пустым.")
        _delete_user_session_file(message.from_user.id, "2fa_empty_password")
        _reset_link_flow(message.from_user.id)
        return
    try:
        _run_telethon(_complete_2fa(message.from_user.id, password))
        _clear_auth_failures(state)
        _SESSION_AUTH_CACHE[message.from_user.id] = (time.time(), True)
        _reset_link_flow(message.from_user.id)
        _refresh_main_card(message.from_user.id, message.chat.id)
        try:
            bot.send_message(
                message.chat.id,
                "Аккаунт привязан.",
                reply_markup=types.ReplyKeyboardRemove(),
            )
        except Exception:
            pass
        auth_log.info("link_success user_id=%s phone=%s (2fa)", message.from_user.id, _mask_phone(state["phone"]))
        bot.send_message(message.chat.id, "Аккаунт успешно привязан.", reply_markup=None)
    except PasswordHashInvalidError:
        _register_auth_failure(state)
        remaining = _auth_locked(state)
        auth_log.warning("2fa_invalid user_id=%s phone=%s", message.from_user.id, _mask_phone(state["phone"]))
        bot.send_message(message.chat.id, "Неверный пароль 2FA. Попробуйте ещё раз.")
        if remaining:
            bot.send_message(message.chat.id, f"Лимит попыток исчерпан. Повторите через {remaining} сек.")
            _delete_user_session_file(message.from_user.id, "2fa_invalid_locked")
            _reset_link_flow(message.from_user.id)
        else:
            msg = bot.send_message(message.chat.id, "Введите пароль от аккаунта.")
            bot.register_next_step_handler(msg, _handle_link_password)
        return
    except Exception as e:  # noqa: BLE001
        _register_auth_failure(state)
        remaining = _auth_locked(state)
        auth_log.warning("2fa_failed user_id=%s phone=%s error=%s", message.from_user.id, _mask_phone(state["phone"]), e)
        bot.send_message(message.chat.id, f"Ошибка входа: {e}")
        if remaining:
            bot.send_message(message.chat.id, f"Лимит попыток исчерпан. Повторите через {remaining} сек.")
        _delete_user_session_file(message.from_user.id, "2fa_failed")
        _reset_link_flow(message.from_user.id)


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
    _reset_parse_flow(user_id, chat_id)
    if card_message_id:
        user_states.setdefault(user_id, {})["card_msg_id"] = card_message_id
    user = _ensure_or_create_user(user_id)
    channels = user.get("channels", [])
    if not channels:
        bot.send_message(chat_id, "Нет настроенных каналов. Добавьте их в меню каналов.", reply_markup=_inline_menu_channels())
        return
    if not _telethon_credentials_ok():
        bot.send_message(
            chat_id,
            "⚠️ Не заданы Telegram API креды (TG_API_ID/TG_API_HASH). Укажите их в .env.",
            reply_markup=None,
        )
        return
    if not _has_user_session(user_id) and not TELETHON_SESSION:
        bot.send_message(
            chat_id,
            "⚠️ Для парсинга нужна MTProto-сессия.\n"
            "Файл сессии не найден — сначала привяжите аккаунт.",
            reply_markup=None,
        )
        return
    user_states[user_id] = {"parse_mode": True, "step": "query"}
    last_query = user.get("last_query")
    prompt = "🔎 Введите запрос (например: сколько сегодня было землетрясений)."
    if last_query:
        prompt += f"\nМожно ввести 'повторить' чтобы использовать прошлый запрос: {last_query}"
    card_msg_id = user_states.get(user_id, {}).get("card_msg_id")
    if card_msg_id:
        _edit_card_photo(chat_id, card_msg_id, "1.png", prompt, markup=_back_markup())
        bot.clear_step_handler_by_chat_id(chat_id)
        bot.register_next_step_handler_by_chat_id(chat_id, _handle_parse_query)
    else:
        msg = _send_asset_photo(chat_id, "1.png", prompt, reply_markup=_back_markup())
        bot.register_next_step_handler(msg, _handle_parse_query)


def _handle_parse_query(message):
    state = user_states.get(message.from_user.id)
    user = _ensure_or_create_user(message.from_user.id)
    if not state or not state.get("parse_mode") or not user or state.get("step") != "query":
        bot.send_message(message.chat.id, "Состояние сброшено, начните заново.", reply_markup=_back_markup())
        user_states.pop(message.from_user.id, None)
        return
    raw = (message.text or "").strip()
    if raw.lower() == "повторить" and user.get("last_query"):
        query = user["last_query"]
    elif raw:
        if len(raw) > MAX_TOPIC_LENGTH:
            bot.send_message(message.chat.id, f"Запрос не должен быть длиннее {MAX_TOPIC_LENGTH} символов.", reply_markup=_back_markup())
            user_states.pop(message.from_user.id, None)
            return
        query = raw
    else:
        bot.send_message(message.chat.id, "Пустой запрос. Попробуйте снова.", reply_markup=_back_markup())
        user_states.pop(message.from_user.id, None)
        return
    state["query"] = query
    state["step"] = "date_from"
    last_range = user.get("last_range") or {}
    prompt = f"📅 Введите дату начала ({DATE_FORMAT}), не старше {HISTORY_LIMIT_MONTHS} месяцев."
    if last_range.get("from"):
        prompt += f"\nМожно ввести 'повторить' чтобы использовать прошлый диапазон: {last_range.get('from')} - {last_range.get('to')}"
    bot.clear_step_handler_by_chat_id(message.chat.id)
    msg = bot.send_message(message.chat.id, prompt, reply_markup=_back_markup())
    bot.register_next_step_handler(msg, _handle_parse_date_from)


def _handle_parse_date_from(message):
    state = user_states.get(message.from_user.id)
    user = _ensure_or_create_user(message.from_user.id)
    if not state or not state.get("parse_mode") or not user or state.get("step") != "date_from":
        bot.send_message(message.chat.id, "Состояние сброшено, начните заново.", reply_markup=_back_markup())
        user_states.pop(message.from_user.id, None)
        return
    raw = (message.text or "").strip()
    last_range = user.get("last_range", {})
    if raw.lower() == "повторить" and user.get("last_range", {}).get("from"):
        date_from_raw = last_range.get("from")
        date_from = _parse_date(date_from_raw)
    else:
        date_from_raw = raw
        date_from = _parse_date(date_from_raw)
    if not date_from:
        bot.send_message(message.chat.id, f"Неверный формат. Используйте {DATE_FORMAT}.", reply_markup=_back_markup())
        user_states.pop(message.from_user.id, None)
        return
    if not _within_history_limit(date_from):
        bot.send_message(message.chat.id, f"Дата начала должна быть не старше {HISTORY_LIMIT_MONTHS} месяцев.", reply_markup=_back_markup())
        user_states.pop(message.from_user.id, None)
        return
    state["parse_date_from"] = date_from_raw
    # Если запросили "повторить" и есть сохранённый конец диапазона — используем его и завершаем без дополнительного вопроса
    if raw.lower() == "повторить" and last_range.get("to"):
        date_to_raw = last_range["to"]
        date_to = _parse_date(date_to_raw)
        if not date_to:
            bot.send_message(message.chat.id, f"Неверный сохранённый конец диапазона. Введите заново.", reply_markup=_back_markup())
            user_states.pop(message.from_user.id, None)
            return
        if date_to < date_from:
            bot.send_message(message.chat.id, "Сохранённая дата окончания раньше даты начала. Введите заново.", reply_markup=_back_markup())
            user_states.pop(message.from_user.id, None)
            return
        if not _within_history_limit(date_to):
            bot.send_message(message.chat.id, f"Дата окончания должна быть не старше {HISTORY_LIMIT_MONTHS} месяцев.", reply_markup=_back_markup())
            user_states.pop(message.from_user.id, None)
            return
        _complete_parsing(message, user, state, date_from_raw, date_to_raw, date_from, date_to)
        return
    state["step"] = "date_to"
    bot.clear_step_handler_by_chat_id(message.chat.id)
    msg = bot.send_message(
        message.chat.id,
        f"📅 Введите дату окончания ({DATE_FORMAT}), не раньше даты начала.",
        reply_markup=_back_markup(),
    )
    bot.register_next_step_handler(msg, _handle_parse_date_to)


def _handle_parse_date_to(message):
    state = user_states.get(message.from_user.id)
    if not state or not state.get("parse_mode") or "parse_date_from" not in state or state.get("step") != "date_to":
        bot.send_message(message.chat.id, "Состояние сброшено, начните заново.", reply_markup=_back_markup())
        user_states.pop(message.from_user.id, None)
        return
    user = _ensure_or_create_user(message.from_user.id)
    raw = (message.text or "").strip()
    if raw.lower() == "повторить" and user and user.get("last_range", {}).get("to"):
        date_to_raw = user["last_range"]["to"]
        date_to = _parse_date(date_to_raw)
    else:
        date_to_raw = raw
        date_to = _parse_date(date_to_raw)
    if not date_to:
        bot.send_message(message.chat.id, f"Неверный формат. Используйте {DATE_FORMAT}.", reply_markup=_back_markup())
        user_states.pop(message.from_user.id, None)
        return
    date_from = _parse_date(state["parse_date_from"])
    if not date_from or date_to < date_from:
        bot.send_message(message.chat.id, "Дата окончания должна быть не раньше даты начала.", reply_markup=_back_markup())
        user_states.pop(message.from_user.id, None)
        return
    if not _within_history_limit(date_to):
        bot.send_message(message.chat.id, f"Дата окончания должна быть не старше {HISTORY_LIMIT_MONTHS} месяцев.", reply_markup=_back_markup())
        user_states.pop(message.from_user.id, None)
        return
    _complete_parsing(message, user, state, state["parse_date_from"], date_to_raw, date_from, date_to)


def _complete_parsing(message, user, state, date_from_raw: str, date_to_raw: str, date_from: datetime, date_to: datetime):
    channels = user.get("channels", [])
    if not channels:
        bot.send_message(message.chat.id, "Нет настроенных каналов. Добавьте их в меню каналов.", reply_markup=_back_markup())
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
        total_found, logs = _run_telethon(
            _parse_with_telethon(message.from_user.id, channels, query, date_from, date_to, progress_cb=progress_cb)
        )
        user["last_parse"] = f"{date_from_raw} - {date_to_raw}"
        user["last_query"] = query
        user["last_range"] = {"from": date_from_raw, "to": date_to_raw}
        data = _load_storage()
        data["users"][str(message.from_user.id)] = user
        _save_storage(data)
        # Сохраняем черновик для генерации поста (обновляется при каждом парсинге)
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
        # Отправляем предварительный отчёт без материалов, но с краткой сводкой по каналам
        summary_lines = []
        for line in logs:
            if "\n" in line:
                summary_lines.append(line.split("\n", 1)[0])
            else:
                summary_lines.append(line)
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
                    "Ты помощник, который отвечает на основе материалов из Telegram-каналов. "
                    "Не выдумывай факты. Если данных недостаточно, скажи об этом. "
                    "Выбери только самую нужную информацию, не надо расписывать каждый пост."
                )
                user_prompt = (
                    f"Запрос пользователя: {query}\n"
                    f"Диапазон дат: {date_from_raw} - {date_to_raw}\n"
                    f"Собрано сообщений: {total_found}\n\n"
                    "Материалы:\n"
                    f"{context_text}\n\n"
                    "Сформируй ответ по запросу пользователя."
                )
                ai_text = openai_client.generate_answer(
                    user_prompt=user_prompt,
                    system_prompt=system_prompt,
                )
                ai_text = _truncate_text(ai_text, AI_MAX_OUTPUT_CHARS)
                if ai_text.strip():
                    _send_long_text(message.chat.id, _render_ai_html(ai_text), parse_mode="HTML")
            except Exception as e:  # noqa: BLE001
                bot.send_message(message.chat.id, f"Ошибка AI-ответа: {e}")
    except Exception as e:  # noqa: BLE001
        bot.send_message(
            message.chat.id,
            f"Ошибка парсинга: {e}",
            reply_markup=_back_markup(),
        )
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
    data["users"][str(message.from_user.id)] = user
    _save_storage(data)
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
    data["users"][str(message.from_user.id)] = user
    _save_storage(data)
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
            _send_long_text(message.chat.id, _render_ai_html(ai_text), parse_mode="HTML")
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
    data["users"][str(message.from_user.id)] = user
    _save_storage(data)
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
