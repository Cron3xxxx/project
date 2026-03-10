import unittest

from services.openai_client import _extract_text


class Obj:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class OpenAIExtractTextTests(unittest.TestCase):
    def test_prefers_output_text(self) -> None:
        resp = Obj(output_text="hello")
        self.assertEqual(_extract_text(resp), "hello")

    def test_fallbacks_to_output_content(self) -> None:
        resp = Obj(
            output=[
                Obj(content=[Obj(text="line 1")]),
                Obj(content=[Obj(text="line 2")]),
            ]
        )
        self.assertEqual(_extract_text(resp), "line 1\nline 2")

    def test_returns_stringified_response(self) -> None:
        resp = Obj(output=[])
        text = _extract_text(resp)
        self.assertTrue(text.startswith("<"))


if __name__ == "__main__":
    unittest.main()
