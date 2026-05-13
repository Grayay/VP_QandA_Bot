import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import load_settings
from database import Database
from faq_loader import FAQLoaderError, load_faq
from handlers import (
    create_router,
    forward_pending_after_cutoff_questions,
    resolve_duty_sections,
    run_pending_question_checker,
)


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

    logging.info("Загружено авторизованных BOOKER_IDS: %s", len(settings.booker_ids))
    if not settings.booker_ids:
        logging.warning("BOOKER_IDS пустой. Панель букера не будет доступна никому.")

    database = Database(settings.db_file)
    database.init()
    duty_sections = resolve_duty_sections(faq)

    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_router(
            faq=faq,
            database=database,
            authorized_booker_ids=settings.booker_ids,
            duty_cutoff_hour=settings.duty_cutoff_hour,
            app_timezone=settings.app_timezone,
        )
    )

    await forward_pending_after_cutoff_questions(
        bot=bot,
        database=database,
        duty_sections=duty_sections,
        duty_cutoff_hour=settings.duty_cutoff_hour,
        app_timezone=settings.app_timezone,
    )
    pending_checker = asyncio.create_task(
        run_pending_question_checker(
            bot=bot,
            database=database,
            duty_sections=duty_sections,
            duty_cutoff_hour=settings.duty_cutoff_hour,
            app_timezone=settings.app_timezone,
        )
    )

    logging.info(
        "Бот запущен. Разделов: %s, вопросов: %s, cutoff: %s:00, timezone: %s",
        len(faq.sections),
        len(faq.items),
        settings.duty_cutoff_hour,
        settings.app_timezone,
    )
    try:
        await dispatcher.start_polling(bot)
    finally:
        pending_checker.cancel()
        await asyncio.gather(pending_checker, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
