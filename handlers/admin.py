from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable
from io import BytesIO

import aiosqlite
from aiogram import F, Router
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
    topic_level: str | None = None



_VALID_TOPIC_LEVELS: set[str] = {"basic", "advanced"}



def _coerce_optional_level(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value:
        return None
    if value in _VALID_TOPIC_LEVELS:
        return value
    raise ValueError(f"Некорректный уровень темы: {raw}")


def _normalize_level(raw: str | None, *, default: str = "basic") -> str:
    level = _coerce_optional_level(raw)
    if level is None:
        return default
    return level


def _parse_topic_command_payload(raw: str) -> tuple[str, str]:
    payload = raw.strip()
    if not payload:
        raise ValueError("Формат: /add_topic <basic|advanced> | <название темы>.")
    if "|" in payload:
        level_raw, title = [part.strip() for part in payload.split("|", 1)]
        if not title:
            raise ValueError("Название темы обязательно.")
        level = _normalize_level(level_raw)
        return title, level
    title, level_hint = _split_topic_and_level(payload)
    return title.strip(), level_hint or "basic"


def _split_topic_and_level(raw: str) -> tuple[str, str | None]:
    candidate = raw.strip()
    for sep in ("|", "@"):
        if sep in candidate:
            base, level_raw = [part.strip() for part in candidate.rsplit(sep, 1)]
            if not base:
                continue
            level = _coerce_optional_level(level_raw)
            if level is not None:
                return base, level
    if candidate.endswith(")") and "(" in candidate:
        base, level_raw = candidate.rsplit("(", 1)
        level_raw = level_raw.rstrip(")")
        level = _coerce_optional_level(level_raw)
        if level is not None and base.strip():
            return base.strip(), level
    return candidate, None

def _command_arguments(source: str | None, command: str) -> str:
    if not source:
        return ""
    cleaned = source.strip()
    if cleaned.startswith(command):
        cleaned = cleaned[len(command):]
    return cleaned.strip()


def _parse_import_mode(raw: str | None) -> bool:
    tokens = (raw or "").lower().split()
    if not tokens:
        return True
    head = tokens[0]
    if head in {"append", "merge", "extend"}:
        return False
    if head in {"replace", "reset"}:
        return True
    return True


def _load_topics_payload(raw: str) -> tuple[list[dict[str, Any]], bool | None]:
    data = json.loads(raw)
    replace: bool | None = None
    if isinstance(data, dict):
        topics = data.get("topics")
        if not isinstance(topics, list):
            raise ValueError("В корне JSON ожидается ключ 'topics' со списком тем.")
        if "replace" in data:
            replace = bool(data.get("replace"))
    elif isinstance(data, list):
        topics = data
    else:
        raise ValueError("JSON должен описывать список тем или объект с ключом 'topics'.")
    return topics, replace


async def _handle_import_file(message: Message, *, replace_default: bool) -> None:
    document = message.document
    if not document:
        await message.answer(
            "Прикрепите JSON-файл с вопросами и отправьте команду /import_q в подписи к файлу.\n"
            "Формат файла: {\"topics\": [...]} или просто список тем."
        )
        return
    if document.file_name and not document.file_name.lower().endswith(".json"):
        await message.answer("Ожидается JSON-файл с расширением .json.")
        return
    buffer = BytesIO()
    await message.bot.download(document, destination=buffer)  # type: ignore[arg-type]
    try:
        raw = buffer.getvalue().decode("utf-8-sig")
    except UnicodeDecodeError:
        await message.answer("Не получилось прочитать файл: используйте кодировку UTF-8.")
        return

    try:
        topics, replace_in_file = _load_topics_payload(raw)
    except ValueError as exc:
        await message.answer(f"Не удалось разобрать файл: {exc}")
        return
    except json.JSONDecodeError as exc:
        await message.answer(f"Ошибка JSON: {exc}")
        return

    replace_flag = replace_in_file if replace_in_file is not None else replace_default

    try:
        stats = await db.import_topics_from_payload(topics, replace_default=replace_flag)
    except ValueError as exc:
        await message.answer(str(exc))
        return

    mode_text = "перезапись существующих вопросов" if replace_flag else "добавление без удаления"
    summary = [
        "Импорт завершён.",
        f"• Тем добавлено: {stats['topics_created']}",
        f"• Тем обновлено: {stats['topics_updated']}",
        f"• Вопросов сохранено: {stats['questions_added']}",
    ]
    if stats['questions_skipped']:
        summary.append(f"• Пропущено из-за совпадений: {stats['questions_skipped']}")
    summary.append(f"Режим: {mode_text}.")

    await message.answer("\n".join(summary))


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


async def _topic_exists(conn: aiosqlite.Connection, title: str, level: str | None = None) -> bool:
    if level:
        result = await _fetch_single(conn, "SELECT 1 FROM topics WHERE title=? AND level=?", title, level)
    else:
        result = await _fetch_single(conn, "SELECT 1 FROM topics WHERE title=?", title)
    return result is not None


async def _resolve_topic_id(conn: aiosqlite.Connection, title: str, level: str | None = None) -> int | None:
    if level:
        return await _fetch_single(conn, "SELECT id FROM topics WHERE title=? AND level=?", title, level)
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
    topic_title, topic_level = _split_topic_and_level(topic_title)
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
        topic_level=topic_level,
    )


