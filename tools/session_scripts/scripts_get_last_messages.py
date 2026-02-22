import os
import asyncio
import sys
from telethon import TelegramClient
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)

load_dotenv()

API_ID = int(os.getenv('TG_API_ID') or '0')
API_HASH = os.getenv('TG_API_HASH')
SESSIONS_DIR = os.path.join(PROJECT_DIR, 'storage', 'sessions')
TARGET_USER_ID = 1173240636
SESSION_USER_ID = 1562297545
LIMIT = 20

if not API_ID or not API_HASH:
    raise SystemExit('TG_API_ID/TG_API_HASH не заданы в .env')

session_path = os.path.join(SESSIONS_DIR, f"{SESSION_USER_ID}.session")
if not os.path.exists(session_path):
    raise SystemExit(f"Сессия не найдена: {session_path}")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

async def main():
    client = TelegramClient(session_path, API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        print('Сессия не авторизована')
        await client.disconnect()
        return

    entity = await client.get_entity(TARGET_USER_ID)
    print(f"Чат: {getattr(entity, 'first_name', '')} {getattr(entity, 'last_name', '')} @{getattr(entity, 'username', '')} (id={entity.id})")
    print(f"Последние {LIMIT} сообщений:")

    messages = []
    async for msg in client.iter_messages(entity, limit=LIMIT):
        dt = msg.date.strftime('%Y-%m-%d %H:%M') if msg.date else 'unknown'
        sender = 'Я' if msg.out else 'Он/Она'
        text = msg.message or ''
        messages.append(f"[{dt}] {sender}: {text}")

    for line in reversed(messages):
        print(line)

    await client.disconnect()

asyncio.run(main())
