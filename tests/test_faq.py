from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook

from database import Database
from handlers import _is_chief_booker
from keyboards import main_menu_keyboard
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


if __name__ == "__main__":
    unittest.main()