def _format_top_topics(rows: Iterable[tuple[str, str, int]]) -> list[str]:
    lines: list[str] = []
    for idx, (title, level, count) in enumerate(rows, start=1):
        lines.append(f"{idx}. {title} ({level}) — {count}")
    return lines


@router.message(Command("import_q"))
async def import_q(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    args = _command_arguments(message.text, "/import_q")
    replace_default = _parse_import_mode(args)
    if not message.document:
        await message.answer(
            "Прикрепите JSON-файл с вопросами и отправьте команду /import_q в подписи к файлу.\n"
            "Используйте '/import_q append', чтобы добавить вопросы без удаления существующих."
        )
        return
    await _handle_import_file(message, replace_default=replace_default)


@router.message(F.document, F.caption, F.caption.startswith("/import_q"))
async def import_q_document(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    args = _command_arguments(message.caption, "/import_q")
    replace_default = _parse_import_mode(args)
    await _handle_import_file(message, replace_default=replace_default)


@router.message(Command("add_topic"))
async def add_topic(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Формат: /add_topic <basic|advanced> | <название темы>")
        return
    try:
        title, level = _parse_topic_command_payload(parts[1])
    except ValueError as exc:
        await message.answer(str(exc))
        return

    async with aiosqlite.connect(db.path) as conn:
        exists = await _topic_exists(conn, title, level)
        if exists:
            await message.answer(f"Тема «{title}» с уровнем {level} уже есть в базе.")
            return
        await conn.execute("INSERT INTO topics(title, level, active) VALUES(?,?,1)", (title, level))
        await conn.commit()
    await message.answer(f"Тема «{title}» ({level}) успешно добавлена и активирована.")


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
        topic_id = await _resolve_topic_id(conn, payload.topic_title, payload.topic_level)
        level_note = f" (уровень {payload.topic_level})" if payload.topic_level else ""
        if topic_id is None:
            await message.answer(
                f"Тема «{payload.topic_title}»{level_note} не найдена. Добавьте её командой /add_topic."
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
        f"• Уровень: {payload.topic_level or 'basic'}",
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

        top_topics: list[tuple[str, str, int]] = []
        async with conn.execute(
            """
            SELECT t.title, t.level, COUNT(*) AS cnt
            FROM questions q
            JOIN topics t ON t.id = q.topic_id
            GROUP BY t.id
            ORDER BY cnt DESC
            LIMIT 5
            """
        ) as cur:
            async for title, level, cnt in cur:
                top_topics.append((title, level, cnt))

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
