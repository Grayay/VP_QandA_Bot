from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

import handlers
from database import Database
from handlers import (
    GENERAL_DUTY_SECTION,
    GENERAL_DUTY_SECTION_KEY,
    _active_duty_booker,
    _duty_status_text,
    _is_authorized_booker_or_chief,
    _reply_question_error,
    _send_model_answer_messages,
    _set_duty_for_user,
    flush_pending_questions_to_booker,
)
from keyboards import booker_reply_keyboard


APP_TIMEZONE = "Europe/Moscow"
WORKDAYS = frozenset({1, 2, 3, 4, 5})


class FakeBot:
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []

    async def send_message(self, **kwargs) -> None:
        self.sent_messages.append(kwargs)


class UserStub:
    def __init__(
        self,
        user_id: int,
        *,
        username: str | None = "booker",
        first_name: str = "Booker",
        last_name: str | None = None,
    ) -> None:
        self.id = user_id
        self.username = username
        self.first_name = first_name
        self.last_name = last_name


class QuestionRoutingTests(unittest.TestCase):
    def test_same_day_duty_is_inactive_outside_working_hours(self) -> None:
        now = datetime(2026, 6, 2, 20, 0, tzinfo=ZoneInfo(APP_TIMEZONE))
        duty = {
            "user_id": 42,
            "updated_at": datetime(2026, 6, 2, 11, 0, tzinfo=ZoneInfo(APP_TIMEZONE)).isoformat(),
        }

        self.assertIsNone(
            _active_duty_booker(
                duty=duty,
                now=now,
                app_timezone=APP_TIMEZONE,
                workday_start_hour=10,
                workday_end_hour=19,
                workdays=WORKDAYS,
            )
        )

    def test_stale_previous_day_duty_does_not_receive_pending_questions(self) -> None:
        now = datetime(2026, 6, 2, 11, 0, tzinfo=ZoneInfo(APP_TIMEZONE))
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = Database(Path(tmp_dir) / "bot.sqlite3")
            db.init()
            question_id = db.save_question(
                user_id=100,
                username="model",
                full_name="Model Name",
                section_key="castings",
                section="Кастинги",
                question="Weekend question",
                created_at=(now - timedelta(days=2)).isoformat(),
                after_cutoff=True,
            )
            db.set_duty_booker(
                section_key="castings",
                section="Кастинги",
                user_id=42,
                username="old_booker",
                full_name="Old Booker",
                updated_at=(now - timedelta(days=1)).isoformat(),
            )

            bot = FakeBot()
            with self._fixed_now(now):
                forwarded = asyncio.run(
                    flush_pending_questions_to_booker(
                        bot=bot,
                        database=db,
                        duty_sections={"castings": "Кастинги"},
                        app_timezone=APP_TIMEZONE,
                        workday_start_hour=10,
                        workday_end_hour=19,
                        workdays=WORKDAYS,
                    )
                )

            self.assertEqual(forwarded, 0)
            self.assertEqual(bot.sent_messages, [])
            self.assertEqual(db.get_question(question_id)["status"], "pending_assignment")

    def test_set_unified_duty_booker_uses_general_row(self) -> None:
        now = datetime(2026, 6, 2, 11, 0, tzinfo=ZoneInfo(APP_TIMEZONE))
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = Database(Path(tmp_dir) / "bot.sqlite3")
            db.init()

            with self._fixed_now(now):
                text = _set_duty_for_user(
                    database=db,
                    user=UserStub(42, username="new_booker", first_name="New", last_name="Booker"),
                    section_key=GENERAL_DUTY_SECTION_KEY,
                    section=GENERAL_DUTY_SECTION,
                    app_timezone=APP_TIMEZONE,
                )

            duty = db.get_duty_booker(GENERAL_DUTY_SECTION_KEY)
            self.assertEqual(text, "Готово! Вы назначены дежурным букером.")
            self.assertEqual(duty["user_id"], 42)
            self.assertEqual(duty["section_key"], GENERAL_DUTY_SECTION_KEY)
            self.assertEqual(_duty_status_text(db, {}), "Текущий дежурный букер:\nNew Booker (@new_booker, ID: 42)")

    def test_fresh_duty_receives_pending_questions_fifo_once(self) -> None:
        now = datetime(2026, 6, 2, 11, 0, tzinfo=ZoneInfo(APP_TIMEZONE))
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = Database(Path(tmp_dir) / "bot.sqlite3")
            db.init()
            first_id = db.save_question(
                user_id=100,
                username="first_model",
                full_name="First Model",
                section_key="castings",
                section="Кастинги",
                question="First question",
                created_at=(now - timedelta(days=2)).isoformat(),
                after_cutoff=True,
            )
            second_id = db.save_question(
                user_id=101,
                username="second_model",
                full_name="Second Model",
                section_key="income",
                section="Дополнительный доход",
                question="Second question",
                created_at=(now - timedelta(days=1)).isoformat(),
                after_cutoff=True,
            )
            db.set_duty_booker(
                section_key=GENERAL_DUTY_SECTION_KEY,
                section=GENERAL_DUTY_SECTION,
                user_id=42,
                username="booker",
                full_name="Booker",
                updated_at=now.isoformat(),
            )

            bot = FakeBot()
            with self._fixed_now(now):
                first_flush = asyncio.run(
                    flush_pending_questions_to_booker(
                        bot=bot,
                        database=db,
                        duty_sections={"castings": "Кастинги"},
                        app_timezone=APP_TIMEZONE,
                        workday_start_hour=10,
                        workday_end_hour=19,
                        workdays=WORKDAYS,
                    )
                )
                second_flush = asyncio.run(
                    flush_pending_questions_to_booker(
                        bot=bot,
                        database=db,
                        duty_sections={"castings": "Кастинги"},
                        app_timezone=APP_TIMEZONE,
                        workday_start_hour=10,
                        workday_end_hour=19,
                        workdays=WORKDAYS,
                    )
                )

            self.assertEqual(first_flush, 2)
            self.assertEqual(second_flush, 0)
            self.assertEqual(len(bot.sent_messages), 2)
            self.assertIn("First question", bot.sent_messages[0]["text"])
            self.assertIn("Second question", bot.sent_messages[1]["text"])
            self.assertEqual(bot.sent_messages[0]["chat_id"], 42)
            self.assertEqual(db.get_question(first_id)["status"], "sent_to_booker")
            self.assertEqual(db.get_question(second_id)["status"], "sent_to_booker")

    def test_reply_keyboard_uses_question_id_callback(self) -> None:
        markup = booker_reply_keyboard(123)
        button = markup.inline_keyboard[0][0]

        self.assertEqual(button.text, "Ответить модели")
        self.assertEqual(button.callback_data, "booker:reply:123")

    def test_model_answer_is_sent_as_two_clean_messages(self) -> None:
        bot = FakeBot()

        asyncio.run(
            _send_model_answer_messages(
                bot=bot,
                chat_id=100,
                answer="Clean answer",
            )
        )

        self.assertEqual(
            bot.sent_messages,
            [
                {"chat_id": 100, "text": "Ответ на ваш вопрос:"},
                {"chat_id": 100, "text": "Clean answer"},
            ],
        )

    def test_answered_and_failed_statuses_are_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = Database(Path(tmp_dir) / "bot.sqlite3")
            db.init()
            question_id = db.save_question(
                user_id=100,
                username=None,
                full_name="Model",
                section_key="castings",
                section="Кастинги",
                question="Question",
            )
            db.mark_question_sent_to_booker(question_id=question_id, booker_id=42)
            db.mark_question_answered(question_id=question_id, answer_text="Clean answer")

            question = db.get_question(question_id)
            self.assertEqual(question["status"], "answered")
            self.assertEqual(question["answer_text"], "Clean answer")
            self.assertEqual(
                _reply_question_error(
                    question=question,
                    user=UserStub(42),
                    chief_booker_ids=set(),
                ),
                "На этот вопрос уже ответили.",
            )

            failed_id = db.save_question(
                user_id=101,
                username=None,
                full_name="Other Model",
                section_key="castings",
                section="Кастинги",
                question="Other question",
            )
            db.mark_question_failed(question_id=failed_id, error_details="blocked")
            failed = db.get_question(failed_id)
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["last_error"], "blocked")

    def test_booker_or_chief_access_helper(self) -> None:
        self.assertFalse(_is_authorized_booker_or_chief(UserStub(1), {2}, {3}))
        self.assertTrue(_is_authorized_booker_or_chief(UserStub(2), {2}, {3}))
        self.assertTrue(_is_authorized_booker_or_chief(UserStub(3), {2}, {3}))

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
