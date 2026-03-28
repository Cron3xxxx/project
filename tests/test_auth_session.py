import unittest

from services.auth_session import (
    close_login_client,
    complete_2fa,
    complete_login,
    send_login_code,
)


class SessionPasswordNeededError(Exception):
    pass


class FakeClient:
    def __init__(self, *, needs_password: bool = False) -> None:
        self.connected = False
        self.needs_password = needs_password
        self.calls: list[tuple] = []
        self.sent = type("Sent", (), {"phone_code_hash": "hash-123"})()

    async def connect(self) -> None:
        self.connected = True
        self.calls.append(("connect",))

    def is_connected(self) -> bool:
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False
        self.calls.append(("disconnect",))

    async def send_code_request(self, phone: str, force_sms: bool = False):
        self.calls.append(("send_code_request", phone, force_sms))
        return self.sent

    async def sign_in(self, *, phone=None, code=None, phone_code_hash=None, password=None) -> None:
        if password is not None:
            self.calls.append(("sign_in_password", password))
            return
        self.calls.append(("sign_in_code", phone, code, phone_code_hash))
        if self.needs_password:
            raise SessionPasswordNeededError()


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple] = []

    def info(self, *args) -> None:
        self.records.append(args)


class AuthSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_login_code_creates_client_and_returns_meta(self) -> None:
        login_clients: dict[int, FakeClient] = {}
        logger = FakeLogger()
        created: list[FakeClient] = []

        def factory(_session_path: str, _api_id: int, _api_hash: str) -> FakeClient:
            client = FakeClient()
            created.append(client)
            return client

        meta = await send_login_code(
            user_id=1,
            phone="+79990000000",
            api_id=1,
            api_hash="h",
            session_path="s",
            login_clients=login_clients,
            client_factory=factory,
            logger=logger,
            force_sms=False,
            parse_sent_code_metadata=lambda sent: {"phone_code_hash": sent.phone_code_hash},
            mask_phone=lambda phone: phone,
        )

        self.assertEqual(meta["phone_code_hash"], "hash-123")
        self.assertIn(1, login_clients)
        self.assertIn(("send_code_request", "+79990000000", False), created[0].calls)

    async def test_complete_login_passes_phone_code_hash(self) -> None:
        client = FakeClient()
        login_clients = {5: client}
        logger = FakeLogger()

        await complete_login(
            user_id=5,
            phone="+79991112233",
            code="12345",
            phone_code_hash="hash-xyz",
            password=None,
            api_id=1,
            api_hash="h",
            session_path="s",
            login_clients=login_clients,
            client_factory=lambda *_: client,
            session_password_needed_error=SessionPasswordNeededError,
            logger=logger,
        )

        self.assertIn(("sign_in_code", "+79991112233", "12345", "hash-xyz"), client.calls)
        self.assertNotIn(5, login_clients)

    async def test_complete_login_keeps_client_when_2fa_needed(self) -> None:
        client = FakeClient(needs_password=True)
        login_clients = {7: client}
        logger = FakeLogger()

        with self.assertRaises(SessionPasswordNeededError):
            await complete_login(
                user_id=7,
                phone="+79991112233",
                code="12345",
                phone_code_hash="hash-xyz",
                password=None,
                api_id=1,
                api_hash="h",
                session_path="s",
                login_clients=login_clients,
                client_factory=lambda *_: client,
                session_password_needed_error=SessionPasswordNeededError,
                logger=logger,
            )

        self.assertIn(7, login_clients)
        self.assertNotIn(("disconnect",), client.calls)

    async def test_complete_2fa_disconnects_and_removes_client(self) -> None:
        client = FakeClient()
        login_clients = {9: client}
        logger = FakeLogger()

        await complete_2fa(user_id=9, password="pwd", login_clients=login_clients, logger=logger)

        self.assertIn(("sign_in_password", "pwd"), client.calls)
        self.assertNotIn(9, login_clients)

    async def test_close_login_client(self) -> None:
        client = FakeClient()
        login_clients = {10: client}

        await close_login_client(user_id=10, login_clients=login_clients)

        self.assertNotIn(10, login_clients)
        self.assertIn(("disconnect",), client.calls)


if __name__ == "__main__":
    unittest.main()
