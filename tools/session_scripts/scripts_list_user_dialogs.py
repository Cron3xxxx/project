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
TARGET_ID = 1562297545
OUT_PATH = os.path.join(BASE_DIR, 'dialogs_1562297545_users.txt')

if not API_ID or not API_HASH:
    raise SystemExit('TG_API_ID/TG_API_HASH не заданы в .env')

session_path = os.path.join(SESSIONS_DIR, f"{TARGET_ID}.session")
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

    lines = []
    idx = 0
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if entity.__class__.__name__ != 'User':
            continue
        idx += 1
        title = dialog.name
        username = getattr(entity, 'username', None)
        did = getattr(entity, 'id', None)
        uname = f"@{username}" if username else ""
        lines.append(f"{idx}. {title} {uname} (User, id={did})")

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"Личных чатов: {idx}")
    print(f"Файл: {OUT_PATH}")
    print('Первые 30:')
    for line in lines[:30]:
        print(line)

    await client.disconnect()

asyncio.run(main())
