import unittest

from services.parsing_service import matches_query, normalize_channel, truncate_text


class ParsingServiceTests(unittest.TestCase):
    def test_matches_query_case_insensitive(self) -> None:
        self.assertTrue(matches_query("Hello World", "world"))
        self.assertFalse(matches_query("Hello", "planet"))

    def test_normalize_channel(self) -> None:
        self.assertEqual(normalize_channel("@abcde"), "@abcde")
        self.assertEqual(normalize_channel("t.me/abcde"), "@abcde")
        self.assertEqual(normalize_channel("https://t.me/abcde"), "@abcde")

    def test_truncate_text(self) -> None:
        self.assertEqual(truncate_text("short", 10), "short")
        self.assertEqual(truncate_text("1234567890", 7), "1234...")


if __name__ == "__main__":
    unittest.main()
