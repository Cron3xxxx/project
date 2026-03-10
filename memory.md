

- pyTelegramBotAPI
- telethon
- python-dotenv
- openai
- unittest (stdlib)




- 2026-03-08: added `services/parsing_service.py` and `tests/test_parsing_service.py`; continued parsing-layer decomposition from `bot.py`.
- 2026-03-08: added `services/auth_flow.py` and `tests/test_auth_flow.py`; moved auth utility logic from `bot.py` to service layer.
- 2026-03-08: added `services/auth_session.py` and `tests/test_auth_session.py`; extracted low-level MTProto login/session flow from `bot.py` with regression check for `phone_code_hash` propagation.
- 2026-03-08: added `services/auth_orchestrator.py` and `tests/test_auth_orchestrator.py`; extracted account-link orchestration steps from `bot.py` while preserving callback wiring and runtime behavior.
- 2026-03-08: added `services/parsing_orchestrator.py` and `tests/test_parsing_orchestrator.py`; moved parsing state-machine steps from `bot.py` to service-level orchestrator.
- 2026-03-08: added integration tests `tests/test_auth_flow_integration.py` for full auth scenarios (success, invalid code resend, 2FA branch).
- 2026-03-08: improved JSON durability in `services/user_storage.py` via atomic writes (temp file + `os.replace`).
- 2026-03-08: added parsing observability with `logs/parsing.log` and event markers `PARSE_START`, `PARSE_EXEC_START`, `PARSE_EXEC_SUCCESS`, `PARSE_EXEC_FAILED`, `PARSE_AI_ERROR`.
- 2026-03-09: fixed broken text/encoding regressions after refactor in `bot.py` (parsing/FAQ strings) and restored readable Russian checklist section in `README.md`; revalidated with compile + unit tests.
- 2026-03-09: in `services/auth_orchestrator.py` removed duplicate success notifications; account linking now sends a single confirmation message for both normal and 2FA success paths.
- 2026-03-09: added `services/date_input.py` and `tests/test_date_input.py`; parsing dates now accepts inputs `DD.MM`, `DD MM`, `DD.MM.YY`, `DD MM YY` with current year fallback when year is omitted.
- 2026-03-09: added `services/ai_formatter.py` and `tests/test_ai_formatter.py`; AI answers are now formatted with readable heading, lists, and inline emphasis before sending to Telegram.
- 2026-03-09: disabled `TELETHON_SESSION` fallback for user parsing; parsing now requires a valid user MTProto session file. Reduced session auth cache TTL from 120s to 30s.
