import unittest

from services.ai_formatter import build_ai_answer_message, render_ai_html


class AiFormatterTests(unittest.TestCase):
    def test_render_ai_html_supports_headings_and_lists(self) -> None:
        raw = "# Заголовок\n- пункт\n1. шаг\nТекст с **важно** и `код`"
        rendered = render_ai_html(raw)
        self.assertIn("<b>Заголовок</b>", rendered)
        self.assertIn("• пункт", rendered)
        self.assertIn("1. шаг", rendered)
        self.assertIn("<b>важно</b>", rendered)
        self.assertIn("<code>код</code>", rendered)

    def test_build_ai_answer_message_adds_title(self) -> None:
        msg = build_ai_answer_message("Пример")
        self.assertTrue(msg.startswith("<b>🤖 Ответ ИИ</b>"))
        self.assertIn("Пример", msg)


if __name__ == "__main__":
    unittest.main()
