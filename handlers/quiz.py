
from __future__ import annotations

import random
from typing import Any, Dict, Tuple

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from ai.chatgpt import chatgpt
from config import config
from keyboards.common import (
    levels_keyboard,
    main_menu_keyboard,
    topics_keyboard,
    question_options_keyboard,
)
from storage.db import db

router = Router()

_PENDING_SESSIONS: Dict[int, Dict[str, Any]] = {}
_LEVEL_LABELS = {
    "basic": "🟢 Basic",
    "advanced": "🔵 Advanced",
}
_LEVEL_ORDER = {"basic": 0, "advanced": 1}
_MAIN_MENU_TEXT = "⬅️ Главное меню"
_BACK_TO_LEVEL_TEXT = "↩️ Выбрать уровень"
_IGNORE_TOKENS = {
    "🤖 следующий вопрос".casefold(),
    "🤖 дальше".casefold(),
    "🤖 пропустить".casefold(),
}
_MAIN_MENU_TOKENS = {_MAIN_MENU_TEXT.casefold()}

MIN_QUESTION_COUNT = 7
MAX_QUESTION_COUNT = 10


def _level_label(level: str) -> str:
    return _LEVEL_LABELS.get(level, level.title())


def _sort_levels(levels: list[str]) -> list[str]:
    return sorted(levels, key=lambda lvl: (_LEVEL_ORDER.get(lvl, 99), lvl))


def _prepare_level_pairs(levels: list[str]) -> list[tuple[str, str]]:
    return [(level, _level_label(level)) for level in _sort_levels(levels)]


def _reset_to_choose_level(state: Dict[str, Any]) -> None:
    state["stage"] = "choose_level"
    for key in (
        "level",
        "level_label",
        "level_pairs",
        "level_buttons",
        "topic_map",
        "topic_buttons",
        "topic_title",
        "questions",
        "session_id",
        "idx",
        "correct",
        "total",
        "last_question_message_id",
        "chat_id",
    ):
        state.pop(key, None)


async def _clear_last_keyboard(bot, state: Dict[str, Any]) -> None:
    message_id = state.pop("last_question_message_id", None)
    chat_id = state.get("chat_id")
    if not message_id or not chat_id:
        return
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except TelegramBadRequest:
        pass


async def _handle_mcq_answer(
    message: Message,
    state: Dict[str, Any],
    question: Dict[str, Any],
    choice_index: int,
    *,
    via_callback: bool,
) -> Tuple[bool, int]:
    options = question.get("options") or []
    bot = getattr(message, "bot", None)
    if choice_index < 0 or choice_index >= len(options):
        warning = "Такого варианта нет. Выберите вариант из списка." if via_callback else "Такого варианта нет. Попробуйте снова."
        await message.answer(warning)
        return False, 0

    if via_callback:
        try:
            await message.edit_reply_markup()
        except TelegramBadRequest:
            pass
        state.pop("last_question_message_id", None)
    elif bot:
        await _clear_last_keyboard(bot, state)

    correct_index = question.get("correct_index")
    is_correct = int(correct_index is not None and choice_index == int(correct_index))
    if is_correct:
        feedback = "Верно! Отличная работа."
    else:
        tip = "Правильный ответ пока недоступен."
        if isinstance(correct_index, int) and 0 <= correct_index < len(options):
            tip = f"Правильный ответ: {options[correct_index]}"
        feedback = f"Неверно. {tip}"
    await message.answer(feedback)

    await db.log_answer(
        state["session_id"],
        question["id"],
        None,
        choice_index,
        is_correct,
        None,
        None,
    )
    return True, is_correct


async def _advance_after_answer(message: Message, user_id: int, state: Dict[str, Any], correct_count: int) -> None:
    bot = getattr(message, "bot", None)
    if bot:
        await _clear_last_keyboard(bot, state)

    idx = state.get("idx", 0) + 1
    questions = state.get("questions", [])
    state["idx"] = idx
    state["correct"] = correct_count
    await db.update_session_progress(state["session_id"], idx, correct_count)

    if idx < len(questions):
        await send_question(message, state, questions[idx], idx + 1, len(questions))
        return

    wrong_topics = await db.answers_by_topic_stats(state["session_id"])
    if wrong_topics:
        topics_summary = ", ".join(
            f"{title} ({_level_label(level)}, {count})" for title, level, count in wrong_topics
        )
        recommendation = f"Рекомендация: повторите темы {topics_summary}."
    else:
        recommendation = "Отличный результат! Продолжайте в том же духе."

    summary_header = f"Тренировка завершена.\nТема: «{state.get('topic_title', '—')}»"
    if state.get("level_label"):
        summary_header += f" ({state['level_label']})"

    await message.answer(
        f"{summary_header}\nПравильных ответов: {correct_count} из {len(questions)}.\n{recommendation}",
        reply_markup=main_menu_keyboard(),
    )
    _PENDING_SESSIONS.pop(user_id, None)


