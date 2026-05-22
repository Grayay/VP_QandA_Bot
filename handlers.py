from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
import logging
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, User

from config import CONTACTS, SECTION_KEYS, section_key_for_section
from database import Database
from faq_loader import FAQData, FAQItem, is_transfer_to_booker_answer
from keyboards import (
    answer_navigation_keyboard,
    booker_panel_keyboard,
    faq_add_confirm_keyboard,
    faq_add_step_keyboard,
    faq_delete_confirm_keyboard,
    faq_edit_entry_keyboard,
    faq_edit_step_keyboard,
    faq_management_keyboard,
    faq_management_questions_keyboard,
    faq_management_sections_keyboard,
    home_keyboard,
    main_menu_keyboard,
    questions_keyboard,
)


MAX_TELEGRAM_MESSAGE_LENGTH = 4096
NO_BOOKER_ACCESS_MESSAGE = "У вас нет доступа к панели букера."
NO_CHIEF_BOOKER_ACCESS_MESSAGE = "У вас нет доступа к управлению вопросами."
PENDING_CHECK_INTERVAL_SECONDS = 300
WELCOME_MESSAGE = (
    "Привет! Я — VP-бот 🤍\n"
    "Помогу быстро найти ответ на вопрос или понять, к кому обратиться.\n\n"
    "Выбери нужный раздел ниже:"
)
LOGGER = logging.getLogger(__name__)
_PENDING_FORWARD_LOCK = asyncio.Lock()


class QuestionState(StatesGroup):
    waiting_for_question = State()


class FAQManagementState(StatesGroup):
    adding_section = State()
    adding_department = State()
    adding_question = State()
    adding_answer = State()
    confirming_add = State()
    editing_value = State()


FAQ_FIELD_LABELS = {
    "section": "раздел",
    "department": "отдел",
    "question": "вопрос",
    "answer": "ответ",
}


