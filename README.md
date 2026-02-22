# Telegram Parser Bot (pyTelegramBotAPI + Telethon)

Requirements:
- Python 3.10+
- Virtual env: `python -m venv .venv` (PowerShell activation: `.\\.venv\\Scripts\\Activate.ps1`)
- Dependencies: `pyTelegramBotAPI`, `python-dotenv`, `telethon`

Config (`.env`):
- `BOT_TOKEN=...` (BotFather token)
- `TG_API_ID=...`, `TG_API_HASH=...` (from my.telegram.org)
- `TELETHON_SESSION=...` (Telethon user string session; required to read channel history)
- Date format: `dd-mm-yyyy`
- Limits: up to 20 channels per user; topic up to 500 chars; history limited to 5 months; match by substring

Structure:
- `bot.py` — entrypoint
- `config.py` — config loader
- `handlers/`, `keyboards/`, `services/` — logic split (currently in bot.py for MVP)
- `storage/` — data storage (JSON, Telethon session file if used)

Flows:
- Start `/start` → main menu (registration, profile, parsing, add/list/edit/delete channel, set topic)
- Registration: create user record
- Topic: one topic for all channels (`Установить тему`)
- Channels: add/list/edit/delete; dates are requested only when starting parsing
- Parsing: asks date range, uses topic + channel list; results are summarized
 - Parsing: asks date range, uses topic + channel list; results are summarized

## Генерация TELETHON_SESSION

1. Убедитесь, что у вас есть `TG_API_ID` и `TG_API_HASH` (получаются на https://my.telegram.org).
2. Запустите интерактивный скрипт для генерации строки сессии (скрипт попросит `TG_API_ID` и `TG_API_HASH`, если они не заданы в окружении):

```powershell
python generate_session.py
```

3. Скрипт выведет строку в формате `TELETHON_SESSION=...`. Скопируйте значение и вставьте в файл `.env`.

4. Пример `.env` (создайте на основе `.env.example`):

```
BOT_TOKEN=ваш_бот_токен
TG_API_ID=ваш_api_id
TG_API_HASH=ваш_api_hash
TELETHON_SESSION=ваша_строка_сессии
```

5. Не храните открыто `api_hash`/`TELETHON_SESSION` в публичных репозиториях.

После этого запустите бота как в основной инструкции.
