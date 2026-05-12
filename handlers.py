from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, User

from config import CONTACTS, SECTION_KEYS, section_key_for_section
from database import Database
from faq_loader import FAQData
from keyboards import (
    answer_navigation_keyboard,
    booker_panel_keyboard,
    home_keyboard,
    main_menu_keyboard,
    questions_keyboard,
)


MAX_TELEGRAM_MESSAGE_LENGTH = 4096
NO_BOOKER_ACCESS_MESSAGE = "У вас нет доступа к панели букера."


class QuestionState(StatesGroup):
    waiting_for_question = State()


def create_router(
    faq: FAQData,
    database: Database,
    authorized_booker_ids: set[int],
) -> Router:
    router = Router()
    duty_sections = _resolve_duty_sections(faq)

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(
            "Здравствуйте! Выберите раздел, чтобы найти ответ на вопрос.",
            reply_markup=main_menu_keyboard(
                faq.sections,
                is_booker=_is_authorized_booker(message.from_user, authorized_booker_ids),
            ),
        )

    @router.message(Command("duty_castings"))
    async def duty_castings(message: Message) -> None:
        if not _is_authorized_booker(message.from_user, authorized_booker_ids):
            await message.answer(NO_BOOKER_ACCESS_MESSAGE)
            return

        await _set_duty(
            message=message,
            database=database,
            section_key="castings",
            section=duty_sections["castings"],
        )

    @router.message(Command("duty_income"))
    async def duty_income(message: Message) -> None:
        if not _is_authorized_booker(message.from_user, authorized_booker_ids):
            await message.answer(NO_BOOKER_ACCESS_MESSAGE)
            return

        await _set_duty(
            message=message,
            database=database,
            section_key="income",
            section=duty_sections["income"],
        )

    @router.message(Command("duty_status"))
    async def duty_status(message: Message) -> None:
        if not _is_authorized_booker(message.from_user, authorized_booker_ids):
            await message.answer(NO_BOOKER_ACCESS_MESSAGE)
            return

        await message.answer(_duty_status_text(database, duty_sections))

    @router.callback_query(F.data == "main_menu")
    async def main_menu(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.answer()
        if callback.message:
            await callback.message.edit_text(
                "Выберите раздел, чтобы найти ответ на вопрос.",
                reply_markup=main_menu_keyboard(
                    faq.sections,
                    is_booker=_is_authorized_booker(
                        callback.from_user,
                        authorized_booker_ids,
                    ),
                ),
            )

    @router.callback_query(F.data == "booker_panel")
    async def booker_panel(callback: CallbackQuery) -> None:
        if not await _ensure_booker_callback(callback, authorized_booker_ids):
            return

        if callback.message:
            await callback.message.edit_text(
                "Панель букера:",
                reply_markup=booker_panel_keyboard(),
            )

    @router.callback_query(F.data == "booker:duty_castings")
    async def booker_duty_castings(callback: CallbackQuery) -> None:
        if not await _ensure_booker_callback(callback, authorized_booker_ids):
            return

        if callback.message:
            text = _set_duty_for_user(
                database=database,
                user=callback.from_user,
                section_key="castings",
                section=duty_sections["castings"],
            )
            await callback.message.edit_text(text, reply_markup=booker_panel_keyboard())

    @router.callback_query(F.data == "booker:duty_income")
    async def booker_duty_income(callback: CallbackQuery) -> None:
        if not await _ensure_booker_callback(callback, authorized_booker_ids):
            return

        if callback.message:
            text = _set_duty_for_user(
                database=database,
                user=callback.from_user,
                section_key="income",
                section=duty_sections["income"],
            )
            await callback.message.edit_text(text, reply_markup=booker_panel_keyboard())

    @router.callback_query(F.data == "booker:status")
    async def booker_status(callback: CallbackQuery) -> None:
        if not await _ensure_booker_callback(callback, authorized_booker_ids):
            return

        if callback.message:
            await callback.message.edit_text(
                _duty_status_text(database, duty_sections),
                reply_markup=booker_panel_keyboard(),
            )

    @router.callback_query(F.data == "contacts")
    async def contacts(callback: CallbackQuery) -> None:
        await callback.answer()
        if callback.message:
            await callback.message.edit_text(
                _contacts_text(),
                reply_markup=home_keyboard(),
            )

    @router.callback_query(F.data.startswith("section:"))
    async def section_selected(callback: CallbackQuery) -> None:
        section_index = _parse_index(callback.data, "section:")
        if section_index is None or section_index >= len(faq.sections):
            await _answer_bad_callback(callback)
            return

        await callback.answer()
        section = faq.sections[section_index]
        questions = faq.by_section[section]
        if callback.message:
            await callback.message.edit_text(
                f"Раздел: {section}\n\nВыберите вопрос:",
                reply_markup=questions_keyboard(
                    section_index=section_index,
                    questions=questions,
                    allow_custom_question=section_key_for_section(section) is not None,
                ),
            )

    @router.callback_query(F.data.startswith("question:"))
    async def question_selected(callback: CallbackQuery) -> None:
        question_id = _parse_index(callback.data, "question:")
        item = faq.by_id.get(question_id or -1)
        if not item:
            await _answer_bad_callback(callback)
            return

        await callback.answer()
        if callback.message:
            await _send_long_message(
                callback.message,
                item.answer,
                reply_markup=answer_navigation_keyboard(),
            )

    @router.callback_query(F.data.startswith("ask:"))
    async def ask_custom_question(callback: CallbackQuery, state: FSMContext) -> None:
        section_index = _parse_index(callback.data, "ask:")
        if section_index is None or section_index >= len(faq.sections):
            await _answer_bad_callback(callback)
            return

        section = faq.sections[section_index]
        section_key = section_key_for_section(section)
        if not section_key:
            await _answer_bad_callback(callback)
            return

        await callback.answer()
        await state.set_state(QuestionState.waiting_for_question)
        await state.update_data(section_key=section_key, section=section)

        if callback.message:
            await callback.message.answer(
                "Напишите ваш вопрос одним сообщением. Я сохраню его и передам дежурному букеру, если он назначен.",
                reply_markup=home_keyboard(),
            )

    @router.message(QuestionState.waiting_for_question)
    async def custom_question_received(
        message: Message,
        state: FSMContext,
        bot: Bot,
    ) -> None:
        question = (message.text or "").strip()
        if not question:
            await message.answer("Пожалуйста, отправьте вопрос обычным текстовым сообщением.")
            return

        data = await state.get_data()
        section_key = str(data["section_key"])
        section = str(data["section"])
        user = message.from_user
        full_name = _full_name(user)

        database.save_question(
            user_id=user.id if user else 0,
            username=user.username if user else None,
            full_name=full_name,
            section_key=section_key,
            section=section,
            question=question,
        )

        duty = database.get_duty_booker(section_key)
        await state.clear()

        if not duty:
            await message.answer(
                "Спасибо! Вопрос сохранён. Сейчас дежурный букер для этого раздела не назначен.",
                reply_markup=home_keyboard(),
            )
            return

        try:
            await bot.send_message(
                chat_id=duty["user_id"],
                text=_duty_question_text(
                    section=section,
                    question=question,
                    user=user,
                ),
            )
        except TelegramAPIError:
            await message.answer(
                "Спасибо! Вопрос сохранён, но я не смогла отправить его дежурному букеру. Он сможет увидеть вопрос в базе.",
                reply_markup=home_keyboard(),
            )
            return

        await message.answer(
            "Спасибо! Вопрос сохранён и отправлен дежурному букеру.",
            reply_markup=home_keyboard(),
        )

    @router.message()
    async def unknown_message(message: Message) -> None:
        await message.answer("Нажмите /start, чтобы открыть меню FAQ.")

    return router


async def _set_duty(
    *,
    message: Message,
    database: Database,
    section_key: str,
    section: str,
) -> None:
    user = message.from_user
    if not user:
        await message.answer("Не смогла определить пользователя Telegram.")
        return

    await message.answer(
        _set_duty_for_user(
            database=database,
            user=user,
            section_key=section_key,
            section=section,
        )
    )


def _set_duty_for_user(
    *,
    database: Database,
    user: User,
    section_key: str,
    section: str,
) -> str:
    database.set_duty_booker(
        section_key=section_key,
        section=section,
        user_id=user.id,
        username=user.username,
        full_name=_full_name(user),
    )
    return f"Готово! Вы назначены дежурным букером для раздела «{section}»."


def _duty_status_text(database: Database, duty_sections: dict[str, str]) -> str:
    duties = database.get_all_duty_bookers()
    lines = ["Текущие дежурные букеры:"]

    for section_key, section in duty_sections.items():
        duty = duties.get(section_key)
        if duty:
            lines.append(f"• {section}: {_format_user(duty)}")
        else:
            lines.append(f"• {section}: не назначен")

    return "\n".join(lines)


def _contacts_text() -> str:
    lines = ["С кем связаться:"]
    for item in CONTACTS:
        lines.append(f"\n{item['topic']}\n{item['person']}\n{item['username']}")
    return "\n".join(lines)


def _duty_question_text(*, section: str, question: str, user: User | None) -> str:
    user_line = "Пользователь не определён"
    if user:
        username = f"@{user.username}" if user.username else "без username"
        user_line = f"{_full_name(user)} ({username}, ID: {user.id})"

    return (
        "Новый вопрос от модели.\n\n"
        f"Раздел: {section}\n"
        f"Модель: {user_line}\n\n"
        f"Вопрос:\n{question}"
    )


async def _send_long_message(
    message: Message,
    text: str,
    *,
    reply_markup,
) -> None:
    chunks = [
        text[index : index + MAX_TELEGRAM_MESSAGE_LENGTH]
        for index in range(0, len(text), MAX_TELEGRAM_MESSAGE_LENGTH)
    ]
    for index, chunk in enumerate(chunks):
        await message.answer(
            chunk,
            reply_markup=reply_markup if index == len(chunks) - 1 else None,
        )


async def _answer_bad_callback(callback: CallbackQuery) -> None:
    await callback.answer("Не удалось открыть этот пункт. Вернитесь в главное меню.", show_alert=True)


def _parse_index(data: str | None, prefix: str) -> int | None:
    if not data or not data.startswith(prefix):
        return None
    try:
        return int(data.removeprefix(prefix))
    except ValueError:
        return None


async def _ensure_booker_callback(
    callback: CallbackQuery,
    authorized_booker_ids: set[int],
) -> bool:
    if _is_authorized_booker(callback.from_user, authorized_booker_ids):
        await callback.answer()
        return True

    await callback.answer(NO_BOOKER_ACCESS_MESSAGE, show_alert=True)
    return False


def _is_authorized_booker(user: User | None, authorized_booker_ids: set[int]) -> bool:
    return bool(user and user.id in authorized_booker_ids)


def _resolve_duty_sections(faq: FAQData) -> dict[str, str]:
    sections = dict(SECTION_KEYS)

    for section_key, default_section in SECTION_KEYS.items():
        if default_section in faq.sections:
            sections[section_key] = default_section
            continue

        for section in faq.sections:
            if section_key_for_section(section) == section_key:
                sections[section_key] = section
                break

    return sections


def _full_name(user: User | None) -> str:
    if not user:
        return "Пользователь не определён"
    return " ".join(part for part in [user.first_name, user.last_name] if part)


def _format_user(duty: dict) -> str:
    username = f"@{duty['username']}" if duty.get("username") else "без username"
    return f"{duty['full_name']} ({username}, ID: {duty['user_id']})"
