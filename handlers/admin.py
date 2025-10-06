from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable

import aiosqlite
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config import config
from storage.db import db

router = Router()


@dataclass
class QuestionPayload:
    topic_title: str
    qtype: str
    text: str
    options: list[str] | None
    correct_index: int | None
    ideal_answer: str | None


def _is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id in config.admins)


async def _ensure_admin(message: Message) -> bool:
    if _is_admin(message):
        logging.debug("Admin command from %s: %s", message.from_user.id, message.text) # type: ignore
        return True
    logging.debug("Ignored admin command from non-admin %s", getattr(message.from_user, "id", None))
    return False


async def _fetch_single(conn: aiosqlite.Connection, query: str, *params: Any) -> Any:
    async with conn.execute(query, params) as cur:
        row = await cur.fetchone()
    return row[0] if row else None


async def _topic_exists(conn: aiosqlite.Connection, title: str) -> bool:
    result = await _fetch_single(conn, "SELECT 1 FROM topics WHERE title=?", title)
    return result is not None


async def _resolve_topic_id(conn: aiosqlite.Connection, title: str) -> int | None:
    return await _fetch_single(conn, "SELECT id FROM topics WHERE title=?", title)


def _parse_options(raw: str) -> list[str] | None:
    if not raw or raw.strip() == "-":
        return None
    raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        candidates = [part.strip() for part in raw.split(";") if part.strip()]
        if candidates:
            return candidates
        raise ValueError("Неверный формат вариантов ответа. Используйте JSON или список через ';'.")
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise ValueError("JSON с вариантами должен быть списком строк.")
    if not data:
        raise ValueError("Список вариантов не может быть пустым.")
    return [item.strip() for item in data]


def _parse_question_payload(message: Message) -> QuestionPayload:
    payload = (message.text or "").replace("/add_q", "", 1).strip()
    parts = [part.strip() for part in payload.split("|")]
    if len(parts) < 6:
        raise ValueError(
            "Неверный формат. Используйте: /add_q <тема> | <mcq|open> | <текст> | <варианты или -> | <номер или -> | <идеал или ->"
        )
    topic_title, qtype, text_q, options_raw, correct_raw, ideal_raw = parts[:6]
    if not topic_title:
        raise ValueError("Название темы обязательно.")
    qtype = qtype.lower()
    if qtype not in {"mcq", "open"}:
        raise ValueError("Тип вопроса должен быть mcq или open.")
    text_q = text_q.strip()
    if not text_q:
        raise ValueError("Текст вопроса не может быть пустым.")

    options: list[str] | None = None
    if qtype == "mcq":
        options = _parse_options(options_raw)
    else:
        if options_raw and options_raw != "-":
            raise ValueError("Для open-вопросов не передавайте варианты.")

    correct_index: int | None = None
    if correct_raw and correct_raw != "-":
        try:
            idx = int(correct_raw)
        except ValueError as exc:
            raise ValueError("Номер правильного ответа должен быть числом.") from exc
        if options:
            if 1 <= idx <= len(options):
                correct_index = idx - 1
            elif 0 <= idx < len(options):
                correct_index = idx
            else:
                raise ValueError("Номер правильного варианта выходит за пределы списка.")
        else:
            correct_index = idx
    elif qtype == "mcq":
        raise ValueError("Для mcq необходимо указать номер правильного ответа.")

    ideal_answer = None
    if ideal_raw and ideal_raw != "-":
        ideal_answer = ideal_raw

    return QuestionPayload(
        topic_title=topic_title,
        qtype=qtype,
        text=text_q,
        options=options,
        correct_index=correct_index,
        ideal_answer=ideal_answer,
    )


def _format_top_topics(rows: Iterable[tuple[str, int]]) -> list[str]:
    lines: list[str] = []
    for idx, (title, count) in enumerate(rows, start=1):
        lines.append(f"{idx}. {title} — {count}")
    return lines


