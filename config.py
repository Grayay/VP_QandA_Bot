from dataclasses import dataclass
from pathlib import Path
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
FAQ_FILE = BASE_DIR / "Вопросы.xlsx"
DB_FILE = BASE_DIR / "bot.sqlite3"
DEFAULT_DUTY_CUTOFF_HOUR = 19
DEFAULT_APP_TIMEZONE = "Europe/Moscow"

CASTINGS_SECTION = "Кастинги/букинги/съемки"
INCOME_SECTION = "Дополнительный доход для моделей"

SECTION_KEYS = {
    "castings": CASTINGS_SECTION,
    "income": INCOME_SECTION,
}

SECTION_TO_KEY = {section: key for key, section in SECTION_KEYS.items()}

NO_ANSWER_BUTTON_TEXT = "Не получилось найти ответ"

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
    chief_booker_ids: set[int]
    duty_cutoff_hour: int = DEFAULT_DUTY_CUTOFF_HOUR
    app_timezone: str = DEFAULT_APP_TIMEZONE
    faq_file: Path = FAQ_FILE
    db_file: Path = DB_FILE


def load_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env", override=True)

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("Не найден BOT_TOKEN. Создайте .env на основе .env.example.")

    return Settings(
        bot_token=bot_token,
        booker_ids=_parse_booker_ids(os.getenv("BOOKER_IDS", "")),
        chief_booker_ids=_parse_booker_ids(os.getenv("CHIEF_BOOKER_IDS", "")),
        duty_cutoff_hour=_parse_duty_cutoff_hour(
            os.getenv("DUTY_CUTOFF_HOUR", str(DEFAULT_DUTY_CUTOFF_HOUR))
        ),
        app_timezone=_parse_app_timezone(os.getenv("APP_TIMEZONE", DEFAULT_APP_TIMEZONE)),
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


def _parse_booker_ids(raw_value: str | None) -> set[int]:
    ids: set[int] = set()
    if not raw_value:
        return ids

    for item in raw_value.split(","):
        value = item.strip().strip("\ufeff").strip("\"'").strip()
        if not value:
            continue
        try:
            ids.add(int(value))
        except ValueError as error:
            raise RuntimeError("BOOKER_IDS должен содержать Telegram ID через запятую.") from error
    return ids


def _parse_duty_cutoff_hour(raw_value: str | None) -> int:
    value = (raw_value or str(DEFAULT_DUTY_CUTOFF_HOUR)).strip().strip("\"'").strip()
    try:
        hour = int(value)
    except ValueError as error:
        raise RuntimeError("DUTY_CUTOFF_HOUR должен быть целым числом от 0 до 23.") from error

    if hour < 0 or hour > 23:
        raise RuntimeError("DUTY_CUTOFF_HOUR должен быть целым числом от 0 до 23.")

    return hour


def _parse_app_timezone(raw_value: str | None) -> str:
    value = (raw_value or DEFAULT_APP_TIMEZONE).strip().strip("\"'").strip()
    if not value:
        value = DEFAULT_APP_TIMEZONE

    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise RuntimeError(f"APP_TIMEZONE содержит неизвестный часовой пояс: {value}") from error

    return value
