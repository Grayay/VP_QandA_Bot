from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import NO_ANSWER_BUTTON_TEXT
from faq_loader import FAQItem


def main_menu_keyboard(
    sections: list[str],
    *,
    section_indexes: list[int] | None = None,
    root_questions: list[FAQItem] | None = None,
    is_booker: bool = False,
    is_chief_booker: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    indexes = section_indexes or list(range(len(sections)))
    for index, section in zip(indexes, sections):
        builder.button(text=section, callback_data=f"section:{index}")
    for item in root_questions or []:
        builder.button(text=item.question, callback_data=f"question:{item.token}")
    if is_booker:
        builder.button(text="Панель букера", callback_data="booker_panel")
    if is_chief_booker:
        builder.button(text="⚙️ Управление вопросами", callback_data="faqm:menu")
        builder.button(text="Управление букерами", callback_data="bookers:menu")
    builder.adjust(1)
    return builder.as_markup()


def questions_keyboard(
    *,
    section_index: int,
    questions: list[FAQItem],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in questions:
        builder.button(text=item.question, callback_data=f"question:{item.token}")

    builder.button(
        text=NO_ANSWER_BUTTON_TEXT,
        callback_data=f"ask:{section_index}",
    )

    builder.button(text="⬅️ Назад к разделам", callback_data="main_menu")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def answer_navigation_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад к разделам", callback_data="main_menu")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def home_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def booker_panel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Стать дежурным букером", callback_data="booker:duty_general")
    builder.button(text="Текущие дежурные", callback_data="booker:status")
    builder.button(text="⬅️ Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def booker_reply_keyboard(question_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Ответить модели", callback_data=f"booker:reply:{question_id}")
    builder.adjust(1)
    return builder.as_markup()


def booker_management_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Список букеров", callback_data="bookers:list")
    builder.button(text="Добавить букера", callback_data="bookers:add")
    builder.button(text="Удалить букера", callback_data="bookers:delete")
    builder.button(text="Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def booker_add_step_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Отмена", callback_data="bookers:cancel")
    builder.adjust(1)
    return builder.as_markup()


def booker_delete_keyboard(bookers: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for booker in bookers:
        builder.button(
            text=str(booker["display_name"]),
            callback_data=f"bookers:delete_select:{booker['telegram_id']}",
        )
    builder.button(text="Назад", callback_data="bookers:menu")
    builder.adjust(1)
    return builder.as_markup()


def booker_delete_confirm_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Да, удалить", callback_data=f"bookers:delete_confirm:{telegram_id}")
    builder.button(text="Отмена", callback_data="bookers:delete")
    builder.adjust(1)
    return builder.as_markup()


def faq_management_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить вопрос", callback_data="faqm:add")
    builder.button(text="✏️ Редактировать вопрос", callback_data="faqm:edit")
    builder.button(text="🗑 Удалить вопрос", callback_data="faqm:delete")
    builder.button(text="⬅️ Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def faq_management_sections_keyboard(
    *,
    action: str,
    sections: list[str],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, section in enumerate(sections):
        builder.button(text=section, callback_data=f"faqm:{action}_section:{index}")
    builder.button(text="⬅️ Назад", callback_data="faqm:menu")
    builder.adjust(1)
    return builder.as_markup()


def faq_management_questions_keyboard(
    *,
    action: str,
    section_index: int,
    questions: list[FAQItem],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in questions:
        builder.button(text=item.question, callback_data=f"faqm:{action}_item:{item.token}")
    builder.button(text="⬅️ Назад к разделам", callback_data=f"faqm:{action}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def faq_add_step_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад", callback_data="faqm:add_back")
    builder.button(text="❌ Отмена", callback_data="faqm:cancel")
    builder.adjust(1)
    return builder.as_markup()


def faq_add_confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Сохранить", callback_data="faqm:add_save")
    builder.button(text="⬅️ Назад", callback_data="faqm:add_back")
    builder.button(text="❌ Отмена", callback_data="faqm:cancel")
    builder.adjust(1)
    return builder.as_markup()


def faq_edit_entry_keyboard(token: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Изменить раздел", callback_data=f"faqm:edit_field:{token}:section")
    builder.button(text="Изменить отдел", callback_data=f"faqm:edit_field:{token}:department")
    builder.button(text="Изменить вопрос", callback_data=f"faqm:edit_field:{token}:question")
    builder.button(text="Изменить ответ", callback_data=f"faqm:edit_field:{token}:answer")
    builder.button(text="⬅️ Назад", callback_data="faqm:edit")
    builder.adjust(1)
    return builder.as_markup()


def faq_edit_step_keyboard(token: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад", callback_data=f"faqm:edit_item:{token}")
    builder.button(text="❌ Отмена", callback_data="faqm:cancel")
    builder.adjust(1)
    return builder.as_markup()


def faq_delete_confirm_keyboard(token: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Удалить", callback_data=f"faqm:delete_confirm:{token}")
    builder.button(text="❌ Отмена", callback_data="faqm:delete")
    builder.adjust(1)
    return builder.as_markup()