async def send_question(
    message: Message,
    state: Dict[str, Any],
    question: Dict[str, Any],
    number: int,
    total: int,
) -> None:
    state.setdefault("chat_id", message.chat.id)

    level_hint = _level_label(question.get("difficulty", "")) if question.get("difficulty") else None
    prefix = f"[{number}/{total}] "
    if level_hint:
        prefix += f"{level_hint} · "
    header = f"{prefix}{question.get('text', '')}"

    if question["qtype"] == "mcq" and question.get("options"):
        options = question.get("options") or []
        keyboard = question_options_keyboard(question["id"], options)
        sent = await message.answer(
            f"{header}\nВыберите вариант на кнопках ниже.",
            reply_markup=keyboard,
        )
        state["last_question_message_id"] = sent.message_id
    else:
        state.pop("last_question_message_id", None)
        await message.answer(
            f"{header}\nНапишите развёрнутый ответ 3–5 предложениями.",
        )


@router.message(Command("quiz"))
@router.message(F.text.casefold() == "🚀 запустить квиз".casefold())
async def cmd_quiz(message: Message) -> None:
    if not message.from_user:
        return
    user_id = message.from_user.id
    state = _PENDING_SESSIONS.get(user_id, {"stage": "choose_level"})
    _PENDING_SESSIONS[user_id] = state
    _reset_to_choose_level(state)

    levels = await db.list_levels()
    if not levels:
        await message.answer(
            "Для квиза пока нет активных тем. Добавьте их через панель администратора.",
            reply_markup=main_menu_keyboard(),
        )
        _PENDING_SESSIONS.pop(user_id, None)
        return

    level_pairs = _prepare_level_pairs(levels)
    state["stage"] = "choose_level"
    state["level_pairs"] = level_pairs
    state["level_buttons"] = {label.casefold(): level for level, label in level_pairs}

    await message.answer(
        "Выберите уровень подготовки:",
        reply_markup=levels_keyboard(level_pairs),
    )


