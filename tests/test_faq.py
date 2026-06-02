from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook

from database import Database
from faq_loader import FAQData
from handlers import WELCOME_MESSAGE, _is_chief_booker, root_faq_menu_parts
from keyboards import booker_panel_keyboard, main_menu_keyboard
from scripts.import_faq_from_excel import import_faq_from_excel


class UserStub:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class FAQTests(unittest.TestCase):
    def test_excel_import_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = Database(Path(tmp_dir) / "bot.sqlite3")
            db.init()
            excel_path = Path(tmp_dir) / "Вопросы.xlsx"
            self._write_workbook(excel_path, answer="Ответ")

            first = import_faq_from_excel(db, excel_path)
            second = import_faq_from_excel(db, excel_path)

            self.assertEqual(first.added, 1)
            self.assertEqual(first.updated, 0)
            self.assertEqual(first.skipped, 1)
            self.assertEqual(second.added, 0)
            self.assertEqual(second.updated, 0)
            self.assertEqual(second.skipped, 2)
            self.assertEqual(len(db.get_active_faq_data().items), 1)

            self._write_workbook(excel_path, answer="Новый ответ")
            third = import_faq_from_excel(db, excel_path)

            self.assertEqual(third.added, 0)
            self.assertEqual(third.updated, 1)
            self.assertEqual(third.skipped, 1)
            item = db.get_active_faq_data().items[0]
            self.assertEqual(item.answer, "Новый ответ")

    def test_faq_entries_are_loaded_by_section_and_soft_delete_hides_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = Database(Path(tmp_dir) / "bot.sqlite3")
            db.init()
            first = db.create_faq_entry(
                question="Вопрос 1",
                answer="Ответ 1",
                section="Раздел A",
            )
            db.create_faq_entry(
                question="Вопрос 2",
                answer="Ответ 2",
                section="Раздел B",
            )

            faq = db.get_active_faq_data()
            self.assertEqual(faq.sections, ["Раздел A", "Раздел B"])
            self.assertEqual(faq.by_section["Раздел A"][0].question, "Вопрос 1")

            db.soft_delete_faq_entry(first.id, updated_by=100)
            faq = db.get_active_faq_data()
            self.assertEqual(faq.sections, ["Раздел B"])
            self.assertNotIn(first.id, faq.by_id)

    def test_non_chief_user_does_not_get_management_controls(self) -> None:
        markup = main_menu_keyboard(
            ["Раздел"],
            is_booker=False,
            is_chief_booker=False,
        )
        texts = [button.text for row in markup.inline_keyboard for button in row]

        self.assertNotIn("⚙️ Управление вопросами", texts)
        self.assertNotIn("С кем связаться", texts)
        self.assertFalse(_is_chief_booker(UserStub(1), {2}))

    def test_start_welcome_message_matches_expected_text(self) -> None:
        self.assertEqual(
            WELCOME_MESSAGE,
            (
                "Привет!\n"
                "Я - VPбот, и сегодня я постараюсь помочь тебе оперативно\n"
                "решить твои вопросы🤍\n"
                "Что тебе хотелось бы узнать?"
            ),
        )

    def test_flattened_sections_are_root_question_buttons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = Database(Path(tmp_dir) / "bot.sqlite3")
            db.init()
            normal = db.create_faq_entry(
                question="Вопрос внутри раздела",
                answer="Ответ внутри раздела",
                section="Снепы",
            )
            children = db.create_faq_entry(
                question="Дети",
                answer="Ответ про детей",
                section="Общий раздел + Дополнительный доход",
            )
            hours = db.create_faq_entry(
                question="Часы работы букеров",
                answer="Ответ про часы",
                section="Общий раздел",
            )
            deleted = db.create_faq_entry(
                question="Скрытый вопрос",
                answer="Скрытый ответ",
                section="Общий раздел",
            )
            db.soft_delete_faq_entry(deleted.id)

            faq = db.get_active_faq_data()
            sections, section_indexes, root_questions = root_faq_menu_parts(faq)
            markup = main_menu_keyboard(
                sections,
                section_indexes=section_indexes,
                root_questions=root_questions,
                is_chief_booker=False,
            )
            texts = [button.text for row in markup.inline_keyboard for button in row]
            callback_by_text = {
                button.text: button.callback_data
                for row in markup.inline_keyboard
                for button in row
            }

            self.assertIn("Снепы", texts)
            self.assertNotIn("Общий раздел", texts)
            self.assertNotIn("Общий раздел + Дополнительный доход", texts)
            self.assertIn("Дети", texts)
            self.assertIn("Часы работы букеров", texts)
            self.assertNotIn("Скрытый вопрос", texts)
            self.assertEqual(callback_by_text["Снепы"], f"section:{faq.sections.index('Снепы')}")
            self.assertEqual(callback_by_text["Дети"], f"question:{children.token}")
            self.assertEqual(callback_by_text["Часы работы букеров"], f"question:{hours.token}")
            self.assertEqual(db.get_faq_entry_by_token(children.token).answer, "Ответ про детей")
            self.assertEqual(db.get_faq_entry_by_token(hours.token).answer, "Ответ про часы")
            self.assertEqual(faq.by_section["Снепы"][0].id, normal.id)

    def test_chief_booker_still_gets_management_control(self) -> None:
        faq = self._faq_data_with_flattened_sections()
        sections, section_indexes, root_questions = root_faq_menu_parts(faq)
        markup = main_menu_keyboard(
            sections,
            section_indexes=section_indexes,
            root_questions=root_questions,
            is_booker=False,
            is_chief_booker=True,
        )
        texts = [button.text for row in markup.inline_keyboard for button in row]

        self.assertIn("⚙️ Управление вопросами", texts)

    def test_normal_user_does_not_get_management_control_on_flattened_menu(self) -> None:
        faq = self._faq_data_with_flattened_sections()
        sections, section_indexes, root_questions = root_faq_menu_parts(faq)
        markup = main_menu_keyboard(
            sections,
            section_indexes=section_indexes,
            root_questions=root_questions,
            is_booker=False,
            is_chief_booker=False,
        )
        texts = [button.text for row in markup.inline_keyboard for button in row]

        self.assertNotIn("⚙️ Управление вопросами", texts)

    def test_booker_panel_shows_single_unified_duty_button(self) -> None:
        markup = booker_panel_keyboard()
        texts = [button.text for row in markup.inline_keyboard for button in row]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

        self.assertIn("Стать дежурным букером", texts)
        self.assertIn("Текущие дежурные", texts)
        self.assertIn("⬅️ Назад", texts)
        self.assertNotIn("Стать дежурным по кастингам", texts)
        self.assertNotIn("Стать дежурным по доп. доходу", texts)
        self.assertIn("booker:duty_general", callbacks)

    def test_chief_can_add_edit_and_soft_delete_faq_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = Database(Path(tmp_dir) / "bot.sqlite3")
            db.init()

            entry = db.create_faq_entry(
                question="Старый вопрос",
                answer="Старый ответ",
                department="Развитие",
                section="Раздел",
                created_by=10,
            )
            db.update_faq_entry(
                entry.id,
                question="Новый вопрос",
                answer="Новый ответ",
                department="РФ",
                section="Новый раздел",
                updated_by=11,
            )

            updated = db.get_faq_entry_by_token(entry.token)
            self.assertIsNotNone(updated)
            self.assertEqual(updated.question, "Новый вопрос")
            self.assertEqual(updated.answer, "Новый ответ")
            self.assertEqual(updated.department, "РФ")
            self.assertEqual(updated.section, "Новый раздел")

            db.soft_delete_faq_entry(entry.id, updated_by=12)
            self.assertIsNone(db.get_faq_entry_by_token(entry.token))
            self.assertIsNotNone(db.get_faq_entry_by_token(entry.token, active_only=False))

            with db._connect() as connection:
                row = connection.execute(
                    """
                    SELECT created_by, updated_by, is_active
                    FROM faq_entries
                    WHERE id = ?
                    """,
                    (entry.id,),
                ).fetchone()

            self.assertEqual(row["created_by"], 10)
            self.assertEqual(row["updated_by"], 12)
            self.assertEqual(row["is_active"], 0)

    @staticmethod
    def _write_workbook(path: Path, *, answer: str) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Вопрос", "Ответ", "Отдел", "Раздел"])
        sheet.append(["  Вопрос  ", f" {answer} ", " Развитие ", " Раздел "])
        sheet.append([None, None, None, None])
        workbook.save(path)
        workbook.close()

    @staticmethod
    def _faq_data_with_flattened_sections() -> FAQData:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = Database(Path(tmp_dir) / "bot.sqlite3")
            db.init()
            db.create_faq_entry(
                question="Разделенный вопрос",
                answer="Разделенный ответ",
                section="Снепы",
            )
            db.create_faq_entry(
                question="Дети",
                answer="Ответ про детей",
                section="Общий раздел",
            )
            return db.get_active_faq_data()


if __name__ == "__main__":
    unittest.main()
