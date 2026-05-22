from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import BASE_DIR, DB_FILE  # noqa: E402
from database import Database  # noqa: E402
from faq_loader import FAQLoaderError, load_faq  # noqa: E402


FAQ_FILE_CANDIDATES = (
    BASE_DIR / "Вопросы.xlsx",
    BASE_DIR / "data" / "Вопросы.xlsx",
)


@dataclass(frozen=True)
class ImportSummary:
    added: int = 0
    updated: int = 0
    skipped: int = 0


def import_faq_from_excel(database: Database, excel_path: Path) -> ImportSummary:
    faq = load_faq(excel_path)
    counts = {"added": 0, "updated": 0, "skipped": faq.skipped_rows}

    for item in faq.items:
        result = database.upsert_faq_entry_from_import(
            question=item.question,
            answer=item.answer,
            department=item.department,
            section=item.section,
        )
        counts[result] += 1

    return ImportSummary(
        added=counts["added"],
        updated=counts["updated"],
        skipped=counts["skipped"],
    )


def find_default_excel_file() -> Path:
    for path in FAQ_FILE_CANDIDATES:
        if path.exists():
            return path

    candidates = ", ".join(str(path) for path in FAQ_FILE_CANDIDATES)
    raise FAQLoaderError(f"Не найден Excel-файл FAQ. Проверенные пути: {candidates}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import FAQ entries from Excel into SQLite.")
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="Path to Вопросы.xlsx. Defaults to project root or data/Вопросы.xlsx.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_FILE,
        help="Path to SQLite database. Defaults to bot.sqlite3.",
    )
    args = parser.parse_args()

    excel_path = args.file or find_default_excel_file()
    database = Database(args.db)
    database.init()
    summary = import_faq_from_excel(database, excel_path)

    print(f"Файл: {excel_path}")
    print(f"База: {args.db}")
    print(
        "Готово. "
        f"Добавлено: {summary.added}; "
        f"обновлено: {summary.updated}; "
        f"пропущено: {summary.skipped}."
    )


if __name__ == "__main__":
    main()