@router.message(F.text, ~F.text.startswith("/"))
async def flow(message: Message) -> None:
    if not message.from_user:
        return
    user_id = message.from_user.id
    text = (message.text or "").strip()
    if not text:
        return

    lowered = text.casefold()
    if lowered in _IGNORE_TOKENS:
        return

    state = _PENDING_SESSIONS.get(user_id)

    if lowered in _MAIN_MENU_TOKENS:
        _PENDING_SESSIONS.pop(user_id, None)
        await message.answer("Вы в главном меню.", reply_markup=main_menu_keyboard())
        return

    if lowered == _BACK_TO_LEVEL_TEXT.casefold():
        if state:
            _reset_to_choose_level(state)
            level_pairs = state.get("level_pairs") or _prepare_level_pairs(await db.list_levels())
            state["level_pairs"] = level_pairs
            state["level_buttons"] = {label.casefold(): level for level, label in level_pairs}
            await message.answer(
                "Хорошо, выберите нужный уровень:",
                reply_markup=levels_keyboard(level_pairs),
            )
        else:
            await cmd_quiz(message)
        return

    if not state:
        return

    stage = state.get("stage")

    if stage == "choose_level":
        level_buttons = state.get("level_buttons", {})
        level = level_buttons.get(lowered)
        if not level:
            await message.answer(
                "Пожалуйста, воспользуйтесь кнопками, чтобы выбрать уровень.",
                reply_markup=levels_keyboard(state.get("level_pairs", [])),
            )
            return

        topics = [topic for topic in await db.list_topics(level) if topic.get("question_count", 0) > 0]
        if not topics:
            await message.answer(
                "Для этого уровня пока нет готовых тем. Попробуйте выбрать другой уровень.",
                reply_markup=levels_keyboard(state.get("level_pairs", [])),
            )
            return

        topic_buttons: list[Dict[str, Any]] = []
        topic_map: Dict[str, Dict[str, Any]] = {}
        for topic in topics:
            question_count = int(topic.get("question_count", 0))
            label = topic["title"]
            button = {**topic, "label": label}
            topic_buttons.append(button)
            topic_map[label.casefold()] = topic
            topic_map[topic["title"].casefold()] = topic

        state.update(
            {
                "stage": "choose_topic",
                "level": level,
                "level_label": _level_label(level),
                "topic_map": topic_map,
                "topic_buttons": topic_buttons,
            }
        )

        await message.answer(
            f"Отлично! Уровень {state['level_label']}. Выберите тему для тренировки:",
            reply_markup=topics_keyboard(topic_buttons, add_level_back=True),
        )
        return

    if stage == "choose_topic":
        topic_map = state.get("topic_map", {})
        topic = topic_map.get(lowered)
        if not topic:
            await message.answer(
                "Используйте кнопки, чтобы выбрать тему.",
                reply_markup=topics_keyboard(state.get("topic_buttons", []), add_level_back=True),
            )
            return

        total_available = int(topic.get("question_count") or await db.count_questions(topic["id"]))
        if total_available <= 0:
            await message.answer(
                "Для выбранной темы пока нет вопросов. Попробуйте другую тему.",
                reply_markup=topics_keyboard(state.get("topic_buttons", []), add_level_back=True),
            )
            return

        upper_bound = min(MAX_QUESTION_COUNT, total_available)
        target_total = random.randint(MIN_QUESTION_COUNT, upper_bound) if total_available >= MIN_QUESTION_COUNT else total_available
        questions = await db.pick_questions(topic["id"], target_total, randomize=True)
        if not questions:
            await message.answer(
                "Для выбранной темы пока нет вопросов. Попробуйте другую тему.",
                reply_markup=topics_keyboard(state.get("topic_buttons", []), add_level_back=True),
            )
            return

        session_id = await db.start_session(user_id, topic["id"], len(questions), "mixed")

        state.update(
            {
                "stage": "answering",
                "topic_id": topic["id"],
                "topic_title": topic["title"],
                "questions": questions,
                "session_id": session_id,
                "idx": 0,
                "correct": 0,
                "total": len(questions),
                "chat_id": message.chat.id,
                "last_question_message_id": None,
            }
        )

        await message.answer(
            f"Стартуем! Тема «{topic['title']}» ({state['level_label']}). В этой сессии {len(questions)} вопросов."
            f" Сессии формируются автоматически и включают от {MIN_QUESTION_COUNT} до {MAX_QUESTION_COUNT} вопросов, если тема позволяет."
            " Отвечайте развёрнуто или выбирайте вариант на кнопках — бот оценит ответы и подскажет, что улучшить.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await send_question(message, state, questions[0], 1, len(questions))
        return

    if stage != "answering":
        return

    questions = state.get("questions", [])
    idx = state.get("idx", 0)
    if idx >= len(questions):
        return

    question = questions[idx]
    correct_count = state.get("correct", 0)

    if question["qtype"] == "mcq":
        try:
            choice_index = int(text) - 1
        except ValueError:
            await message.answer("Выберите вариант с помощью кнопок или введите номер варианта.")
            return
        processed, gained = await _handle_mcq_answer(message, state, question, choice_index, via_callback=False)
        if not processed:
            return
        await _advance_after_answer(message, user_id, state, correct_count + gained)
        return

    elif question["qtype"] == "open":
        user_answer = text
        gold = question.get("ideal_answer") or ""
        try:
            result = await chatgpt.score_open_answer(
                topic=state.get("topic_title", "(mixed)"),
                question=question.get("text", ""),
                gold=gold,
                user_answer=user_answer,
            )
            score = int(result.get("score", 0))
            comment = result.get("comment", "Без комментария.")
            if score >= 4:
                correct_count += 1
            await message.answer(f"Оценка: {score}/5\nКомментарий: {comment}")
            await db.log_answer(
                state["session_id"],
                question["id"],
                user_answer,
                None,
                None,
                score,
                comment,
            )
        except Exception:
            await message.answer("Не получилось получить оценку от ChatGPT. Ответ сохранён, продолжаем.")
            await db.log_answer(
                state["session_id"],
                question["id"],
                user_answer,
                None,
                None,
                None,
                None,
            )

        await _advance_after_answer(message, user_id, state, correct_count)
        return

    await message.answer("Неизвестный тип вопроса. Пропускаем.")
    await _advance_after_answer(message, user_id, state, correct_count)


@router.callback_query(F.data.startswith("quiz:answer:"))
async def handle_answer_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        return
    user_id = callback.from_user.id
    state = _PENDING_SESSIONS.get(user_id)
    if not state or state.get("stage") != "answering":
        await callback.answer("Сессия не активна", show_alert=True)
        return

    parts = callback.data.split(":") # type: ignore
    if len(parts) != 4:
        await callback.answer()
        return

    try:
        question_id = int(parts[2])
        choice_index = int(parts[3])
    except ValueError:
        await callback.answer()
        return

    questions = state.get("questions", [])
    idx = state.get("idx", 0)
    if idx >= len(questions):
        await callback.answer("Вопрос уже завершён", show_alert=True)
        return

    question = questions[idx]
    if question.get("id") != question_id:
        await callback.answer("Этот вопрос уже закрыт", show_alert=True)
        return

    processed, gained = await _handle_mcq_answer(callback.message, state, question, choice_index, via_callback=True) # type: ignore
    if not processed:
        await callback.answer("Некорректный вариант", show_alert=True)
        return

    await callback.answer()
    correct_count = state.get("correct", 0) + gained
    await _advance_after_answer(callback.message, user_id, state, correct_count) # type: ignore
