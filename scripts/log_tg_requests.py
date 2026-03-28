import asyncio
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from services import auth_session


def _setup_logger(path: str) -> logging.Logger:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    logger = logging.getLogger("auth-tg-stat")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
    return logger


class _StubClient:
    async def connect(self) -> None:
        pass

    def is_connected(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def send_code_request(self, phone: str, force_sms: bool = False):
        return SimpleNamespace(
            type=type("SentCodeTypeDryRun", (), {})(),
            next_type=type("SentCodeTypeDryRunNext", (), {})(),
            timeout=0,
            phone_code_hash="dry-run-hash",
        )

    async def sign_in(self, *, phone=None, code=None, phone_code_hash=None, password=None):
        return None


async def _main() -> None:
    os.environ["TELETHON_DRY_RUN"] = "1"
    logger = _setup_logger("logs/auth.log")
    login_clients: dict[int, _StubClient] = {}
    meta = await auth_session.send_login_code(
        user_id=123,
        phone="+79990001111",
        api_id=1,
        api_hash="hash",
        session_path="unused",
        login_clients=login_clients,
        client_factory=lambda *args: _StubClient(),
        logger=logger,
        force_sms=False,
        parse_sent_code_metadata=lambda sent: {
            "phone_code_hash": getattr(sent, "phone_code_hash", "dry"),
            "code_type_name": getattr(sent, "type", type("T", (), {})()).__class__.__name__,
            "next_type_name": getattr(sent, "next_type", type("N", (), {})()).__class__.__name__,
            "timeout": getattr(sent, "timeout", None),
        },
        mask_phone=lambda value: value,
    )
    print("send_login_code meta:", meta)

    await auth_session.complete_login(
        user_id=123,
        phone="+79990001111",
        code="123456",
        phone_code_hash=meta.get("phone_code_hash"),
        password=None,
        api_id=1,
        api_hash="hash",
        session_path="unused",
        login_clients=login_clients,
        client_factory=lambda *args: _StubClient(),
        session_password_needed_error=Exception,
        logger=logger,
    )
    print("complete_login done")


if __name__ == "__main__":
    asyncio.run(_main())
