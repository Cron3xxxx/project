import json
import tempfile
import unittest
from pathlib import Path

from services.user_storage import (
    create_user,
    ensure_or_create_user,
    get_user,
    load_storage,
    save_storage,
    upsert_user,
)


class UserStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.storage_path = str(Path(self.tmp_dir.name) / "data.json")

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_load_storage_returns_empty_on_missing(self) -> None:
        self.assertEqual(load_storage(self.storage_path), {"users": {}})

    def test_load_storage_returns_empty_on_invalid_json(self) -> None:
        Path(self.storage_path).write_text("{broken", encoding="utf-8")
        self.assertEqual(load_storage(self.storage_path), {"users": {}})

    def test_create_user_and_get_user(self) -> None:
        created = create_user(self.storage_path, 42, "%d-%m-%Y")
        got = get_user(self.storage_path, 42)
        self.assertIsNotNone(got)
        self.assertEqual(created["registered_at"], got["registered_at"])
        self.assertEqual(got["channels"], [])

    def test_ensure_or_create_user_is_idempotent(self) -> None:
        first = ensure_or_create_user(self.storage_path, 5, "%d-%m-%Y")
        second = ensure_or_create_user(self.storage_path, 5, "%d-%m-%Y")
        self.assertEqual(first["registered_at"], second["registered_at"])

    def test_upsert_user(self) -> None:
        payload = {
            "registered_at": "01-01-2026",
            "channels": [{"channel": "@test"}],
            "last_query": "q",
            "last_range": {"from": "01-01-2026", "to": "02-01-2026"},
            "last_parse": "ok",
        }
        upsert_user(self.storage_path, 7, payload)
        got = get_user(self.storage_path, 7)
        self.assertEqual(got, payload)

    def test_save_storage_writes_json(self) -> None:
        save_storage(self.storage_path, {"users": {"1": {"x": 1}}})
        raw = Path(self.storage_path).read_text(encoding="utf-8")
        parsed = json.loads(raw)
        self.assertIn("users", parsed)


if __name__ == "__main__":
    unittest.main()
