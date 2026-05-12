from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException


REQUIRED_COLUMNS = ("Вопрос", "Ответ", "Раздел")


class FAQLoaderError(RuntimeError):
    pass


@dataclass(frozen=True)
class FAQItem:
    id: int
    question: str
    answer: str
    section: str


@dataclass(frozen=True)
class FAQData:
    items: list[FAQItem]
    sections: list[str]
    by_section: dict[str, list[FAQItem]]
    by_id: dict[int, FAQItem]


def load_faq(path: Path) -> FAQData:
    if not path.exists():
        raise FAQLoaderError(f"Файл {path.name} не найден в корне проекта.")

    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except (BadZipFile, InvalidFileException, OSError) as error:
        raise FAQLoaderError(f"Не удалось открыть Excel-файл: {error}") from error

    try:
        sheet = workbook.active

        header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), None)
        if not header_row:
            raise FAQLoaderError("В Excel-файле нет строки с заголовками.")

        headers = _trim_empty_tail([_normalize_cell(value) for value in header_row])
        _validate_headers(headers)

        column_index = {name: headers.index(name) for name in REQUIRED_COLUMNS}
        items: list[FAQItem] = []
        sections: list[str] = []
        by_section: dict[str, list[FAQItem]] = {}

        for row_number, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            question = _get_value(row, column_index["Вопрос"])
            answer = _get_value(row, column_index["Ответ"])
            section = _get_value(row, column_index["Раздел"])

            if not question and not answer and not section:
                continue

            if not question or not answer or not section:
                raise FAQLoaderError(
                    f"В строке {row_number} должны быть заполнены Вопрос, Ответ и Раздел."
                )

            item = FAQItem(
                id=len(items) + 1,
                question=question,
                answer=answer,
                section=section,
            )
            items.append(item)

            if section not in by_section:
                by_section[section] = []
                sections.append(section)
            by_section[section].append(item)

        if not items:
            raise FAQLoaderError("В Excel-файле нет FAQ-вопросов.")

        return FAQData(
            items=items,
            sections=sections,
            by_section=by_section,
            by_id={item.id: item for item in items},
        )
    finally:
        workbook.close()


def _validate_headers(headers: list[str]) -> None:
    expected = set(REQUIRED_COLUMNS)
    actual = set(headers)

    missing = expected - actual
    extra = actual - expected

    if missing or extra or len(headers) != len(REQUIRED_COLUMNS):
        details = []
        if missing:
            details.append("нет колонок: " + ", ".join(sorted(missing)))
        if extra:
            details.append("лишние колонки: " + ", ".join(sorted(extra)))
        if len(headers) != len(REQUIRED_COLUMNS) and not details:
            details.append("колонки должны быть ровно: " + ", ".join(REQUIRED_COLUMNS))
        raise FAQLoaderError("Неверные колонки в Excel-файле: " + "; ".join(details))


def _get_value(row: tuple[object, ...], index: int) -> str:
    if index >= len(row):
        return ""
    return _normalize_cell(row[index])


def _normalize_cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _trim_empty_tail(values: list[str]) -> list[str]:
    while values and not values[-1]:
        values.pop()
    return values
