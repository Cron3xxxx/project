from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ParsingOrchestrator:
    user_states: dict[int, dict]
    bot: Any
    date_format: str
    history_limit_months: int
    max_topic_length: int
    telethon_session: str
    get_logger: Callable[[], Any]
    reset_parse_flow: Callable[[int, int], None]
    ensure_or_create_user: Callable[[int], dict]
    telethon_credentials_ok: Callable[[], bool]
    has_user_session: Callable[[int], bool]
    parse_date: Callable[[str], Any]
    within_history_limit: Callable[[Any], bool]
    send_asset_photo: Callable[[int, str, str, Any], Any]
    edit_card_photo: Callable[[int, int, str, str, Any], bool]
    back_markup: Callable[[], Any]
    inline_menu_channels: Callable[[], Any]
    on_complete_parsing: Callable[[Any, dict, dict, str, str, Any, Any], None]
    on_handle_query: Callable[[Any], None] | None = None
    on_handle_date_from: Callable[[Any], None] | None = None
    on_handle_date_to: Callable[[Any], None] | None = None

    def set_handlers(
        self,
        *,
        handle_query: Callable[[Any], None],
        handle_date_from: Callable[[Any], None],
        handle_date_to: Callable[[Any], None],
    ) -> None:
        self.on_handle_query = handle_query
        self.on_handle_date_from = handle_date_from
        self.on_handle_date_to = handle_date_to

    def start_parsing_flow(self, user_id: int, chat_id: int, card_message_id: int | None = None) -> None:
        log = self.get_logger()
        self.reset_parse_flow(user_id, chat_id)
        if card_message_id:
            self.user_states.setdefault(user_id, {})["card_msg_id"] = card_message_id
        user = self.ensure_or_create_user(user_id)
        channels = user.get("channels", [])
        if not channels:
            self.bot.send_message(chat_id, "Нет настроенных каналов. Добавьте их в меню каналов.", reply_markup=self.inline_menu_channels())
            return
        if not self.telethon_credentials_ok():
            self.bot.send_message(
                chat_id,
                "⚠️ Не заданы Telegram API креды (TG_API_ID/TG_API_HASH). Укажите их в .env.",
                reply_markup=None,
            )
            return
        if not self.has_user_session(user_id):
            self.bot.send_message(
                chat_id,
                "⚠️ Для парсинга нужна MTProto-сессия.\n"
                "Файл сессии пользователя не найден — сначала привяжите аккаунт.",
                reply_markup=None,
            )
            return
        self.user_states[user_id] = {"parse_mode": True, "step": "query"}
        log.info("event=PARSE_START user_id=%s chat_id=%s", user_id, chat_id)
        last_query = user.get("last_query")
        prompt = "🔎 Введите запрос (например: сколько сегодня было землетрясений)."
        if last_query:
            prompt += f"\nМожно ввести 'повторить' чтобы использовать прошлый запрос: {last_query}"
        saved_card_id = self.user_states.get(user_id, {}).get("card_msg_id")
        if saved_card_id:
            self.edit_card_photo(chat_id, saved_card_id, "1.png", prompt, markup=self.back_markup())
            self.bot.clear_step_handler_by_chat_id(chat_id)
            if self.on_handle_query is not None:
                self.bot.register_next_step_handler_by_chat_id(chat_id, self.on_handle_query)
        else:
            msg = self.send_asset_photo(chat_id, "1.png", prompt, reply_markup=self.back_markup())
            if self.on_handle_query is not None:
                self.bot.register_next_step_handler(msg, self.on_handle_query)

    def handle_parse_query(self, message: Any) -> None:
        log = self.get_logger()
        user_id = message.from_user.id
        state = self.user_states.get(user_id)
        user = self.ensure_or_create_user(user_id)
        if not state or not state.get("parse_mode") or not user or state.get("step") != "query":
            self.bot.send_message(message.chat.id, "Состояние сброшено, начните заново.", reply_markup=self.back_markup())
            self.user_states.pop(user_id, None)
            return
        raw = (message.text or "").strip()
        if raw.lower() == "повторить" and user.get("last_query"):
            query = user["last_query"]
        elif raw:
            if len(raw) > self.max_topic_length:
                self.bot.send_message(
                    message.chat.id,
                    f"Запрос не должен быть длиннее {self.max_topic_length} символов.",
                    reply_markup=self.back_markup(),
                )
                self.user_states.pop(user_id, None)
                return
            query = raw
        else:
            self.bot.send_message(message.chat.id, "Пустой запрос. Попробуйте снова.", reply_markup=self.back_markup())
            self.user_states.pop(user_id, None)
            return
        log.info("event=PARSE_QUERY_ACCEPTED user_id=%s query_len=%s", user_id, len(query))
        state["query"] = query
        state["step"] = "date_from"
        last_range = user.get("last_range") or {}
        prompt = (
            f"📅 Введите дату начала ({self.date_format}), не старше {self.history_limit_months} месяцев.\n"
            "Допустимо: 02.03, 02 03, 02.03.25, 02 03 25."
        )
        if last_range.get("from"):
            prompt += (
                f"\nМожно ввести 'повторить' чтобы использовать прошлый диапазон: "
                f"{last_range.get('from')} - {last_range.get('to')}"
            )
        self.bot.clear_step_handler_by_chat_id(message.chat.id)
        msg = self.bot.send_message(message.chat.id, prompt, reply_markup=self.back_markup())
        if self.on_handle_date_from is not None:
            self.bot.register_next_step_handler(msg, self.on_handle_date_from)

    def handle_parse_date_from(self, message: Any) -> None:
        user_id = message.from_user.id
        state = self.user_states.get(user_id)
        user = self.ensure_or_create_user(user_id)
        if not state or not state.get("parse_mode") or not user or state.get("step") != "date_from":
            self.bot.send_message(message.chat.id, "Состояние сброшено, начните заново.", reply_markup=self.back_markup())
            self.user_states.pop(user_id, None)
            return
        raw = (message.text or "").strip()
        last_range = user.get("last_range", {})
        if raw.lower() == "повторить" and user.get("last_range", {}).get("from"):
            date_from_raw = last_range.get("from")
            date_from = self.parse_date(date_from_raw)
        else:
            date_from_raw = raw
            date_from = self.parse_date(date_from_raw)
        if not date_from:
            self.bot.send_message(
                message.chat.id,
                f"Неверный формат. Используйте {self.date_format} или варианты: 02.03, 02 03, 02.03.25, 02 03 25.",
                reply_markup=self.back_markup(),
            )
            self.user_states.pop(user_id, None)
            return
        if not self.within_history_limit(date_from):
            self.bot.send_message(
                message.chat.id,
                f"Дата начала должна быть не старше {self.history_limit_months} месяцев.",
                reply_markup=self.back_markup(),
            )
            self.user_states.pop(user_id, None)
            return
        date_from_canonical = date_from.strftime(self.date_format)
        state["parse_date_from"] = date_from_canonical
        if raw.lower() == "повторить" and last_range.get("to"):
            date_to_raw = last_range["to"]
            date_to = self.parse_date(date_to_raw)
            if not date_to:
                self.bot.send_message(message.chat.id, "Неверный сохраненный конец диапазона. Введите заново.", reply_markup=self.back_markup())
                self.user_states.pop(user_id, None)
                return
            if date_to < date_from:
                self.bot.send_message(message.chat.id, "Сохраненная дата окончания раньше даты начала. Введите заново.", reply_markup=self.back_markup())
                self.user_states.pop(user_id, None)
                return
            if not self.within_history_limit(date_to):
                self.bot.send_message(
                    message.chat.id,
                    f"Дата окончания должна быть не старше {self.history_limit_months} месяцев.",
                    reply_markup=self.back_markup(),
                )
                self.user_states.pop(user_id, None)
                return
            self.on_complete_parsing(
                message,
                user,
                state,
                date_from_canonical,
                date_to.strftime(self.date_format),
                date_from,
                date_to,
            )
            return
        state["step"] = "date_to"
        self.bot.clear_step_handler_by_chat_id(message.chat.id)
        msg = self.bot.send_message(
            message.chat.id,
            f"📅 Введите дату окончания ({self.date_format}), не раньше даты начала.\n"
            "Допустимо: 02.03, 02 03, 02.03.25, 02 03 25.",
            reply_markup=self.back_markup(),
        )
        if self.on_handle_date_to is not None:
            self.bot.register_next_step_handler(msg, self.on_handle_date_to)

    def handle_parse_date_to(self, message: Any) -> None:
        user_id = message.from_user.id
        state = self.user_states.get(user_id)
        if not state or not state.get("parse_mode") or "parse_date_from" not in state or state.get("step") != "date_to":
            self.bot.send_message(message.chat.id, "Состояние сброшено, начните заново.", reply_markup=self.back_markup())
            self.user_states.pop(user_id, None)
            return
        user = self.ensure_or_create_user(user_id)
        raw = (message.text or "").strip()
        if raw.lower() == "повторить" and user and user.get("last_range", {}).get("to"):
            date_to_raw = user["last_range"]["to"]
            date_to = self.parse_date(date_to_raw)
        else:
            date_to_raw = raw
            date_to = self.parse_date(date_to_raw)
        if not date_to:
            self.bot.send_message(
                message.chat.id,
                f"Неверный формат. Используйте {self.date_format} или варианты: 02.03, 02 03, 02.03.25, 02 03 25.",
                reply_markup=self.back_markup(),
            )
            self.user_states.pop(user_id, None)
            return
        date_from = self.parse_date(state["parse_date_from"])
        if not date_from or date_to < date_from:
            self.bot.send_message(message.chat.id, "Дата окончания должна быть не раньше даты начала.", reply_markup=self.back_markup())
            self.user_states.pop(user_id, None)
            return
        if not self.within_history_limit(date_to):
            self.bot.send_message(
                message.chat.id,
                f"Дата окончания должна быть не старше {self.history_limit_months} месяцев.",
                reply_markup=self.back_markup(),
            )
            self.user_states.pop(user_id, None)
            return
        self.on_complete_parsing(
            message,
            user,
            state,
            state["parse_date_from"],
            date_to.strftime(self.date_format),
            date_from,
            date_to,
        )
