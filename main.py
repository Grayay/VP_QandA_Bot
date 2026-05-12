import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import load_settings
from database import Database
from faq_loader import FAQLoaderError, load_faq
from handlers import create_router


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        settings = load_settings()
        faq = load_faq(settings.faq_file)
    except (RuntimeError, FAQLoaderError) as error:
        logging.error("%s", error)
        return

    database = Database(settings.db_file)
    database.init()

    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_router(
            faq=faq,
            database=database,
            authorized_booker_ids=settings.booker_ids,
        )
    )

    logging.info("Бот запущен. Разделов: %s, вопросов: %s", len(faq.sections), len(faq.items))
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
