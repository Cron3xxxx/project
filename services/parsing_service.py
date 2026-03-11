from __future__ import annotations

from datetime import datetime, timedelta

from telethon import TelegramClient
from telethon.errors import RPCError
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest


def matches_query(text: str, query: str) -> bool:
    if not text or not query:
        return False
    return query.lower() in text.lower()


def normalize_channel(channel: str) -> str:
    ch = channel.strip()
    if ch.startswith("https://t.me/") or ch.startswith("http://t.me/") or ch.startswith("t.me/"):
        ch = ch.split("t.me/")[1]
    if not ch.startswith("@"):
        ch = "@" + ch
    return ch


def truncate_text(text: str, max_len: int) -> str:
    if max_len <= 0 or len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


async def parse_with_telethon(
    *,
    api_id: int,
    api_hash: str,
    session_path: str,
    fallback_session: str | None,
    channels: list[dict],
    query: str,
    date_from: datetime,
    date_to: datetime,
    ai_max_messages: int,
    ai_max_message_chars: int,
    progress_cb=None,
) -> tuple[int, list[str]]:
    if session_path:
        client = TelegramClient(session_path, api_id, api_hash)
    else:
        if not fallback_session:
            raise RuntimeError("Нет файла сессии пользователя и не задан TELETHON_SESSION.")
        client = TelegramClient(StringSession(fallback_session), api_id, api_hash)

    await client.start()
    total = 0
    logs: list[str] = []
    total_collected = 0
    limit_reached = False
    total_channels = len(channels)
    processed = 0

    for ch in channels:
        name = normalize_channel(ch.get("channel", ""))
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
            try:
                await client(JoinChannelRequest(entity))
                joined = True
            except Exception:
                pass

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
                if text.strip() and matches_query(text, query):
                    found += 1
                    total_collected += 1
                    if len(samples) < 50:
                        clean = truncate_text(text.replace("\n", " "), ai_max_message_chars)
                        if entity_username and msg.id:
                            link = f"https://t.me/{entity_username}/{msg.id}"
                        else:
                            link = "n/a"
                        header = f"[{msg_date}] {name} | {link}"
                        samples.append(f"{header}\n{clean}")
                    if total_collected >= ai_max_messages:
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
            logs.append(f"Достигнут лимит сообщений: {ai_max_messages}. Остальные каналы пропущены.")
            break

    await client.disconnect()
    return total, logs
