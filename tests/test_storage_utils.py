import unittest

from services.storage_utils import get_users_bucket


class StorageUtilsTests(unittest.TestCase):
    def test_returns_existing_users_dict(self) -> None:
        data = {"users": {"1": {"name": "u"}}}
        users = get_users_bucket(data)
        self.assertEqual(users, {"1": {"name": "u"}})

    def test_creates_users_when_missing(self) -> None:
        data = {"meta": 1}
        users = get_users_bucket(data)
        self.assertEqual(users, {})
        self.assertIn("users", data)

    def test_resets_non_dict_users(self) -> None:
        data = {"users": []}
        users = get_users_bucket(data)
        self.assertEqual(users, {})
        self.assertIsInstance(data["users"], dict)


if __name__ == "__main__":
    unittest.main()
