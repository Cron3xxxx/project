import importlib
import os
import unittest

from services import auth_session


class SpyLogger:
    def __init__(self) -> None:
        self.records: list[str] = []

    def info(self, *args) -> None:
        self.records.append(" ".join(str(arg) for arg in args if arg is not None))


class TgRequestLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        os.environ["TELETHON_DRY_RUN"] = "1"
        importlib.reload(auth_session)
        self.module = auth_session
        self.module._tg_request_counts.clear()
        self.logger = SpyLogger()

    class DummyClient:
        async def connect(self) -> None:
            pass

        def is_connected(self) -> bool:
            return True

        async def disconnect(self) -> None:
            pass

    async def test_send_code_request_logs_count(self) -> None:
        await self.module.send_login_code(
            user_id=42,
            phone="+79990001122",
            api_id=1,
            api_hash="hash",
            session_path="unused",
            login_clients={},
            client_factory=lambda *args: self.DummyClient(),
            logger=self.logger,
            force_sms=False,
            parse_sent_code_metadata=lambda sent: {"phone_code_hash": getattr(sent, "phone_code_hash", "dry")},
            mask_phone=lambda value: value,
        )

        self.assertIn(1, self.module._tg_request_counts.values())
        self.assertTrue(any("send_code_request" in entry for entry in self.logger.records))

    async def test_complete_login_logs_sign_in(self) -> None:
        await self.module.complete_login(
            user_id=42,
            phone="+79990001122",
            code="123456",
            phone_code_hash="dry",
            password=None,
            api_id=1,
            api_hash="hash",
            session_path="unused",
            login_clients={},
            client_factory=lambda *args: self.DummyClient(),
            session_password_needed_error=ValueError,
            logger=self.logger,
        )

        self.assertEqual(self.module._tg_request_counts.get("sign_in_code"), 1)
        self.assertTrue(any("sign_in_code" in entry for entry in self.logger.records))

    async def test_complete_2fa_logs_sign_in_password(self) -> None:
        await self.module.complete_2fa(
            user_id=42,
            password="pwd",
            login_clients={},
            logger=self.logger,
        )

        self.assertEqual(self.module._tg_request_counts.get("sign_in_password"), 1)
        self.assertTrue(any("sign_in_password" in entry for entry in self.logger.records))


if __name__ == "__main__":
    unittest.main()
