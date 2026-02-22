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
    me = await client.get_me()
    print(f"me: id={me.id} username={me.username} phone={me.phone} name={(me.first_name or '')} {(me.last_name or '')}")
    print('Список диалогов:')
    idx = 0
    async for dialog in client.iter_dialogs():
        idx += 1
        title = dialog.name
        entity = dialog.entity
        dtype = entity.__class__.__name__
        username = getattr(entity, 'username', None)
        did = getattr(entity, 'id', None)
        uname = f"@{username}" if username else ""
        print(f"{idx}. {title} {uname} ({dtype}, id={did})")
    await client.disconnect()

asyncio.run(main())
