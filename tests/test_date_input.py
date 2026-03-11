import unittest
from datetime import datetime

from services.date_input import parse_user_date


class DateInputTests(unittest.TestCase):
    def test_parses_without_year_using_current_year(self) -> None:
        now = datetime(2026, 3, 9)
        self.assertEqual(parse_user_date("02.03", now=now), datetime(2026, 3, 2))
        self.assertEqual(parse_user_date("02 03", now=now), datetime(2026, 3, 2))

    def test_parses_two_digit_year(self) -> None:
        self.assertEqual(parse_user_date("02.03.25"), datetime(2025, 3, 2))
        self.assertEqual(parse_user_date("02 03 25"), datetime(2025, 3, 2))

    def test_parses_full_year(self) -> None:
        self.assertEqual(parse_user_date("02-03-2026"), datetime(2026, 3, 2))

    def test_rejects_invalid(self) -> None:
        self.assertIsNone(parse_user_date("32.03"))
        self.assertIsNone(parse_user_date("abc"))


if __name__ == "__main__":
    unittest.main()
