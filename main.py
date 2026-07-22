import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import load_settings
from database import Database
from handlers import (
    create_router,
    flush_pending_questions_to_booker,
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
    except RuntimeError as error:
        logging.error("%s", error)
        return

    logging.info("Загружено авторизованных BOOKER_IDS: %s", len(settings.booker_ids))
    logging.info("Загружено CHIEF_BOOKER_IDS: %s", len(settings.chief_booker_ids))

    database = Database(settings.db_file)
    database.init()
    imported_bookers = database.import_bookers_from_config(
        settings.booker_ids,
        chief_booker_ids=settings.chief_booker_ids,
    )
    if imported_bookers:
        logging.info("Импортировано обычных букеров из BOOKER_IDS: %s", imported_bookers)
    if not database.list_bookers() and not settings.chief_booker_ids:
        logging.warning("В базе нет обычных букеров и CHIEF_BOOKER_IDS пустой. Панель букера не будет доступна никому.")
    faq = database.get_active_faq_data()
    if not faq.items:
        logging.warning(
            "В базе нет активных FAQ-вопросов. Импортируйте Excel через scripts/import_faq_from_excel.py."
        )
    duty_sections = resolve_duty_sections(faq)

    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_router(
            faq=faq,
            database=database,
            authorized_booker_ids=settings.booker_ids,
            chief_booker_ids=settings.chief_booker_ids,
            duty_cutoff_hour=settings.duty_cutoff_hour,
            app_timezone=settings.app_timezone,
            workday_start_hour=settings.workday_start_hour,
            workday_end_hour=settings.workday_end_hour,
            workdays=settings.workdays,
        )
    )

    await flush_pending_questions_to_booker(
        bot=bot,
        database=database,
        duty_sections=duty_sections,
        app_timezone=settings.app_timezone,
        workday_start_hour=settings.workday_start_hour,
        workday_end_hour=settings.workday_end_hour,
        workdays=settings.workdays,
    )
    pending_checker = asyncio.create_task(
        run_pending_question_checker(
            bot=bot,
            database=database,
            duty_sections=duty_sections,
            app_timezone=settings.app_timezone,
            workday_start_hour=settings.workday_start_hour,
            workday_end_hour=settings.workday_end_hour,
            workdays=settings.workdays,
        )
    )

    logging.info(
        "Бот запущен. Разделов: %s, вопросов: %s, рабочее время: %s:00-%s:00, timezone: %s",
        len(faq.sections),
        len(faq.items),
        settings.workday_start_hour,
        settings.workday_end_hour,
        settings.app_timezone,
    )
    try:
        await dispatcher.start_polling(bot)
    finally:
        pending_checker.cancel()
        await asyncio.gather(pending_checker, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
