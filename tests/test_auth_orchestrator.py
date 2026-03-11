import asyncio
import unittest
from types import SimpleNamespace

from services.auth_orchestrator import AuthOrchestrator
from services.user_serializer import UserOperationSerializer


class SessionPasswordNeededError(Exception):
    pass


class PhoneCodeExpiredError(Exception):
    pass


class PhoneCodeInvalidError(Exception):
    pass


class PasswordHashInvalidError(Exception):
    pass


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple] = []

    def info(self, *args) -> None:
        self.records.append(args)

    def warning(self, *args) -> None:
        self.records.append(args)


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple] = []
        self.handlers: list = []
        self._message_id = 0

    def send_message(self, chat_id, text, reply_markup=None):
        self._message_id += 1
        self.messages.append((chat_id, text, reply_markup))
        return SimpleNamespace(message_id=self._message_id, chat=SimpleNamespace(id=chat_id))

    def register_next_step_handler(self, msg, handler) -> None:
        self.handlers.append((msg.message_id, handler))


def make_message(user_id: int, chat_id: int, text: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=user_id),
        chat=SimpleNamespace(id=chat_id),
    )


class AuthOrchestratorTests(unittest.TestCase):
    def _build(self):
        user_states: dict[int, dict] = {}
        session_cache: dict[int, tuple[float, bool]] = {}
        bot = FakeBot()
        logger = FakeLogger()
        stats = {
            "reset_calls": [],
            "delete_calls": [],
            "refresh_calls": [],
            "send_meta": {"phone_code_hash": "hash", "code_type_name": "SentCodeTypeApp", "timeout": 0},
            "login_mode": "success",
            "password_mode": "success",
        }

        async def send_login_code(_user_id: int, _phone: str):
            return stats["send_meta"]

        async def complete_login(_user_id: int, _phone: str, _code: str, phone_code_hash=None):
            if stats["login_mode"] == "2fa":
                raise SessionPasswordNeededError()
            if stats["login_mode"] == "invalid":
                raise PhoneCodeInvalidError()
            if stats["login_mode"] == "error":
                raise RuntimeError("login failed")
            return phone_code_hash

        async def complete_2fa(_user_id: int, _password: str):
            if stats["password_mode"] == "invalid":
                raise PasswordHashInvalidError()
            if stats["password_mode"] == "error":
                raise RuntimeError("2fa failed")

        def run_telethon(coro):
            return asyncio.run(coro)

        def reset_link_flow(user_id: int) -> None:
            stats["reset_calls"].append(user_id)
            user_states.pop(user_id, None)

        def delete_user_session_file(user_id: int, reason: str) -> None:
            stats["delete_calls"].append((user_id, reason))

        def refresh_main_card(user_id: int, chat_id: int) -> None:
            stats["refresh_calls"].append((user_id, chat_id))

        def register_auth_failure(state: dict) -> None:
            state["auth_attempts"] = int(state.get("auth_attempts", 0)) + 1

        def clear_auth_failures(state: dict) -> None:
            state.pop("auth_attempts", None)
            state.pop("lock_until", None)

        def auth_locked(state: dict) -> int:
            return int(state.get("forced_lock", 0))

        def apply_sent_code_meta(state: dict, meta: dict) -> None:
            state["phone_code_hash"] = meta.get("phone_code_hash")
            state["code_type_name"] = meta.get("code_type_name")

        orch = AuthOrchestrator(
            user_states=user_states,
            session_auth_cache=session_cache,
            code_resend_cooldown=60,
            bot=bot,
            get_auth_logger=lambda: logger,
            telethon_credentials_ok=lambda: True,
            has_user_session=lambda _user_id: False,
            auth_locked=auth_locked,
            register_auth_failure=register_auth_failure,
            clear_auth_failures=clear_auth_failures,
            normalize_phone=lambda raw: "+79991112233" if raw else None,
            mask_phone=lambda phone: phone,
            run_telethon=run_telethon,
            send_login_code=send_login_code,
            complete_login=complete_login,
            complete_2fa=complete_2fa,
            reset_link_flow=reset_link_flow,
            delete_user_session_file=delete_user_session_file,
            refresh_main_card=refresh_main_card,
            extract_digits_code=lambda raw: "12345" if raw else None,
            apply_sent_code_meta=apply_sent_code_meta,
            code_resend_wait=lambda _state, cooldown_seconds: 0,
            delivery_hint=lambda _type_name: "в приложении Telegram",
            session_password_needed_error=SessionPasswordNeededError,
            phone_code_expired_error=PhoneCodeExpiredError,
            phone_code_invalid_error=PhoneCodeInvalidError,
            password_hash_invalid_error=PasswordHashInvalidError,
            reply_keyboard_remove_factory=lambda: "remove",
            now_ts=lambda: 100.0,
            serializer=UserOperationSerializer(),
        )
        return orch, user_states, session_cache, bot, stats

    def test_start_link_flow_sets_state_and_handler(self) -> None:
        orch, user_states, _, bot, _ = self._build()

        def phone_handler(_message):
            return None

        orch.set_handlers(handle_phone=phone_handler, handle_code=lambda m: None, handle_password=lambda m: None)
        orch.start_link_flow(make_message(1, 101))

        self.assertTrue(user_states[1]["link_mode"])
        self.assertEqual(len(bot.handlers), 1)
        self.assertIs(bot.handlers[0][1], phone_handler)

    def test_handle_link_phone_success_sets_code_state(self) -> None:
        orch, user_states, _, bot, _ = self._build()
        user_states[1] = {"link_mode": True}

        def code_handler(_message):
            return None

        orch.set_handlers(handle_phone=lambda m: None, handle_code=code_handler, handle_password=lambda m: None)
        orch.handle_link_phone(make_message(1, 101, text="+7999"))

        self.assertEqual(user_states[1]["phone"], "+79991112233")
        self.assertEqual(user_states[1]["phone_code_hash"], "hash")
        self.assertEqual(len(bot.handlers), 1)
        self.assertIs(bot.handlers[0][1], code_handler)

    def test_handle_link_code_success_updates_cache_and_resets(self) -> None:
        orch, user_states, session_cache, _, stats = self._build()
        user_states[1] = {"link_mode": True, "phone": "+79991112233", "phone_code_hash": "hash"}
        orch.handle_link_code(make_message(1, 101, text="1a2b3c4d5"))

        self.assertEqual(session_cache[1], (100.0, True))
        self.assertIn(1, stats["reset_calls"])
        self.assertIn((1, 101), stats["refresh_calls"])

    def test_handle_link_code_2fa_requests_password(self) -> None:
        orch, user_states, _, bot, stats = self._build()
        stats["login_mode"] = "2fa"
        user_states[1] = {"link_mode": True, "phone": "+79991112233", "phone_code_hash": "hash"}

        def password_handler(_message):
            return None

        orch.set_handlers(handle_phone=lambda m: None, handle_code=lambda m: None, handle_password=password_handler)
        orch.handle_link_code(make_message(1, 101, text="12345"))

        self.assertEqual(user_states[1]["code"], "12345")
        self.assertEqual(len(bot.handlers), 1)
        self.assertIs(bot.handlers[0][1], password_handler)

    def test_handle_link_password_invalid_reprompts(self) -> None:
        orch, user_states, _, bot, stats = self._build()
        stats["password_mode"] = "invalid"
        user_states[1] = {"link_mode": True, "phone": "+79991112233", "code": "12345"}

        def password_handler(_message):
            return None

        orch.set_handlers(handle_phone=lambda m: None, handle_code=lambda m: None, handle_password=password_handler)
        orch.handle_link_password(make_message(1, 101, text="bad"))

        self.assertEqual(user_states[1]["auth_attempts"], 1)
        self.assertEqual(len(bot.handlers), 1)
        self.assertIs(bot.handlers[0][1], password_handler)


if __name__ == "__main__":
    unittest.main()
