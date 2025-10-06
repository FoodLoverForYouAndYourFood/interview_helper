from __future__ import annotations

from typing import Any, Dict

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.exceptions import TelegramBadRequest

from aiogram.filters import Command
from aiogram.types import Message

from ai.chatgpt import chatgpt
from config import config
from keyboards.common import main_menu_keyboard, topics_keyboard
from storage.db import db

router = Router()

_PENDING_SESSIONS: Dict[int, Dict[str, Any]] = {}
_IGNORE_TOKENS = {
    "?? список команд".casefold(),
    "?? помощь".casefold(),
    "?? запустить квиз".casefold(),
}
_MAIN_MENU_TOKENS = {"?? главное меню".casefold()}


@router.message(Command("quiz"))
@router.message(F.text.casefold() == "?? запустить квиз".casefold())
async def cmd_quiz(message: Message) -> None:
    if not message.from_user:
        return
    user_id = message.from_user.id
    _PENDING_SESSIONS.pop(user_id, None)
    topics = await db.list_topics()
    if not topics:
        await message.answer(
            "Для квиза пока нет активных тем. Добавьте их через панель администратора.",
            reply_markup=main_menu_keyboard(),
        )
        return
    await message.answer(
        "Выберите тему для тренировки:",
        reply_markup=topics_keyboard(topics),
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

    if lowered in _MAIN_MENU_TOKENS:
        _PENDING_SESSIONS.pop(user_id, None)
        await message.answer("Вы в главном меню.", reply_markup=main_menu_keyboard())
        return

    if user_id not in _PENDING_SESSIONS:
        topics = await db.list_topics()
        title_to_topic = {t["title"].casefold(): t for t in topics}
        if lowered not in title_to_topic:
            return

        topic = title_to_topic[lowered]
        topic_id = topic["id"]
        session_id = await db.start_session(user_id, topic_id, config.default_session_n, "mixed")
        questions = await db.pick_questions(topic_id, config.default_session_n)
        if not questions:
            await message.answer(
                "Для выбранной темы пока нет вопросов. Попробуйте другую тему.",
                reply_markup=topics_keyboard(topics),
            )
            return

        _PENDING_SESSIONS[user_id] = {
            "stage": "answering",
            "topic_id": topic_id,
            "questions": questions,
            "session_id": session_id,
            "idx": 0,
            "correct": 0,
        }
        await message.answer(
            "Отлично! Начинаем тренировку. Отвечайте на вопросы и получайте разбор.",
        )
        await send_question(message, questions[0], 1, len(questions))
        return

    state = _PENDING_SESSIONS[user_id]
    if state.get("stage") != "answering":
        return

    idx = state["idx"]
    questions = state["questions"]
    if idx >= len(questions):
        return

    question = questions[idx]
    correct_count = state["correct"]

    if question["qtype"] == "mcq":
        options = question.get("options") or []
        try:
            choice_index = int(text) - 1
        except ValueError:
            await message.answer("Введите номер варианта ответа (цифра).")
            return
        if choice_index < 0 or choice_index >= len(options):
            await message.answer("Такого варианта нет. Попробуйте снова.")
            return

        correct_index = question.get("correct_index")
        is_correct = int(correct_index is not None and choice_index == int(correct_index))
        if is_correct:
            correct_count += 1
            await message.answer("Верно! Отличная работа.")
        else:
            tip = "Правильный ответ пока недоступен."
            if isinstance(correct_index, int) and 0 <= correct_index < len(options):
                tip = f"Правильный ответ: {options[correct_index]}"
            await message.answer(f"Неверно. {tip}")

        await db.log_answer(
            state["session_id"],
            question["id"],
            None,
            choice_index,
            is_correct,
            None,
            None,
        )

    elif question["qtype"] == "open":
        user_answer = text
        gold = question.get("ideal_answer") or ""
        try:
            result = await chatgpt.score_open_answer(
                topic="(mixed)",
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
            await message.answer(
                "Не получилось получить оценку от ChatGPT. Ответ сохранён, продолжаем.",
            )
            await db.log_answer(
                state["session_id"],
                question["id"],
                user_answer,
                None,
                None,
                None,
                None,
            )

    else:
        await message.answer("Неизвестный тип вопроса. Пропускаем.")

    idx += 1
    state["idx"] = idx
    state["correct"] = correct_count
    await db.update_session_progress(state["session_id"], idx, correct_count)

    if idx < len(questions):
        await send_question(message, questions[idx], idx + 1, len(questions))
        return

    wrong_topics = await db.answers_by_topic_stats(state["session_id"])
    if wrong_topics:
        topics_summary = ", ".join(f"{title} ({count})" for title, count in wrong_topics)
        recommendation = f"Рекомендация: повторите темы {topics_summary}."
    else:
        recommendation = "Отличный результат! Продолжайте в том же духе."

    await message.answer(
        f"Тренировка завершена.\nПравильных ответов: {correct_count} из {len(questions)}.\n{recommendation}",
        reply_markup=main_menu_keyboard(),
    )
    _PENDING_SESSIONS.pop(user_id, None)


async def send_question(message: Message, question: Dict[str, Any], number: int, total: int) -> None:
    header = f"[{number}/{total}] {question.get('text', '')}"
    if question.get("qtype") == "mcq" and question.get("options"):
        options_text = "\n".join(
            f"{idx + 1}. {option}" for idx, option in enumerate(question["options"])
        )
        await message.answer(f"{header}\n\n{options_text}")
    else:
        await message.answer(f"{header}\nНапишите развёрнутый ответ текстом.")


