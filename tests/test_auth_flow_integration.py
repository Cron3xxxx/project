import asyncio
import unittest
from types import SimpleNamespace

from services.auth_flow import (
    apply_sent_code_meta,
    auth_locked,
    clear_auth_failures,
    code_resend_wait,
    mask_phone,
    normalize_phone,
    register_auth_failure,
)
from services.auth_orchestrator import AuthOrchestrator
from services.user_serializer import UserOperationSerializer
from services.auth_utils import delivery_hint, extract_digits_code


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
        self.events: list[tuple] = []

    def info(self, *args) -> None:
        self.events.append(args)

    def warning(self, *args) -> None:
        self.events.append(args)


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple] = []
        self.handlers: list = []
        self._message_id = 0

    def send_message(self, chat_id, text, reply_markup=None):
        self._message_id += 1
        self.sent.append((chat_id, text, reply_markup))
        return SimpleNamespace(message_id=self._message_id, chat=SimpleNamespace(id=chat_id))

    def register_next_step_handler(self, msg, handler) -> None:
        self.handlers.append((msg.message_id, handler))


def make_message(user_id: int, chat_id: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=user_id),
        chat=SimpleNamespace(id=chat_id),
    )


class AuthFlowIntegrationTests(unittest.TestCase):
    def _make_orchestrator(self):
        user_states: dict[int, dict] = {}
        session_cache: dict[int, tuple[float, bool]] = {}
        logger = FakeLogger()
        bot = FakeBot()
        state = {"mode": "success", "send_meta": {"phone_code_hash": "h-1", "code_type_name": "SentCodeTypeApp", "timeout": 0}}
        stats = {"reset": [], "deleted": [], "refresh": []}

        async def send_login_code(_user_id: int, _phone: str):
            return state["send_meta"]

        async def complete_login(_user_id: int, _phone: str, _code: str, phone_code_hash=None):
            if state["mode"] == "2fa":
                raise SessionPasswordNeededError()
            if state["mode"] == "invalid":
                raise PhoneCodeInvalidError()
            if state["mode"] == "error":
                raise RuntimeError("login failed")
            return phone_code_hash

        async def complete_2fa(_user_id: int, _password: str):
            if state["mode"] == "2fa_bad_pwd":
                raise PasswordHashInvalidError()
            if state["mode"] == "2fa_error":
                raise RuntimeError("2fa failed")

        def run_telethon(coro):
            return asyncio.run(coro)

        def reset_link_flow(user_id: int) -> None:
            stats["reset"].append(user_id)
            user_states.pop(user_id, None)

        def delete_user_session_file(user_id: int, reason: str) -> None:
            stats["deleted"].append((user_id, reason))

        def refresh_main_card(user_id: int, chat_id: int) -> None:
            stats["refresh"].append((user_id, chat_id))

        def reg_fail(s: dict) -> None:
            register_auth_failure(s, max_auth_attempts=5, auth_lock_seconds=600, now_ts=100)

        orch = AuthOrchestrator(
            user_states=user_states,
            session_auth_cache=session_cache,
            code_resend_cooldown=60,
            bot=bot,
            get_auth_logger=lambda: logger,
            telethon_credentials_ok=lambda: True,
            has_user_session=lambda _uid: False,
            auth_locked=lambda s: auth_locked(s, now_ts=100),
            register_auth_failure=reg_fail,
            clear_auth_failures=clear_auth_failures,
            normalize_phone=normalize_phone,
            mask_phone=mask_phone,
            run_telethon=run_telethon,
            send_login_code=send_login_code,
            complete_login=complete_login,
            complete_2fa=complete_2fa,
            reset_link_flow=reset_link_flow,
            delete_user_session_file=delete_user_session_file,
            refresh_main_card=refresh_main_card,
            extract_digits_code=extract_digits_code,
            apply_sent_code_meta=lambda st, meta: apply_sent_code_meta(st, meta, now_ts=100),
            code_resend_wait=lambda st, cooldown_seconds: code_resend_wait(st, cooldown_seconds=cooldown_seconds, now_ts=100),
            delivery_hint=delivery_hint,
            session_password_needed_error=SessionPasswordNeededError,
            phone_code_expired_error=PhoneCodeExpiredError,
            phone_code_invalid_error=PhoneCodeInvalidError,
            password_hash_invalid_error=PasswordHashInvalidError,
            reply_keyboard_remove_factory=lambda: "remove",
            now_ts=lambda: 100.0,
            serializer=UserOperationSerializer(),
        )
        orch.set_handlers(
            handle_phone=lambda m: orch.handle_link_phone(m),
            handle_code=lambda m: orch.handle_link_code(m),
            handle_password=lambda m: orch.handle_link_password(m),
        )
        return orch, user_states, session_cache, bot, state, stats

    def test_full_success_flow(self) -> None:
        orch, user_states, cache, _, _, stats = self._make_orchestrator()
        orch.start_link_flow(make_message(1, 10, ""))
        orch.handle_link_phone(make_message(1, 10, "+79991112233"))
        orch.handle_link_code(make_message(1, 10, "1a2b3c4d5"))

        self.assertEqual(cache[1], (100.0, True))
        self.assertIn((1, 10), stats["refresh"])
        self.assertIn(1, stats["reset"])
        self.assertNotIn(1, user_states)

    def test_invalid_code_resend_keeps_flow(self) -> None:
        orch, user_states, _, _, runtime, _ = self._make_orchestrator()
        runtime["mode"] = "invalid"
        orch.start_link_flow(make_message(2, 20, ""))
        orch.handle_link_phone(make_message(2, 20, "+79991112233"))
        orch.handle_link_code(make_message(2, 20, "12345"))
        self.assertIn("phone", user_states[2])
        self.assertIn("phone_code_hash", user_states[2])

    def test_2fa_branch_then_success(self) -> None:
        orch, user_states, cache, _, runtime, stats = self._make_orchestrator()
        runtime["mode"] = "2fa"
        orch.start_link_flow(make_message(3, 30, ""))
        orch.handle_link_phone(make_message(3, 30, "+79991112233"))
        orch.handle_link_code(make_message(3, 30, "12345"))
        self.assertEqual(user_states[3]["code"], "12345")
        runtime["mode"] = "success"
        orch.handle_link_password(make_message(3, 30, "password"))
        self.assertEqual(cache[3], (100.0, True))
        self.assertIn(3, stats["reset"])


if __name__ == "__main__":
    unittest.main()
