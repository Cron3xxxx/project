import asyncio
import os
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

load_dotenv()


async def main():
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")
    force_sms = os.getenv("TG_FORCE_SMS", "0").lower() in {"1", "true", "yes", "on"}
    if not api_id:
        api_id = input("Enter TG_API_ID (from my.telegram.org): ").strip()
    if not api_hash:
        api_hash = input("Enter TG_API_HASH (from my.telegram.org): ").strip()
    try:
        api_id = int(api_id)
    except Exception:
        print("Invalid TG_API_ID. It must be a number.")
        return

    # Генерируем user-session: явно запускаем вход по номеру телефона (user account)
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        phone = input("Enter phone in international format, e.g. +79991112233: ").strip()
        if not phone:
            print("Phone is empty.")
            return
        print("Requesting login code...")
        sent = await client.send_code_request(phone, force_sms=force_sms)
        code_type = getattr(sent, "type", None)
        code_type_name = code_type.__class__.__name__ if code_type else "unknown"
        print(f"Code sent via: {code_type_name} | force_sms={force_sms}")
        print(f"phone_code_hash={sent.phone_code_hash!r}")
        if getattr(sent, "next_type", None):
            print(f"next_type={sent.next_type.__class__.__name__}")
        if getattr(sent, "timeout", None):
            print(f"timeout={sent.timeout}")
        code = input("Enter the code you received: ").strip()
        if not code:
            print("Code is empty.")
            return
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
        except SessionPasswordNeededError:
            password = input("2FA enabled. Enter your password: ").strip()
            if not password:
                print("Password is empty.")
                return
            await client.sign_in(password=password)

        session_str = client.session.save()
        print("\nСкопируйте значение правее 'TELETHON_SESSION=' и добавьте в ваш .env:\n")
        print("TELETHON_SESSION=" + session_str)

        save = input("Сохранить в файл .env в текущей папке? (y/N): ").strip().lower()
        if save == "y":
            env_path = ".env"
            # Считаем текущий .env (если есть), удалим старую строку TELETHON_SESSION и добавим новую
            lines = []
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    for ln in f:
                        if not ln.strip().startswith("TELETHON_SESSION="):
                            lines.append(ln.rstrip("\n"))
            lines.append(f"TELETHON_SESSION={session_str}")
            with open(env_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            print(f"Сохранено в {env_path}")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
