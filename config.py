from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
FAQ_FILE = BASE_DIR / "Вопросы.xlsx"
DB_FILE = BASE_DIR / "bot.sqlite3"

CASTINGS_SECTION = "Кастинги/букинги/съемки"
INCOME_SECTION = "Дополнительный доход для моделей"

SECTION_KEYS = {
    "castings": CASTINGS_SECTION,
    "income": INCOME_SECTION,
}

SECTION_TO_KEY = {section: key for key, section in SECTION_KEYS.items()}

NO_ANSWER_BUTTON_TEXT = "Не нашла ответ на нужный вопрос"

CONTACTS = [
    {
        "topic": "Подписание и расторжение контрактов, коммуникация по вопросам развития, журналам",
        "person": "🤍 Майя — старший менеджер по развитию моделей",
        "username": "@mayya_drzh",
    },
    {
        "topic": "Организация тестов, снепов и творчества, запись в салоны красоты и спортзал КОМЕТА",
        "person": "🤍 Аня — модельный менеджер",
        "username": "@aannett_b",
    },
    {
        "topic": "Работа с портфолио моделей, запрос материалов у клиентов",
        "person": "🤍 Ира — медиа редактор",
        "username": "@Tsayskaya16",
    },
    {
        "topic": "Запись на еженедельную йогу",
        "person": "🤍 Ульяна — ассистент отдела развития",
        "username": "@ulyanavishnyakova",
    },
    {
        "topic": "Пиар моделей в соц сетях компании",
        "person": "🤍 Ксения — head of SMM",
        "username": "@niksenya",
    },
]


@dataclass(frozen=True)
class Settings:
    bot_token: str
    booker_ids: set[int]
    faq_file: Path = FAQ_FILE
    db_file: Path = DB_FILE


def load_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env")

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("Не найден BOT_TOKEN. Создайте .env на основе .env.example.")

    return Settings(
        bot_token=bot_token,
        booker_ids=_parse_booker_ids(os.getenv("BOOKER_IDS", "")),
    )


def section_key_for_section(section: str) -> str | None:
    normalized = section.lower().replace("ё", "е")

    if any(keyword in normalized for keyword in ("кастинг", "букинг", "съем")):
        return "castings"

    if "дополнительный доход" in normalized or (
        "доп" in normalized and "доход" in normalized
    ):
        return "income"

    return None


def _parse_booker_ids(raw_value: str) -> set[int]:
    ids: set[int] = set()
    for item in raw_value.split(","):
        value = item.strip()
        if not value:
            continue
        try:
            ids.add(int(value))
        except ValueError as error:
            raise RuntimeError("BOOKER_IDS должен содержать Telegram ID через запятую.") from error
    return ids
