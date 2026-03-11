import os
from dotenv import load_dotenv


load_dotenv()


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
TG_API_ID = os.getenv("TG_API_ID")
TG_API_HASH = os.getenv("TG_API_HASH")
TELETHON_SESSION = os.getenv("TELETHON_SESSION")
TG_FORCE_SMS = os.getenv("TG_FORCE_SMS", "0").lower() in {"1", "true", "yes", "on"}

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_TIMEOUT_SECONDS = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "30"))

# AI ответчик
AI_ENABLED = os.getenv("AI_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
AI_MAX_INPUT_CHARS = int(os.getenv("AI_MAX_INPUT_CHARS", "40000"))
AI_MAX_OUTPUT_CHARS = int(os.getenv("AI_MAX_OUTPUT_CHARS", "4000"))
AI_MAX_MESSAGES = int(os.getenv("AI_MAX_MESSAGES", "500"))
AI_MAX_MESSAGE_CHARS = int(os.getenv("AI_MAX_MESSAGE_CHARS", "1000"))