@router.message(Command("add_topic"))
async def add_topic(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    text = (message.text or "").split(maxsplit=1)
    if len(text) < 2 or not text[1].strip():
        await message.answer("Формат: /add_topic <название темы>")
        return
    title = text[1].strip()

    async with aiosqlite.connect(db.path) as conn:
        exists = await _topic_exists(conn, title)
        if exists:
            await message.answer(f"Тема «{title}» уже есть в базе.")
            return
        await conn.execute("INSERT INTO topics(title) VALUES(?)", (title,))
        await conn.commit()
    await message.answer(f"Тема «{title}» успешно добавлена и активирована.")


@router.message(Command("add_q"))
async def add_q(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    try:
        payload = _parse_question_payload(message)
    except ValueError as exc:
        await message.answer(str(exc))
        return

    async with aiosqlite.connect(db.path) as conn:
        topic_id = await _resolve_topic_id(conn, payload.topic_title)
        if topic_id is None:
            await message.answer(
                "Тема не найдена. Добавьте её командой /add_topic или убедитесь в правильности названия."
            )
            return
        await conn.execute(
            "INSERT INTO questions(topic_id,qtype,text,options,correct_index,ideal_answer) VALUES(?,?,?,?,?,?)",
            (
                topic_id,
                payload.qtype,
                payload.text,
                json.dumps(payload.options, ensure_ascii=False) if payload.options else None,
                payload.correct_index,
                payload.ideal_answer,
            ),
        )
        await conn.commit()

    summary = [
        "Вопрос добавлен:",
        f"• Тема: {payload.topic_title}",
        f"• Тип: {payload.qtype}",
        f"• Текст: {payload.text}",
    ]
    if payload.options:
        opts = "\n".join(f"   {i + 1}. {opt}" for i, opt in enumerate(payload.options))
        summary.append("• Варианты:\n" + opts)
        if payload.correct_index is not None:
            summary.append(f"• Правильный вариант: {payload.correct_index + 1}")
    if payload.ideal_answer:
        summary.append(f"• Эталонный ответ: {payload.ideal_answer}")

    await message.answer("\n".join(summary))


@router.message(Command("stats"))
async def stats(message: Message) -> None:
    if not await _ensure_admin(message):
        return

    async with aiosqlite.connect(db.path) as conn:
        users_total = await _fetch_single(conn, "SELECT COUNT(*) FROM users") or 0
        users_subscribed = await _fetch_single(conn, "SELECT COUNT(*) FROM users WHERE subscribed=1") or 0
        topics_total = await _fetch_single(conn, "SELECT COUNT(*) FROM topics") or 0
        topics_active = await _fetch_single(conn, "SELECT COUNT(*) FROM topics WHERE active=1") or 0
        questions_total = await _fetch_single(conn, "SELECT COUNT(*) FROM questions") or 0
        sessions_total = await _fetch_single(conn, "SELECT COUNT(*) FROM sessions") or 0
        sessions_finished = await _fetch_single(conn, "SELECT COUNT(*) FROM sessions WHERE finished_at IS NOT NULL") or 0
        answers_total = await _fetch_single(conn, "SELECT COUNT(*) FROM answers") or 0
        answers_correct = await _fetch_single(
            conn,
            "SELECT COUNT(*) FROM answers WHERE is_correct=1 OR (is_correct IS NULL AND ai_score >= 4)",
        ) or 0
        last_session = await _fetch_single(conn, "SELECT MAX(started_at) FROM sessions")
        last_answer = await _fetch_single(conn, "SELECT MAX(answered_at) FROM answers")

        top_topics: list[tuple[str, int]] = []
        async with conn.execute(
            """
            SELECT t.title, COUNT(*) AS cnt
            FROM questions q
            JOIN topics t ON t.id = q.topic_id
            GROUP BY t.id
            ORDER BY cnt DESC
            LIMIT 5
            """
        ) as cur:
            async for title, cnt in cur:
                top_topics.append((title, cnt))

    accuracy = (answers_correct / answers_total * 100) if answers_total else 0.0

    lines = [
        "Статистика бота:",
        f"• Пользователи: {users_total} (подписаны: {users_subscribed})",
        f"• Темы: {topics_total} (активные: {topics_active})",
        f"• Вопросы: {questions_total}",
        f"• Сессии: {sessions_total} (завершены: {sessions_finished})",
        f"• Ответы: {answers_total} (зачтено: {answers_correct}, точность: {accuracy:.1f}%)",
    ]
    if last_session:
        lines.append(f"• Последняя сессия: {last_session}")
    if last_answer:
        lines.append(f"• Последний ответ: {last_answer}")

    if top_topics:
        lines.append("\nТоп тем по количеству вопросов:")
        lines.extend(_format_top_topics(top_topics))

    await message.answer("\n".join(lines))
