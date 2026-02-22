import os

from dotenv import load_dotenv
from telebot import TeleBot


def main():
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN не задан в .env")
    bot = TeleBot(token)
    bot.remove_webhook()
    print("Webhook удалён.")


if __name__ == "__main__":
    main()
