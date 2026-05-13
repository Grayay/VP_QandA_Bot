from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import NO_ANSWER_BUTTON_TEXT
from faq_loader import FAQItem


def main_menu_keyboard(sections: list[str], *, is_booker: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, section in enumerate(sections):
        builder.button(text=section, callback_data=f"section:{index}")
    builder.button(text="С кем связаться", callback_data="contacts")
    if is_booker:
        builder.button(text="Панель букера", callback_data="booker_panel")
    builder.adjust(1)
    return builder.as_markup()


def questions_keyboard(
    *,
    section_index: int,
    questions: list[FAQItem],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in questions:
        builder.button(text=item.question, callback_data=f"question:{item.id}")

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
    builder.button(text="Стать дежурным по кастингам", callback_data="booker:duty_castings")
    builder.button(text="Стать дежурным по доп. доходу", callback_data="booker:duty_income")
    builder.button(text="Текущие дежурные", callback_data="booker:status")
    builder.button(text="⬅️ Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()
