import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from services.parsing_orchestrator import ParsingOrchestrator


class FakeLogger:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def info(self, *args) -> None:
        self.events.append(args)


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple] = []
        self.handlers: list = []
        self._msg_id = 0

    def send_message(self, chat_id, text, reply_markup=None):
        self._msg_id += 1
        self.messages.append((chat_id, text, reply_markup))
        return SimpleNamespace(message_id=self._msg_id, chat=SimpleNamespace(id=chat_id))

    def register_next_step_handler(self, msg, handler) -> None:
        self.handlers.append((msg.message_id, handler))

    def register_next_step_handler_by_chat_id(self, chat_id, handler) -> None:
        self.handlers.append((chat_id, handler))

    def clear_step_handler_by_chat_id(self, _chat_id) -> None:
        return None


def make_message(user_id: int, chat_id: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=user_id),
        chat=SimpleNamespace(id=chat_id),
    )


class ParsingOrchestratorTests(unittest.TestCase):
    def _build(self):
        user_states: dict[int, dict] = {}
        bot = FakeBot()
        logger = FakeLogger()
        called = {"complete": []}
        user = {"channels": [{"channel": "@test"}], "last_query": "q", "last_range": {"from": "01-01-2026", "to": "02-01-2026"}}

        def parse_date(raw: str):
            try:
                return datetime.strptime(raw, "%d-%m-%Y")
            except ValueError:
                return None

        def within_history_limit(date_obj: datetime) -> bool:
            return date_obj >= datetime.now() - timedelta(days=30 * 5)

        orch = ParsingOrchestrator(
            user_states=user_states,
            bot=bot,
            date_format="%d-%m-%Y",
            history_limit_months=5,
            max_topic_length=500,
            telethon_session="fallback",
            get_logger=lambda: logger,
            reset_parse_flow=lambda _uid, _cid: None,
            ensure_or_create_user=lambda _uid: user,
            telethon_credentials_ok=lambda: True,
            has_user_session=lambda _uid: True,
            parse_date=parse_date,
            within_history_limit=within_history_limit,
            send_asset_photo=lambda cid, _f, caption, reply_markup=None: bot.send_message(cid, caption, reply_markup=reply_markup),
            edit_card_photo=lambda *_args, **_kwargs: True,
            back_markup=lambda: "back",
            inline_menu_channels=lambda: "channels",
            on_complete_parsing=lambda *args: called["complete"].append(args),
        )
        orch.set_handlers(
            handle_query=lambda m: orch.handle_parse_query(m),
            handle_date_from=lambda m: orch.handle_parse_date_from(m),
            handle_date_to=lambda m: orch.handle_parse_date_to(m),
        )
        return orch, user_states, bot, called

    def test_start_parsing_flow_sets_state(self) -> None:
        orch, user_states, bot, _ = self._build()
        orch.start_parsing_flow(1, 100)
        self.assertEqual(user_states[1]["step"], "query")
        self.assertTrue(bot.handlers)

    def test_parse_query_sets_next_step(self) -> None:
        orch, user_states, bot, _ = self._build()
        user_states[1] = {"parse_mode": True, "step": "query"}
        orch.handle_parse_query(make_message(1, 100, "мой запрос"))
        self.assertEqual(user_states[1]["step"], "date_from")
        self.assertEqual(user_states[1]["query"], "мой запрос")
        self.assertTrue(bot.handlers)

    def test_date_flow_completes(self) -> None:
        orch, user_states, _, called = self._build()
        user_states[1] = {"parse_mode": True, "step": "date_from", "query": "q"}
        orch.handle_parse_date_from(make_message(1, 100, "01-03-2026"))
        self.assertEqual(user_states[1]["step"], "date_to")
        orch.handle_parse_date_to(make_message(1, 100, "02-03-2026"))
        self.assertEqual(len(called["complete"]), 1)


if __name__ == "__main__":
    unittest.main()
