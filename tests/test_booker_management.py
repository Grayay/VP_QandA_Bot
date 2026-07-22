from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

import handlers
from database import Database
from handlers import (
    GENERAL_DUTY_SECTION,
    GENERAL_DUTY_SECTION_KEY,
    NO_BOOKER_MANAGEMENT_ACCESS_MESSAGE,
    _booker_delete_error,
    _ensure_booker_management_callback,
    _is_authorized_booker_or_chief,
    flush_pending_questions_to_booker,
)
from keyboards import booker_management_keyboard, main_menu_keyboard
from tests.test_question_routing import FakeBot


APP_TIMEZONE = "Europe/Moscow"
WORKDAYS = frozenset({1, 2, 3, 4, 5})


class UserStub:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class CallbackStub:
    def __init__(self, user_id: int) -> None:
        self.from_user = UserStub(user_id)
        self.answers: list[dict] = []

    async def answer(self, text: str | None = None, show_alert: bool | None = None) -> None:
        self.answers.append({"text": text, "show_alert": show_alert})


class BookerManagementTests(unittest.TestCase):
    def test_chief_admin_can_open_booker_management(self) -> None:
        callback = CallbackStub(10)

        allowed = asyncio.run(_ensure_booker_management_callback(callback, {10}))

        self.assertTrue(allowed)
        self.assertEqual(callback.answers, [{"text": None, "show_alert": None}])

    def test_regular_user_cannot_open_booker_management_by_direct_callback(self) -> None:
        callback = CallbackStub(20)

        allowed = asyncio.run(_ensure_booker_management_callback(callback, {10}))

        self.assertFalse(allowed)
        self.assertEqual(
            callback.answers,
            [{"text": NO_BOOKER_MANAGEMENT_ACCESS_MESSAGE, "show_alert": True}],
        )

    def test_main_menu_shows_booker_management_only_to_chief(self) -> None:
        chief_markup = main_menu_keyboard(["Раздел"], is_chief_booker=True)
        regular_markup = main_menu_keyboard(["Раздел"], is_chief_booker=False)
        chief_texts = [button.text for row in chief_markup.inline_keyboard for button in row]
        regular_texts = [button.text for row in regular_markup.inline_keyboard for button in row]

        self.assertIn("Управление букерами", chief_texts)
        self.assertNotIn("Управление букерами", regular_texts)

    def test_booker_management_menu_has_expected_actions(self) -> None:
        markup = booker_management_keyboard()
        texts = [button.text for row in markup.inline_keyboard for button in row]

        self.assertEqual(
            texts,
            ["Список букеров", "Добавить букера", "Удалить букера", "Назад"],
        )

    def test_add_new_booker_and_reject_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = Database(Path(tmp_dir) / "bot.sqlite3")
            db.init()
            db.save_question(
                user_id=42,
                username="known_booker",
                full_name="Known Booker",
                section_key=GENERAL_DUTY_SECTION_KEY,
                section=GENERAL_DUTY_SECTION,
                question="Question",
            )

            added = db.add_booker(
                telegram_id=42,
                username=db.find_known_booker_username(42),
                display_name="Known Booker",
            )
            duplicate = db.add_booker(
                telegram_id=42,
                username=None,
                display_name="Duplicate Booker",
            )
            booker = db.get_booker(42)

            self.assertTrue(added)
            self.assertFalse(duplicate)
            self.assertEqual(booker["telegram_id"], 42)
            self.assertEqual(booker["username"], "known_booker")
            self.assertEqual(booker["display_name"], "Known Booker")

    def test_import_bookers_from_config_is_idempotent_and_skips_chiefs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = Database(Path(tmp_dir) / "bot.sqlite3")
            db.init()

            first = db.import_bookers_from_config({1, 2, 3}, chief_booker_ids={1})
            second = db.import_bookers_from_config({4}, chief_booker_ids=set())
            ids = {booker["telegram_id"] for booker in db.list_bookers()}

            self.assertEqual(first, 2)
            self.assertEqual(second, 0)
            self.assertEqual(ids, {2, 3})

    def test_delete_booker_revokes_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = Database(Path(tmp_dir) / "bot.sqlite3")
            db.init()
            db.add_booker(telegram_id=42, username=None, display_name="Booker")

            self.assertTrue(
                _is_authorized_booker_or_chief(
                    UserStub(42),
                    set(),
                    {10},
                    database=db,
                )
            )
            self.assertTrue(db.remove_booker(42))

            self.assertFalse(db.is_booker(42))
            self.assertFalse(
                _is_authorized_booker_or_chief(
                    UserStub(42),
                    set(),
                    {10},
                    database=db,
                )
            )

    def test_cannot_delete_chief_admin_as_regular_booker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = Database(Path(tmp_dir) / "bot.sqlite3")
            db.init()

            self.assertEqual(
                _booker_delete_error(10, {10}, db),
                "Главных администраторов нельзя удалять через это меню.",
            )

    def test_deleted_booker_no_longer_receives_pending_questions(self) -> None:
        now = datetime(2026, 6, 2, 11, 0, tzinfo=ZoneInfo(APP_TIMEZONE))
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = Database(Path(tmp_dir) / "bot.sqlite3")
            db.init()
            db.add_booker(telegram_id=42, username="booker", display_name="Booker")
            db.set_duty_booker(
                section_key=GENERAL_DUTY_SECTION_KEY,
                section=GENERAL_DUTY_SECTION,
                user_id=42,
                username="booker",
                full_name="Booker",
                updated_at=now.isoformat(),
            )
            db.remove_booker(42)
            question_id = db.save_question(
                user_id=100,
                username="model",
                full_name="Model",
                section_key=GENERAL_DUTY_SECTION_KEY,
                section=GENERAL_DUTY_SECTION,
                question="Pending question",
                created_at=now.isoformat(),
            )

            bot = FakeBot()
            with self._fixed_now(now):
                forwarded = asyncio.run(
                    flush_pending_questions_to_booker(
                        bot=bot,
                        database=db,
                        duty_sections={GENERAL_DUTY_SECTION_KEY: GENERAL_DUTY_SECTION},
                        app_timezone=APP_TIMEZONE,
                        workday_start_hour=10,
                        workday_end_hour=19,
                        workdays=WORKDAYS,
                    )
                )

            self.assertEqual(forwarded, 0)
            self.assertEqual(bot.sent_messages, [])
            self.assertEqual(db.get_question(question_id)["status"], "pending_assignment")

    def test_deleting_current_duty_booker_returns_sent_questions_to_queue(self) -> None:
        now = datetime(2026, 6, 2, 11, 0, tzinfo=ZoneInfo(APP_TIMEZONE))
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = Database(Path(tmp_dir) / "bot.sqlite3")
            db.init()
            db.add_booker(telegram_id=42, username="booker", display_name="Booker")
            db.set_duty_booker(
                section_key=GENERAL_DUTY_SECTION_KEY,
                section=GENERAL_DUTY_SECTION,
                user_id=42,
                username="booker",
                full_name="Booker",
                updated_at=now.isoformat(),
            )
            question_id = db.save_question(
                user_id=100,
                username="model",
                full_name="Model",
                section_key=GENERAL_DUTY_SECTION_KEY,
                section=GENERAL_DUTY_SECTION,
                question="Already sent question",
                created_at=now.isoformat(),
            )
            db.mark_question_sent_to_booker(question_id=question_id, booker_id=42, sent_at=now.isoformat())

            self.assertTrue(db.remove_booker(42))
            question = db.get_question(question_id)

            self.assertIsNone(db.get_duty_booker(GENERAL_DUTY_SECTION_KEY))
            self.assertEqual(question["status"], "pending_assignment")
            self.assertIsNone(question["routed_to_booker_id"])
            self.assertIsNone(question["routed_at"])

    @staticmethod
    def _fixed_now(value: datetime):
        class FixedNow:
            def __enter__(self):
                self.original = handlers._now
                handlers._now = lambda app_timezone: value

            def __exit__(self, exc_type, exc, tb):
                handlers._now = self.original

        return FixedNow()


if __name__ == "__main__":
    unittest.main()
