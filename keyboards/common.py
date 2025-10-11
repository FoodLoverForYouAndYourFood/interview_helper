from __future__ import annotations

from typing import Any, Iterable, Mapping

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

_MAIN_MENU_LABEL = "⬅️ Главное меню"
_BACK_TO_LEVEL_LABEL = "↩️ Выбрать уровень"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚀 Запустить квиз")],
            [KeyboardButton(text="📋 Список команд"), KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def levels_keyboard(levels: Iterable[tuple[str, str]]) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=label)] for _, label in levels]
    rows.append([KeyboardButton(text=_MAIN_MENU_LABEL)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Выберите уровень подготовки",
    )


def topics_keyboard(
    topics: Iterable[Mapping[str, Any]],
    *,
    add_level_back: bool = False,
) -> ReplyKeyboardMarkup:
    rows = []
    for topic in topics:
        label = str(topic.get("label") or topic.get("title"))
        rows.append([KeyboardButton(text=label)])
    if add_level_back:
        rows.append([KeyboardButton(text=_BACK_TO_LEVEL_LABEL)])
    rows.append([KeyboardButton(text=_MAIN_MENU_LABEL)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Выберите тему",
    )


def subscription_keyboard(target_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Перейти в канал", url=target_url)]],
    )


def question_options_keyboard(question_id: int, options: Iterable[str]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{idx + 1}. {option}",
                callback_data=f"quiz:answer:{question_id}:{idx}",
            )
        ]
        for idx, option in enumerate(options)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