def create_router(
    faq: FAQData,
    database: Database,
    authorized_booker_ids: set[int],
    chief_booker_ids: set[int],
    duty_cutoff_hour: int,
    app_timezone: str,
) -> Router:
    router = Router()
    duty_sections = resolve_duty_sections(faq)

    def current_faq() -> FAQData:
        return database.get_active_faq_data()

    async def show_faq_management_menu(
        callback: CallbackQuery,
        *,
        state: FSMContext | None = None,
        text: str = "Управление вопросами:",
    ) -> None:
        if state:
            await state.clear()
        if callback.message:
            await callback.message.edit_text(text, reply_markup=faq_management_keyboard())

    async def show_faq_management_sections(
        callback: CallbackQuery,
        *,
        action: str,
    ) -> None:
        faq_data = current_faq()
        if not faq_data.sections:
            if callback.message:
                await callback.message.edit_text(
                    "Пока нет активных вопросов.",
                    reply_markup=faq_management_keyboard(),
                )
            return

        action_title = "редактирования" if action == "edit" else "удаления"
        if callback.message:
            await callback.message.edit_text(
                f"Выберите раздел для {action_title}:",
                reply_markup=faq_management_sections_keyboard(
                    action=action,
                    sections=faq_data.sections,
                ),
            )

    async def show_faq_management_questions(
        callback: CallbackQuery,
        *,
        action: str,
        section_index: int,
    ) -> None:
        faq_data = current_faq()
        if section_index >= len(faq_data.sections):
            await _answer_bad_callback(callback)
            return

        section = faq_data.sections[section_index]
        questions = faq_data.by_section.get(section, [])
        if not questions:
            if callback.message:
                await callback.message.edit_text(
                    "В этом разделе нет активных вопросов.",
                    reply_markup=faq_management_sections_keyboard(
                        action=action,
                        sections=faq_data.sections,
                    ),
                )
            return

        if callback.message:
            await callback.message.edit_text(
                f"Раздел: {section}\n\nВыберите вопрос:",
                reply_markup=faq_management_questions_keyboard(
                    action=action,
                    section_index=section_index,
                    questions=questions,
                ),
            )

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        faq_data = current_faq()
        await message.answer(
            WELCOME_MESSAGE,
            reply_markup=main_menu_keyboard(
                faq_data.sections,
                is_booker=_is_authorized_booker(message.from_user, authorized_booker_ids),
                is_chief_booker=_is_chief_booker(message.from_user, chief_booker_ids),
            ),
        )

    @router.message(Command("my_id"))
    async def my_id(message: Message) -> None:
        if not message.from_user:
            await message.answer("Не смогла определить ваш Telegram ID.")
            return

        await message.answer(f"Ваш Telegram ID: {message.from_user.id}")

    @router.message(Command("booker_debug"))
    async def booker_debug(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else None
        is_authorized = _is_authorized_booker(message.from_user, authorized_booker_ids)
        is_chief = _is_chief_booker(message.from_user, chief_booker_ids)
        user_id_text = str(user_id) if user_id is not None else "не удалось определить"
        status_text = "да" if is_authorized else "нет"
        chief_status_text = "да" if is_chief else "нет"

        await message.answer(
            "\n".join(
                [
                    f"Ваш Telegram ID: {user_id_text}",
                    f"Вы авторизованы как букер: {status_text}",
                    f"Вы авторизованы как главный букер: {chief_status_text}",
                    f"Загружено BOOKER_IDS: {len(authorized_booker_ids)}",
                    f"Загружено CHIEF_BOOKER_IDS: {len(chief_booker_ids)}",
                ]
            )
        )

    @router.message(Command("duty_castings"))
    async def duty_castings(message: Message, bot: Bot) -> None:
        if not _is_authorized_booker(message.from_user, authorized_booker_ids):
            await message.answer(NO_BOOKER_ACCESS_MESSAGE)
            return

        await _set_duty(
            bot=bot,
            message=message,
            database=database,
            section_key="castings",
            section=duty_sections["castings"],
            duty_sections=duty_sections,
            duty_cutoff_hour=duty_cutoff_hour,
            app_timezone=app_timezone,
        )

    @router.message(Command("duty_income"))
    async def duty_income(message: Message, bot: Bot) -> None:
        if not _is_authorized_booker(message.from_user, authorized_booker_ids):
            await message.answer(NO_BOOKER_ACCESS_MESSAGE)
            return

        await _set_duty(
            bot=bot,
            message=message,
            database=database,
            section_key="income",
            section=duty_sections["income"],
            duty_sections=duty_sections,
            duty_cutoff_hour=duty_cutoff_hour,
            app_timezone=app_timezone,
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
        faq_data = current_faq()
        if callback.message:
            await callback.message.edit_text(
                "Выберите раздел, чтобы найти ответ на вопрос.",
                reply_markup=main_menu_keyboard(
                    faq_data.sections,
                    is_booker=_is_authorized_booker(
                        callback.from_user,
                        authorized_booker_ids,
                    ),
                    is_chief_booker=_is_chief_booker(
                        callback.from_user,
                        chief_booker_ids,
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
    async def booker_duty_castings(callback: CallbackQuery, bot: Bot) -> None:
        if not await _ensure_booker_callback(callback, authorized_booker_ids):
            return

        if callback.message:
            text = _set_duty_for_user(
                database=database,
                user=callback.from_user,
                section_key="castings",
                section=duty_sections["castings"],
                app_timezone=app_timezone,
            )
            await forward_pending_after_cutoff_questions(
                bot=bot,
                database=database,
                duty_sections=duty_sections,
                duty_cutoff_hour=duty_cutoff_hour,
                app_timezone=app_timezone,
                section_keys={"castings"},
            )
            await callback.message.edit_text(text, reply_markup=booker_panel_keyboard())

    @router.callback_query(F.data == "booker:duty_income")
    async def booker_duty_income(callback: CallbackQuery, bot: Bot) -> None:
        if not await _ensure_booker_callback(callback, authorized_booker_ids):
            return

        if callback.message:
            text = _set_duty_for_user(
                database=database,
                user=callback.from_user,
                section_key="income",
                section=duty_sections["income"],
                app_timezone=app_timezone,
            )
            await forward_pending_after_cutoff_questions(
                bot=bot,
                database=database,
                duty_sections=duty_sections,
                duty_cutoff_hour=duty_cutoff_hour,
                app_timezone=app_timezone,
                section_keys={"income"},
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

    @router.callback_query(F.data == "faqm:menu")
    async def faq_management_menu(callback: CallbackQuery, state: FSMContext) -> None:
        if not await _ensure_chief_booker_callback(callback, chief_booker_ids):
            return

        await show_faq_management_menu(callback, state=state)

    @router.callback_query(F.data == "faqm:cancel")
    async def faq_management_cancel(callback: CallbackQuery, state: FSMContext) -> None:
        if not await _ensure_chief_booker_callback(callback, chief_booker_ids):
            return

        await show_faq_management_menu(callback, state=state, text="Действие отменено.")

    @router.callback_query(F.data == "faqm:add")
    async def faq_add_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not await _ensure_chief_booker_callback(callback, chief_booker_ids):
            return

        await state.clear()
        await state.set_state(FAQManagementState.adding_section)
        await state.update_data(section="", department="", question="", answer="")
        if callback.message:
            await callback.message.edit_text(
                "Введите раздел для нового вопроса.",
                reply_markup=faq_add_step_keyboard(),
            )

    @router.callback_query(F.data == "faqm:add_back")
    async def faq_add_back(callback: CallbackQuery, state: FSMContext) -> None:
        if not await _ensure_chief_booker_callback(callback, chief_booker_ids):
            return

        current_state = await state.get_state()
        if current_state == FAQManagementState.adding_section.state:
            await show_faq_management_menu(callback, state=state)
            return
        if current_state == FAQManagementState.adding_department.state:
            await state.set_state(FAQManagementState.adding_section)
            text = "Введите раздел для нового вопроса."
        elif current_state == FAQManagementState.adding_question.state:
            await state.set_state(FAQManagementState.adding_department)
            text = "Введите отдел или отправьте «-», чтобы оставить поле пустым."
        elif current_state == FAQManagementState.adding_answer.state:
            await state.set_state(FAQManagementState.adding_question)
            text = "Введите текст вопроса."
        elif current_state == FAQManagementState.confirming_add.state:
            await state.set_state(FAQManagementState.adding_answer)
            text = "Введите ответ."
        else:
            await show_faq_management_menu(callback, state=state)
            return

        if callback.message:
            await callback.message.edit_text(text, reply_markup=faq_add_step_keyboard())

    @router.message(FAQManagementState.adding_section)
    async def faq_add_section_received(message: Message, state: FSMContext) -> None:
        if not await _ensure_chief_booker_message(message, chief_booker_ids, state):
            return

        section = _clean_message_text(message)
        if not section:
            await message.answer("Раздел не должен быть пустым.", reply_markup=faq_add_step_keyboard())
            return

        await state.update_data(section=section)
        await state.set_state(FAQManagementState.adding_department)
        await message.answer(
            "Введите отдел или отправьте «-», чтобы оставить поле пустым.",
            reply_markup=faq_add_step_keyboard(),
        )

    @router.message(FAQManagementState.adding_department)
    async def faq_add_department_received(message: Message, state: FSMContext) -> None:
        if not await _ensure_chief_booker_message(message, chief_booker_ids, state):
            return

        department = _empty_marker_to_none(_clean_message_text(message)) or ""
        await state.update_data(department=department)
        await state.set_state(FAQManagementState.adding_question)
        await message.answer("Введите текст вопроса.", reply_markup=faq_add_step_keyboard())

    @router.message(FAQManagementState.adding_question)
    async def faq_add_question_received(message: Message, state: FSMContext) -> None:
        if not await _ensure_chief_booker_message(message, chief_booker_ids, state):
            return

        question = _clean_message_text(message)
        if not question:
            await message.answer("Вопрос не должен быть пустым.", reply_markup=faq_add_step_keyboard())
            return

        await state.update_data(question=question)
        await state.set_state(FAQManagementState.adding_answer)
        await message.answer("Введите ответ.", reply_markup=faq_add_step_keyboard())

    @router.message(FAQManagementState.adding_answer)
    async def faq_add_answer_received(message: Message, state: FSMContext) -> None:
        if not await _ensure_chief_booker_message(message, chief_booker_ids, state):
            return

        answer = _clean_message_text(message)
        if not answer:
            await message.answer("Ответ не должен быть пустым.", reply_markup=faq_add_step_keyboard())
            return

        await state.update_data(answer=answer)
        await state.set_state(FAQManagementState.confirming_add)
        data = await state.get_data()
        await message.answer(
            _faq_entry_preview_text(data),
            reply_markup=faq_add_confirm_keyboard(),
        )

    @router.callback_query(F.data == "faqm:add_save")
    async def faq_add_save(callback: CallbackQuery, state: FSMContext) -> None:
        if not await _ensure_chief_booker_callback(callback, chief_booker_ids):
            return

        data = await state.get_data()
        try:
            database.create_faq_entry(
                section=str(data.get("section") or ""),
                department=str(data.get("department") or ""),
                question=str(data.get("question") or ""),
                answer=str(data.get("answer") or ""),
                created_by=callback.from_user.id if callback.from_user else None,
            )
        except ValueError:
            await _answer_bad_callback(callback)
            return

        await show_faq_management_menu(callback, state=state, text="Вопрос сохранён.")

    @router.callback_query(F.data == "faqm:edit")
    async def faq_edit_sections(callback: CallbackQuery, state: FSMContext) -> None:
        if not await _ensure_chief_booker_callback(callback, chief_booker_ids):
            return

        await state.clear()
        await show_faq_management_sections(callback, action="edit")

    @router.callback_query(F.data.startswith("faqm:edit_section:"))
    async def faq_edit_questions(callback: CallbackQuery) -> None:
        if not await _ensure_chief_booker_callback(callback, chief_booker_ids):
            return

        section_index = _parse_index(callback.data, "faqm:edit_section:")
        if section_index is None:
            await _answer_bad_callback(callback)
            return

        await show_faq_management_questions(
            callback,
            action="edit",
            section_index=section_index,
        )

    @router.callback_query(F.data.startswith("faqm:edit_item:"))
    async def faq_edit_item(callback: CallbackQuery, state: FSMContext) -> None:
        if not await _ensure_chief_booker_callback(callback, chief_booker_ids):
            return

        await state.clear()
        token = (callback.data or "").removeprefix("faqm:edit_item:")
        item = database.get_faq_entry_by_token(token)
        if not item:
            await _answer_bad_callback(callback)
            return

        if callback.message:
            await callback.message.edit_text(
                _faq_item_details_text(item),
                reply_markup=faq_edit_entry_keyboard(item.token),
            )

    @router.callback_query(F.data.startswith("faqm:edit_field:"))
    async def faq_edit_field(callback: CallbackQuery, state: FSMContext) -> None:
        if not await _ensure_chief_booker_callback(callback, chief_booker_ids):
            return

        parts = (callback.data or "").split(":")
        if len(parts) != 4 or parts[3] not in FAQ_FIELD_LABELS:
            await _answer_bad_callback(callback)
            return

        token = parts[2]
        field = parts[3]
        item = database.get_faq_entry_by_token(token)
        if not item:
            await _answer_bad_callback(callback)
            return

        await state.set_state(FAQManagementState.editing_value)
        await state.update_data(edit_token=token, edit_field=field)
        empty_hint = " Можно отправить «-», чтобы оставить поле пустым." if field == "department" else ""
        if callback.message:
            await callback.message.edit_text(
                f"Введите новый {FAQ_FIELD_LABELS[field]}.{empty_hint}",
                reply_markup=faq_edit_step_keyboard(token),
            )

    @router.message(FAQManagementState.editing_value)
    async def faq_edit_value_received(message: Message, state: FSMContext) -> None:
        if not await _ensure_chief_booker_message(message, chief_booker_ids, state):
            return

        data = await state.get_data()
        token = str(data.get("edit_token") or "")
        field = str(data.get("edit_field") or "")
        item = database.get_faq_entry_by_token(token)
        if not item or field not in FAQ_FIELD_LABELS:
            await state.clear()
            await message.answer(
                "Не удалось обновить вопрос. Откройте управление вопросами заново.",
                reply_markup=faq_management_keyboard(),
            )
            return

        value = _clean_message_text(message)
        if field == "department":
            value = _empty_marker_to_none(value) or ""
        elif not value:
            await message.answer(
                f"Новый {FAQ_FIELD_LABELS[field]} не должен быть пустым.",
                reply_markup=faq_edit_step_keyboard(token),
            )
            return

        database.update_faq_entry(
            item.id,
            updated_by=message.from_user.id if message.from_user else None,
            **{field: value},
        )
        await state.clear()
        updated_item = database.get_faq_entry_by_token(token)
        if not updated_item:
            await message.answer(
                "Вопрос обновлён, но сейчас он не активен.",
                reply_markup=faq_management_keyboard(),
            )
            return

        await message.answer(
            "Изменения сохранены.\n\n" + _faq_item_details_text(updated_item),
            reply_markup=faq_edit_entry_keyboard(updated_item.token),
        )

    @router.callback_query(F.data == "faqm:delete")
    async def faq_delete_sections(callback: CallbackQuery, state: FSMContext) -> None:
        if not await _ensure_chief_booker_callback(callback, chief_booker_ids):
            return

        await state.clear()
        await show_faq_management_sections(callback, action="delete")

    @router.callback_query(F.data.startswith("faqm:delete_section:"))
    async def faq_delete_questions(callback: CallbackQuery) -> None:
        if not await _ensure_chief_booker_callback(callback, chief_booker_ids):
            return

        section_index = _parse_index(callback.data, "faqm:delete_section:")
        if section_index is None:
            await _answer_bad_callback(callback)
            return

        await show_faq_management_questions(
            callback,
            action="delete",
            section_index=section_index,
        )

    @router.callback_query(F.data.startswith("faqm:delete_item:"))
    async def faq_delete_item(callback: CallbackQuery) -> None:
        if not await _ensure_chief_booker_callback(callback, chief_booker_ids):
            return

        token = (callback.data or "").removeprefix("faqm:delete_item:")
        item = database.get_faq_entry_by_token(token)
        if not item:
            await _answer_bad_callback(callback)
            return

        if callback.message:
            await callback.message.edit_text(
                _faq_item_details_text(item) + "\n\nУдалить этот вопрос и ответ?",
                reply_markup=faq_delete_confirm_keyboard(item.token),
            )

    @router.callback_query(F.data.startswith("faqm:delete_confirm:"))
    async def faq_delete_confirm(callback: CallbackQuery) -> None:
        if not await _ensure_chief_booker_callback(callback, chief_booker_ids):
            return

        token = (callback.data or "").removeprefix("faqm:delete_confirm:")
        item = database.get_faq_entry_by_token(token)
        if not item:
            await _answer_bad_callback(callback)
            return

        database.soft_delete_faq_entry(
            item.id,
            updated_by=callback.from_user.id if callback.from_user else None,
        )
        if callback.message:
            await callback.message.edit_text(
                "Вопрос удалён из FAQ.",
                reply_markup=faq_management_keyboard(),
            )

    @router.callback_query(F.data.startswith("faqm:"))
    async def faq_management_unknown(callback: CallbackQuery) -> None:
        if not _is_chief_booker(callback.from_user, chief_booker_ids):
            await callback.answer(NO_CHIEF_BOOKER_ACCESS_MESSAGE, show_alert=True)
            return

        await _answer_bad_callback(callback)

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
        faq_data = current_faq()
        section_index = _parse_index(callback.data, "section:")
        if section_index is None or section_index >= len(faq_data.sections):
            await _answer_bad_callback(callback)
            return

        await callback.answer()
        section = faq_data.sections[section_index]
        questions = faq_data.by_section[section]
        if callback.message:
            await callback.message.edit_text(
                f"Раздел: {section}\n\nВыберите вопрос:",
                reply_markup=questions_keyboard(
                    section_index=section_index,
                    questions=questions,
                ),
            )

    @router.callback_query(F.data.startswith("question:"))
    async def question_selected(callback: CallbackQuery, state: FSMContext) -> None:
        token = (callback.data or "").removeprefix("question:")
        item = database.get_faq_entry_by_token(token)
        if not item:
            await _answer_bad_callback(callback)
            return

        if not callback.message:
            await callback.answer()
            return

        if _is_booker_not_answering_question(item.question) or is_transfer_to_booker_answer(item.answer):
            await _start_custom_question_flow(
                callback=callback,
                state=state,
                item=item,
            )
            return

        await callback.answer()
        await _send_long_message(
            callback.message,
            item.answer,
            reply_markup=answer_navigation_keyboard(),
        )

    @router.callback_query(F.data.startswith("ask:"))
    async def ask_custom_question(callback: CallbackQuery, state: FSMContext) -> None:
        faq_data = current_faq()
        section_index = _parse_index(callback.data, "ask:")
        if section_index is None or section_index >= len(faq_data.sections):
            await _answer_bad_callback(callback)
            return

        section = faq_data.sections[section_index]
        await _start_custom_question_flow(
            callback=callback,
            state=state,
            section=section,
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
        now = _now(app_timezone)
        after_cutoff = _is_after_cutoff(now, duty_cutoff_hour)
        duty = database.get_duty_booker(section_key)

        question_id = database.save_question(
            user_id=user.id if user else 0,
            username=user.username if user else None,
            full_name=full_name,
            section_key=section_key,
            section=section,
            question=question,
            created_at=_datetime_to_storage(now),
            after_cutoff=after_cutoff,
        )
        await state.clear()

        if after_cutoff:
            await message.answer(
                "Вопрос сохранён. После 19:00 такие вопросы передаются дежурному букеру следующего дня.",
                reply_markup=home_keyboard(),
            )
            return

        if not duty:
            await message.answer(
                "Вопрос сохранён. Сейчас дежурный букер не назначен, но команда сможет вернуться к нему позже.",
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
            LOGGER.exception("Не удалось отправить вопрос %s дежурному букеру.", question_id)
            await message.answer(
                "Вопрос сохранён. Не смогла отправить его дежурному букеру, но команда сможет вернуться к нему позже.",
                reply_markup=home_keyboard(),
            )
            return

        database.mark_question_forwarded(
            question_id=question_id,
            routed_to_booker_id=int(duty["user_id"]),
            routed_at=_datetime_to_storage(_now(app_timezone)),
        )
        await message.answer(
            "Вопрос передан дежурному букеру.",
            reply_markup=home_keyboard(),
        )

    @router.message()
    async def unknown_message(message: Message) -> None:
        await message.answer("Нажмите /start, чтобы открыть меню FAQ.")

    return router


async def _set_duty(
    *,
    bot: Bot,
    message: Message,
    database: Database,
    section_key: str,
    section: str,
    duty_sections: dict[str, str],
    duty_cutoff_hour: int,
    app_timezone: str,
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
            app_timezone=app_timezone,
        )
    )
    await forward_pending_after_cutoff_questions(
        bot=bot,
        database=database,
        duty_sections=duty_sections,
        duty_cutoff_hour=duty_cutoff_hour,
        app_timezone=app_timezone,
        section_keys={section_key},
    )


def _set_duty_for_user(
    *,
    database: Database,
    user: User,
    section_key: str,
    section: str,
    app_timezone: str,
) -> str:
    database.set_duty_booker(
        section_key=section_key,
        section=section,
        user_id=user.id,
        username=user.username,
        full_name=_full_name(user),
        updated_at=_datetime_to_storage(_now(app_timezone)),
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
    return "\n\n".join(
        f"{item['topic']}\n{item['person']}\n{item['username']}"
        for item in CONTACTS
    )


async def _start_custom_question_flow(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    section: str | None = None,
    item: FAQItem | None = None,
) -> None:
    routing_section = section or (item.section if item else None) or ""
    routing_department = item.department if item else None
    section_key = section_key_for_section(routing_section)
    if not section_key and routing_department:
        section_key = section_key_for_section(routing_department)

    if not section_key:
        await state.clear()
        await callback.answer()
        if callback.message:
            await callback.message.edit_text(
                _contacts_text(),
                reply_markup=home_keyboard(),
            )
        return

    await callback.answer()
    await state.set_state(QuestionState.waiting_for_question)
    await state.update_data(
        section_key=section_key,
        section=routing_section or routing_department or "FAQ",
    )

    if callback.message:
        await callback.message.answer(
            "Напишите свой вопрос одним сообщением.",
            reply_markup=home_keyboard(),
        )


def _faq_entry_preview_text(data: dict) -> str:
    return "\n".join(
        [
            "Проверьте новый вопрос:",
            "",
            f"Раздел: {_format_optional(data.get('section'))}",
            f"Отдел: {_format_optional(data.get('department'))}",
            f"Вопрос: {_truncate(str(data.get('question') or ''))}",
            f"Ответ: {_truncate(str(data.get('answer') or ''))}",
        ]
    )


def _faq_item_details_text(item: FAQItem) -> str:
    return "\n".join(
        [
            "Текущий вопрос:",
            "",
            f"Раздел: {_format_optional(item.section)}",
            f"Отдел: {_format_optional(item.department)}",
            f"Вопрос: {_truncate(item.question)}",
            f"Ответ: {_truncate(item.answer)}",
        ]
    )


def _format_optional(value: object) -> str:
    text = str(value or "").strip()
    return text or "—"


def _truncate(value: str, limit: int = 1200) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _clean_message_text(message: Message) -> str:
    return (message.text or "").strip()


def _empty_marker_to_none(value: str) -> str | None:
    cleaned = value.strip()
    if cleaned in {"", "-", "—", "нет", "Нет", "пропустить", "Пропустить"}:
        return None
    return cleaned


def _duty_question_text(*, section: str, question: str, user: User | None) -> str:
    if user:
        username = f"@{user.username}" if user.username else "без username"
        user_line = f"{_full_name(user)} ({username}, ID: {user.id})"
    else:
        user_line = "Пользователь не определён"

    return _duty_question_text_with_user_line(
        section=section,
        question=question,
        user_line=user_line,
    )


def _stored_duty_question_text(question: dict) -> str:
    username = f"@{question['username']}" if question.get("username") else "без username"
    user_line = f"{question['full_name']} ({username}, ID: {question['user_id']})"
    return _duty_question_text_with_user_line(
        section=str(question["section"]),
        question=str(question["question_text"]),
        user_line=user_line,
    )


def _duty_question_text_with_user_line(
    *,
    section: str,
    question: str,
    user_line: str,
) -> str:
    return (
        "Новый вопрос от модели.\n\n"
        f"Раздел: {section}\n"
        f"Модель: {user_line}\n\n"
        f"Вопрос:\n{question}"
    )


async def run_pending_question_checker(
    *,
    bot: Bot,
    database: Database,
    duty_sections: dict[str, str],
    duty_cutoff_hour: int,
    app_timezone: str,
    interval_seconds: int = PENDING_CHECK_INTERVAL_SECONDS,
) -> None:
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await forward_pending_after_cutoff_questions(
                bot=bot,
                database=database,
                duty_sections=duty_sections,
                duty_cutoff_hour=duty_cutoff_hour,
                app_timezone=app_timezone,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Ошибка при проверке отложенных вопросов.")


async def forward_pending_after_cutoff_questions(
    *,
    bot: Bot,
    database: Database,
    duty_sections: dict[str, str],
    duty_cutoff_hour: int,
    app_timezone: str,
    section_keys: set[str] | None = None,
) -> int:
    async with _PENDING_FORWARD_LOCK:
        now = _now(app_timezone)
        duties = database.get_all_duty_bookers()
        questions = database.get_pending_after_cutoff_questions(section_keys=section_keys)
        forwarded_count = 0

        for question in questions:
            section_key = str(question["section_key"])
            if section_key not in duty_sections:
                continue

            duty = duties.get(section_key)
            if not duty or not _pending_question_is_due(
                question=question,
                duty=duty,
                now=now,
                duty_cutoff_hour=duty_cutoff_hour,
                app_timezone=app_timezone,
            ):
                continue

            try:
                await bot.send_message(
                    chat_id=int(duty["user_id"]),
                    text=_stored_duty_question_text(question),
                )
            except TelegramAPIError:
                LOGGER.exception(
                    "Не удалось отправить отложенный вопрос %s дежурному букеру.",
                    question["id"],
                )
                continue

            database.mark_question_forwarded(
                question_id=int(question["id"]),
                routed_to_booker_id=int(duty["user_id"]),
                routed_at=_datetime_to_storage(_now(app_timezone)),
            )
            forwarded_count += 1

        if forwarded_count:
            LOGGER.info("Отправлено отложенных вопросов: %s", forwarded_count)

        return forwarded_count


def _pending_question_is_due(
    *,
    question: dict,
    duty: dict,
    now: datetime,
    duty_cutoff_hour: int,
    app_timezone: str,
) -> bool:
    created_at = _parse_storage_datetime(str(question["created_at"]), app_timezone)
    duty_updated_at = _parse_storage_datetime(str(duty["updated_at"]), app_timezone)
    next_day = created_at.date() + timedelta(days=1)

    duty_was_set_next_day = (
        duty_updated_at > created_at
        and duty_updated_at.date() >= next_day
    )
    if duty_was_set_next_day:
        return True

    next_day_cutoff = datetime.combine(
        next_day,
        time(hour=duty_cutoff_hour),
        tzinfo=ZoneInfo(app_timezone),
    )
    return now >= next_day_cutoff


def _is_after_cutoff(current_time: datetime, duty_cutoff_hour: int) -> bool:
    return current_time.hour >= duty_cutoff_hour


def _now(app_timezone: str) -> datetime:
    return datetime.now(ZoneInfo(app_timezone))


def _datetime_to_storage(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse_storage_datetime(value: str, app_timezone: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    timezone = ZoneInfo(app_timezone)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


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
    try:
        await callback.answer(
            "Не удалось открыть этот пункт. Вернитесь в главное меню.",
            show_alert=True,
        )
    except TelegramAPIError:
        LOGGER.exception("Не удалось ответить на некорректный callback.")


def _parse_index(data: str | None, prefix: str) -> int | None:
    if not data or not data.startswith(prefix):
        return None
    try:
        value = int(data.removeprefix(prefix))
    except ValueError:
        return None
    return value if value >= 0 else None


async def _ensure_booker_callback(
    callback: CallbackQuery,
    authorized_booker_ids: set[int],
) -> bool:
    if _is_authorized_booker(callback.from_user, authorized_booker_ids):
        await callback.answer()
        return True

    await callback.answer(NO_BOOKER_ACCESS_MESSAGE, show_alert=True)
    return False


async def _ensure_chief_booker_callback(
    callback: CallbackQuery,
    chief_booker_ids: set[int],
) -> bool:
    if _is_chief_booker(callback.from_user, chief_booker_ids):
        await callback.answer()
        return True

    await callback.answer(NO_CHIEF_BOOKER_ACCESS_MESSAGE, show_alert=True)
    return False


async def _ensure_chief_booker_message(
    message: Message,
    chief_booker_ids: set[int],
    state: FSMContext,
) -> bool:
    if _is_chief_booker(message.from_user, chief_booker_ids):
        return True

    await state.clear()
    await message.answer(NO_CHIEF_BOOKER_ACCESS_MESSAGE)
    return False


def _is_authorized_booker(user: User | None, authorized_booker_ids: set[int]) -> bool:
    return bool(user and user.id in authorized_booker_ids)


def _is_chief_booker(user: User | None, chief_booker_ids: set[int]) -> bool:
    return bool(user and user.id in chief_booker_ids)


def _is_booker_not_answering_question(question: str) -> bool:
    normalized = question.lower().replace("ё", "е")
    return "букер" in normalized and "не отвечает" in normalized


def resolve_duty_sections(faq: FAQData) -> dict[str, str]:
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
