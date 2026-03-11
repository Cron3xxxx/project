import unittest

from services.auth_utils import delivery_hint, extract_digits_code, parse_sent_code_metadata


class Obj:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class AuthUtilsTests(unittest.TestCase):
    def test_extract_digits_code_ok(self) -> None:
        self.assertEqual(extract_digits_code("1a2b3c4"), "1234")

    def test_extract_digits_code_too_short(self) -> None:
        self.assertIsNone(extract_digits_code("a1b2"))

    def test_parse_sent_code_metadata(self) -> None:
        sent = Obj(
            type=Obj(),
            next_type=Obj(),
            timeout=55,
            phone_code_hash="hash_123",
        )
        meta = parse_sent_code_metadata(sent)
        self.assertEqual(meta["code_type_name"], "Obj")
        self.assertEqual(meta["next_type_name"], "Obj")
        self.assertEqual(meta["timeout"], 55)
        self.assertEqual(meta["phone_code_hash"], "hash_123")

    def test_delivery_hint(self) -> None:
        self.assertEqual(delivery_hint("SentCodeTypeSms"), "по SMS")
        self.assertEqual(delivery_hint("UnknownType"), "неизвестный канал")


if __name__ == "__main__":
    unittest.main()
