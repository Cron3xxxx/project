import unittest

from services.auth_flow import (
    apply_sent_code_meta,
    auth_locked,
    clear_auth_failures,
    code_resend_wait,
    mask_phone,
    normalize_phone,
    register_auth_failure,
)


class AuthFlowTests(unittest.TestCase):
    def test_normalize_phone(self) -> None:
        self.assertEqual(normalize_phone("+79991112233"), "+79991112233")
        self.assertEqual(normalize_phone("79991112233"), "+79991112233")
        self.assertIsNone(normalize_phone("+79a"))

    def test_mask_phone(self) -> None:
        self.assertEqual(mask_phone("+79991112233"), "+79***33")
        self.assertEqual(mask_phone("123"), "***")

    def test_auth_lock_lifecycle(self) -> None:
        state: dict = {}
        register_auth_failure(state, max_auth_attempts=2, auth_lock_seconds=60, now_ts=100)
        self.assertEqual(auth_locked(state, now_ts=100), 0)
        register_auth_failure(state, max_auth_attempts=2, auth_lock_seconds=60, now_ts=100)
        self.assertEqual(auth_locked(state, now_ts=110), 50)
        clear_auth_failures(state)
        self.assertEqual(auth_locked(state, now_ts=110), 0)

    def test_code_resend_wait(self) -> None:
        state = {"last_code_sent_at": 100}
        self.assertEqual(code_resend_wait(state, cooldown_seconds=60, now_ts=120), 40)
        self.assertEqual(code_resend_wait(state, cooldown_seconds=60, now_ts=200), 0)

    def test_apply_sent_code_meta(self) -> None:
        state: dict = {}
        apply_sent_code_meta(
            state,
            {"phone_code_hash": "hash", "code_type_name": "SentCodeTypeApp"},
            now_ts=123,
        )
        self.assertEqual(state["phone_code_hash"], "hash")
        self.assertEqual(state["code_type_name"], "SentCodeTypeApp")
        self.assertEqual(state["last_code_sent_at"], 123)


if __name__ == "__main__":
    unittest.main()
