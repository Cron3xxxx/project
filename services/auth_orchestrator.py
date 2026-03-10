from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class AuthOrchestrator:
    user_states: dict[int, dict]
    session_auth_cache: dict[int, tuple[float, bool]]
    code_resend_cooldown: int
    bot: Any
    get_auth_logger: Callable[[], Any]
    telethon_credentials_ok: Callable[[], bool]
    has_user_session: Callable[[int], bool]
    auth_locked: Callable[[dict], int]
    register_auth_failure: Callable[[dict], None]
    clear_auth_failures: Callable[[dict], None]
    normalize_phone: Callable[[str], str | None]
    mask_phone: Callable[[str], str]
    run_telethon: Callable[[Any], Any]
    send_login_code: Callable[[int, str], Any]
    complete_login: Callable[..., Any]
    complete_2fa: Callable[[int, str], Any]
    reset_link_flow: Callable[[int], None]
    delete_user_session_file: Callable[[int, str], None]
    refresh_main_card: Callable[[int, int], None]
    extract_digits_code: Callable[[str], str | None]
    apply_sent_code_meta: Callable[[dict, dict], None]
    code_resend_wait: Callable[..., int]
    delivery_hint: Callable[[str], str]
    session_password_needed_error: type[Exception]
    phone_code_expired_error: type[Exception]
    phone_code_invalid_error: type[Exception]
    password_hash_invalid_error: type[Exception]
    reply_keyboard_remove_factory: Callable[[], Any]
    now_ts: Callable[[], float] = time.time
    on_handle_phone: Callable[[Any], None] | None = None
    on_handle_code: Callable[[Any], None] | None = None
    on_handle_password: Callable[[Any], None] | None = None

    def set_handlers(
        self,
        *,
        handle_phone: Callable[[Any], None],
        handle_code: Callable[[Any], None],
        handle_password: Callable[[Any], None],
    ) -> None:
        self.on_handle_phone = handle_phone
        self.on_handle_code = handle_code
        self.on_handle_password = handle_password

    def _send_link_success(self, chat_id: int) -> None:
        try:
            self.bot.send_message(
                chat_id,
                "Аккаунт успешно привязан.",
                reply_markup=self.reply_keyboard_remove_factory(),
            )
        except Exception:
            self.bot.send_message(chat_id, "Аккаунт успешно привязан.", reply_markup=None)

    def start_link_flow(self, message: Any) -> None:
        auth_log = self.get_auth_logger()
        user_id = message.from_user.id
        if not self.telethon_credentials_ok():
            self.bot.send_message(
                message.chat.id,
                "Не заданы Telegram API креды (TG_API_ID/TG_API_HASH). Укажите их в .env.",
                reply_markup=None,
            )
            return
        if self.has_user_session(user_id):
            self.bot.send_message(message.chat.id, "Аккаунт уже привязан.")
            return
        if self.user_states.get(user_id, {}).get("link_mode"):
            self.bot.send_message(message.chat.id, "Привязка уже начата. Следуйте предыдущим шагам.")
            return
        self.user_states[user_id] = {"link_mode": True}
        auth_log.info("event=AUTH_LINK_START user_id=%s", user_id)
        msg = self.bot.send_message(message.chat.id, "Введите номер телефона в формате +79991112233.")
        if self.on_handle_phone is not None:
            self.bot.register_next_step_handler(msg, self.on_handle_phone)

    def handle_link_phone(self, message: Any) -> None:
        auth_log = self.get_auth_logger()
        user_id = message.from_user.id
        state = self.user_states.get(user_id)
        auth_log.info("event=AUTH_PHONE_INPUT user_id=%s", user_id)
        if not state or not state.get("link_mode"):
            self.bot.send_message(message.chat.id, "Состояние сброшено, начните заново.")
            self.user_states.pop(user_id, None)
            return

        remaining = self.auth_locked(state)
        if remaining:
            auth_log.warning("event=AUTH_LOCKED user_id=%s remaining=%s", user_id, remaining)
            self.bot.send_message(message.chat.id, f"Превышен лимит попыток. Повторите через {remaining} сек.")
            return

        wait = self.code_resend_wait(state, cooldown_seconds=self.code_resend_cooldown)
        if wait:
            auth_log.info("event=AUTH_CODE_RESEND_BLOCKED user_id=%s wait=%s", user_id, wait)
            self.bot.send_message(message.chat.id, f"Код уже отправлен. Повторите через {wait} сек.")
            return

        phone = self.normalize_phone(message.text or "")
        if not phone:
            auth_log.warning("event=AUTH_PHONE_INVALID user_id=%s", user_id)
            self.bot.send_message(message.chat.id, "Неверный формат телефона. Пример: +79991112233")
            self.user_states.pop(user_id, None)
            return

        auth_log.info("event=AUTH_PHONE_ACCEPTED user_id=%s phone=%s", user_id, self.mask_phone(phone))
        try:
            send_meta = self.run_telethon(self.send_login_code(user_id, phone))
        except Exception as exc:  # noqa: BLE001
            self.register_auth_failure(state)
            remaining = self.auth_locked(state)
            auth_log.warning("event=AUTH_CODE_SEND_FAILED user_id=%s phone=%s error=%s", user_id, self.mask_phone(phone), exc)
            self.bot.send_message(message.chat.id, f"Ошибка отправки кода: {exc}")
            if remaining:
                self.bot.send_message(message.chat.id, f"Лимит попыток исчерпан. Повторите через {remaining} сек.")
            self.delete_user_session_file(user_id, "code_send_failed")
            self.reset_link_flow(user_id)
            return

        state["phone"] = phone
        self.apply_sent_code_meta(state, send_meta)
        auth_log.info("event=AUTH_CODE_SENT user_id=%s phone=%s", user_id, self.mask_phone(phone))
        delivery = self.delivery_hint(state.get("code_type_name") or "unknown")
        timeout = send_meta.get("timeout")
        timeout_hint = f"\nОжидайте {timeout} сек перед повторной попыткой." if isinstance(timeout, int) and timeout > 0 else ""
        msg = self.bot.send_message(
            message.chat.id,
            "Код отправлен.\n"
            f"Канал доставки: {delivery}.\n"
            "Введите код в любом формате, например: 1a2b3c4d5 (бот извлечет цифры)."
            f"{timeout_hint}",
        )
        if self.on_handle_code is not None:
            self.bot.register_next_step_handler(msg, self.on_handle_code)

    def handle_link_code(self, message: Any) -> None:
        auth_log = self.get_auth_logger()
        user_id = message.from_user.id
        state = self.user_states.get(user_id)
        if not state or not state.get("link_mode") or "phone" not in state:
            self.bot.send_message(message.chat.id, "Состояние сброшено, начните заново.")
            self.delete_user_session_file(user_id, "link_state_missing_code")
            self.reset_link_flow(user_id)
            return

        remaining = self.auth_locked(state)
        if remaining:
            self.bot.send_message(message.chat.id, f"Превышен лимит попыток. Повторите через {remaining} сек.")
            return

        code = self.extract_digits_code((message.text or "").strip())
        if not code:
            self.bot.send_message(
                message.chat.id,
                "Код не распознан. Введите его с любыми буквами между цифрами, например: 1a2b3c4d5",
                reply_markup=None,
            )
            self.delete_user_session_file(user_id, "code_too_short")
            self.reset_link_flow(user_id)
            return

        try:
            self.run_telethon(
                self.complete_login(
                    user_id,
                    state["phone"],
                    code,
                    phone_code_hash=state.get("phone_code_hash"),
                )
            )
            self.clear_auth_failures(state)
            self.session_auth_cache[user_id] = (self.now_ts(), True)
            self.reset_link_flow(user_id)
            self.refresh_main_card(user_id, message.chat.id)
            self._send_link_success(message.chat.id)
            auth_log.info("event=AUTH_LINK_SUCCESS user_id=%s phone=%s", user_id, self.mask_phone(state["phone"]))
            return
        except self.session_password_needed_error:
            state["code"] = code
            auth_log.info("event=AUTH_2FA_REQUIRED user_id=%s phone=%s", user_id, self.mask_phone(state["phone"]))
            msg = self.bot.send_message(message.chat.id, "Включена 2FA. Введите пароль от аккаунта.")
            if self.on_handle_password is not None:
                self.bot.register_next_step_handler(msg, self.on_handle_password)
            return
        except (self.phone_code_expired_error, self.phone_code_invalid_error) as exc:
            auth_log.warning("event=AUTH_CODE_INVALID user_id=%s phone=%s error=%s", user_id, self.mask_phone(state["phone"]), exc)
            self.delete_user_session_file(user_id, "code_invalid")
            try:
                send_meta = self.run_telethon(self.send_login_code(user_id, state["phone"]))
                self.apply_sent_code_meta(state, send_meta)
                delivery = self.delivery_hint(state.get("code_type_name") or "unknown")
                msg = self.bot.send_message(
                    message.chat.id,
                    "Код недействителен/истек. Отправлен новый код.\n"
                    f"Канал доставки: {delivery}.\n"
                    "Введите код в формате 1a2b3c4d5.",
                )
                if self.on_handle_code is not None:
                    self.bot.register_next_step_handler(msg, self.on_handle_code)
                return
            except Exception as send_err:  # noqa: BLE001
                self.register_auth_failure(state)
                remaining = self.auth_locked(state)
                auth_log.warning(
                    "event=AUTH_CODE_RESEND_FAILED user_id=%s phone=%s error=%s",
                    user_id,
                    self.mask_phone(state["phone"]),
                    send_err,
                )
                self.bot.send_message(message.chat.id, f"Ошибка отправки нового кода: {send_err}")
                if remaining:
                    self.bot.send_message(message.chat.id, f"Лимит попыток исчерпан. Повторите через {remaining} сек.")
                self.delete_user_session_file(user_id, "code_resend_failed")
                self.reset_link_flow(user_id)
                return
        except Exception as exc:  # noqa: BLE001
            self.register_auth_failure(state)
            remaining = self.auth_locked(state)
            auth_log.warning("event=AUTH_CODE_VERIFY_FAILED user_id=%s phone=%s error=%s", user_id, self.mask_phone(state["phone"]), exc)
            self.bot.send_message(message.chat.id, f"Ошибка входа: {exc}")
            if remaining:
                self.bot.send_message(message.chat.id, f"Лимит попыток исчерпан. Повторите через {remaining} сек.")
            self.delete_user_session_file(user_id, "code_verify_failed")
            self.reset_link_flow(user_id)

    def handle_link_password(self, message: Any) -> None:
        auth_log = self.get_auth_logger()
        user_id = message.from_user.id
        state = self.user_states.get(user_id)
        if not state or not state.get("link_mode") or "phone" not in state or "code" not in state:
            self.bot.send_message(message.chat.id, "Состояние сброшено, начните заново.")
            self.delete_user_session_file(user_id, "link_state_missing_2fa")
            self.reset_link_flow(user_id)
            return
        remaining = self.auth_locked(state)
        if remaining:
            self.bot.send_message(message.chat.id, f"Превышен лимит попыток. Повторите через {remaining} сек.")
            return
        password = (message.text or "").strip()
        if not password:
            self.bot.send_message(message.chat.id, "Пароль не может быть пустым.")
            self.delete_user_session_file(user_id, "2fa_empty_password")
            self.reset_link_flow(user_id)
            return
        try:
            self.run_telethon(self.complete_2fa(user_id, password))
            self.clear_auth_failures(state)
            self.session_auth_cache[user_id] = (self.now_ts(), True)
            self.reset_link_flow(user_id)
            self.refresh_main_card(user_id, message.chat.id)
            self._send_link_success(message.chat.id)
            auth_log.info("event=AUTH_LINK_SUCCESS_2FA user_id=%s phone=%s", user_id, self.mask_phone(state["phone"]))
        except self.password_hash_invalid_error:
            self.register_auth_failure(state)
            remaining = self.auth_locked(state)
            auth_log.warning("event=AUTH_2FA_INVALID user_id=%s phone=%s", user_id, self.mask_phone(state["phone"]))
            self.bot.send_message(message.chat.id, "Неверный пароль 2FA. Попробуйте ещё раз.")
            if remaining:
                self.bot.send_message(message.chat.id, f"Лимит попыток исчерпан. Повторите через {remaining} сек.")
                self.delete_user_session_file(user_id, "2fa_invalid_locked")
                self.reset_link_flow(user_id)
            else:
                msg = self.bot.send_message(message.chat.id, "Введите пароль от аккаунта.")
                if self.on_handle_password is not None:
                    self.bot.register_next_step_handler(msg, self.on_handle_password)
            return
        except Exception as exc:  # noqa: BLE001
            self.register_auth_failure(state)
            remaining = self.auth_locked(state)
            auth_log.warning("event=AUTH_2FA_FAILED user_id=%s phone=%s error=%s", user_id, self.mask_phone(state["phone"]), exc)
            self.bot.send_message(message.chat.id, f"Ошибка входа: {exc}")
            if remaining:
                self.bot.send_message(message.chat.id, f"Лимит попыток исчерпан. Повторите через {remaining} сек.")
            self.delete_user_session_file(user_id, "2fa_failed")
            self.reset_link_flow(user_id)
