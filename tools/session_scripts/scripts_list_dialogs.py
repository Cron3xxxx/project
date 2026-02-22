import os
import asyncio
import sys
from telethon import TelegramClient
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv()

API_ID = int(os.getenv('TG_API_ID') or '0')
API_HASH = os.getenv('TG_API_HASH')
SESSIONS_DIR = os.path.join(os.path.dirname(BASE_DIR), 'storage', 'sessions')

if not API_ID or not API_HASH:
    raise SystemExit('TG_API_ID/TG_API_HASH не заданы в .env')

sessions = [f for f in os.listdir(SESSIONS_DIR) if f.endswith('.session')]
if not sessions:
    raise SystemExit('Нет .session в storage/sessions')

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


async def main():
    for fname in sessions:
        path = os.path.join(SESSIONS_DIR, fname)
        print(f"\n=== {fname} ===")
        client = TelegramClient(path, API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            print('Сессия не авторизована')
            await client.disconnect()
            continue
        me = await client.get_me()
        print(f"me: id={me.id} username={me.username} phone={me.phone} name={(me.first_name or '')} {(me.last_name or '')}")
        print('Диалоги (первые 15):')
        count = 0
        async for dialog in client.iter_dialogs():
            title = dialog.name
            entity = dialog.entity
            dtype = entity.__class__.__name__
            print(f"- {title} ({dtype})")
            count += 1
            if count >= 15:
                break
        await client.disconnect()

asyncio.run(main())
