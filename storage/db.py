
from __future__ import annotations

import json
import os
from typing import Any, Iterable, Sequence

import aiosqlite

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "bot_data.db")
SEED_TOPICS_PATH = os.path.join(os.path.dirname(__file__), "seed_topics.json")

PLACEHOLDER_TOPIC_TITLES: set[str] = {
    "Python Basics",
    "SQL Basics",
    "TestTopic",
    "Никита привет",
    "Никита привет!",
    "- ПриветНикита",
    "- Привет Никита",
    "тест",
    "test",
    "Test",
    "test topic",
    "Test topic",
}

_ALLOWED_LEVELS = {"basic", "advanced"}
_ALLOWED_QTYPES = {"mcq", "open"}
_ALLOWED_DIFFICULTIES = {"basic", "advanced"}


def _normalize_text(value: str) -> str:
    text = (value or "").replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())


def _load_seed_topics() -> list[dict[str, Any]]:
    if not os.path.exists(SEED_TOPICS_PATH):
        return []
    try:
        with open(SEED_TOPICS_PATH, "r", encoding="utf-8") as fp:
            data = json.load(fp)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


class DB:
    def __init__(self, path: str = DB_PATH):
        self.path = os.path.abspath(path)

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA foreign_keys=ON;")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                  telegram_id INTEGER PRIMARY KEY,
                  subscribed  INTEGER DEFAULT 0,
                  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS topics (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  title TEXT NOT NULL,
                  level TEXT NOT NULL DEFAULT 'basic',
                  active INTEGER DEFAULT 1,
                  UNIQUE(title, level)
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS questions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  topic_id INTEGER NOT NULL,
                  qtype TEXT NOT NULL CHECK (qtype IN ('mcq','open')),
                  text TEXT NOT NULL,
                  options TEXT,
                  correct_index INTEGER,
                  ideal_answer TEXT,
                  difficulty TEXT DEFAULT 'basic' CHECK (difficulty IN ('basic','advanced')),
                  image_path TEXT,
                  FOREIGN KEY(topic_id) REFERENCES topics(id)
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  telegram_id INTEGER NOT NULL,
                  topic_id INTEGER NOT NULL,
                  mode TEXT NOT NULL CHECK (mode IN ('test','open','mixed')),
                  total_questions INTEGER NOT NULL,
                  idx INTEGER DEFAULT 0,
                  correct_count INTEGER DEFAULT 0,
                  started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                  finished_at DATETIME
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS answers (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  session_id INTEGER NOT NULL,
                  question_id INTEGER NOT NULL,
                  user_text TEXT,
                  chosen_index INTEGER,
                  is_correct INTEGER,
                  ai_score INTEGER,
                  ai_comment TEXT,
                  answered_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.commit()
            await self._ensure_topics_have_level(conn)

    async def _ensure_topics_have_level(self, conn: aiosqlite.Connection) -> None:
        columns: list[str] = []
        async with conn.execute("PRAGMA table_info(topics)") as cur:
            async for row in cur:
                columns.append(row[1])
        if "level" not in columns:
            await conn.execute("ALTER TABLE topics ADD COLUMN level TEXT DEFAULT 'basic'")
            await conn.execute("UPDATE topics SET level='basic' WHERE level IS NULL")
            await conn.commit()
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_topics_level ON topics(level)")
        await conn.commit()

    async def _remove_placeholder_topics(self, conn: aiosqlite.Connection) -> None:
        normalized = {title.casefold() for title in PLACEHOLDER_TOPIC_TITLES}
        target_ids: list[int] = []
        async with conn.execute("SELECT id, title FROM topics") as cur:
            async for topic_id, title in cur:
                if title and title.casefold() in normalized:
                    target_ids.append(topic_id)
        if not target_ids:
            return
        marks = ','.join('?' for _ in target_ids)
        params = tuple(target_ids)
        await conn.execute(
            f"DELETE FROM answers WHERE session_id IN (SELECT id FROM sessions WHERE topic_id IN ({marks}))",
            params,
        )
        await conn.execute(f"DELETE FROM sessions WHERE topic_id IN ({marks})", params)
        await conn.execute(f"DELETE FROM questions WHERE topic_id IN ({marks})", params)
        await conn.execute(f"DELETE FROM topics WHERE id IN ({marks})", params)
        await conn.commit()

    def _normalize_level(self, level: str | None, *, default: str = 'basic') -> str:
        candidate = (level or default).strip().lower()
        if candidate not in _ALLOWED_LEVELS:
            raise ValueError(f"Некорректный уровень темы: {level}")
        return candidate

    def _normalize_difficulty(self, difficulty: str | None, *, fallback: str) -> str:
        candidate = (difficulty or fallback).strip().lower()
        if candidate not in _ALLOWED_DIFFICULTIES:
            raise ValueError(f"Некорректная сложность: {difficulty}")
        return candidate

    def _prepare_question_row(self, topic_id: int, payload: dict[str, Any], topic_level: str) -> tuple[Any, ...]:
        qtype = (payload.get('qtype') or '').strip().lower()
        if qtype not in _ALLOWED_QTYPES:
            raise ValueError(f"Неверный тип вопроса: {qtype}")
        text = _normalize_text(payload.get('text', ''))
        if not text:
            raise ValueError('Текст вопроса не может быть пустым')
        difficulty = self._normalize_difficulty(payload.get('difficulty'), fallback=topic_level)
        if qtype == 'open':
            ideal_answer = _normalize_text(payload.get('ideal_answer', ''))
            if not ideal_answer:
                raise ValueError('Для open-вопроса требуется идеальный ответ')
            return (topic_id, qtype, text, None, None, ideal_answer, difficulty)
        options = payload.get('options')
        if not isinstance(options, (list, tuple)):
            raise ValueError('Для mcq необходимо передать список вариантов')
        normalized_options: list[str] = []
        for option in options:
            option_text = _normalize_text(str(option))
            if not option_text:
                raise ValueError('Пустой вариант ответа недопустим')
            normalized_options.append(option_text)
        if len(normalized_options) < 2:
            raise ValueError('Для mcq требуется минимум два варианта ответа')
        correct_index = payload.get('correct_index')
        if correct_index is None and payload.get('correct') is not None:
            correct_index = int(payload['correct']) - 1
        if correct_index is None:
            raise ValueError('Укажите correct_index для mcq вопроса')
        correct_index = int(correct_index)
        if correct_index < 0 or correct_index >= len(normalized_options):
            raise ValueError('Индекс правильного варианта выходит за пределы списка')
        ideal_answer = payload.get('ideal_answer')
        ideal_answer = _normalize_text(ideal_answer) if ideal_answer else None
        return (
            topic_id,
            qtype,
            text,
            json.dumps(normalized_options, ensure_ascii=False),
            correct_index,
            ideal_answer,
            difficulty,
        )

    async def _ensure_topic(self, conn: aiosqlite.Connection, title: str, level: str, *, active: int = 1) -> tuple[int, bool]:
        async with conn.execute("SELECT id FROM topics WHERE title=? AND level=?", (title, level)) as cur:
            row = await cur.fetchone()
        if row:
            if active is not None:
                await conn.execute("UPDATE topics SET active=? WHERE id=?", (active, row[0]))
            return row[0], False
        cur = await conn.execute("INSERT INTO topics(title, level, active) VALUES(?,?,?)", (title, level, active))
        return cur.lastrowid, True # type: ignore

    async def add_sample_data(self) -> None:
        seeds = _load_seed_topics()
        if not seeds:
            return
        async with aiosqlite.connect(self.path) as conn:
            await self._ensure_topics_have_level(conn)
            await self._remove_placeholder_topics(conn)
            for topic in seeds:
                title = _normalize_text(topic.get('title', ''))
                if not title:
                    continue
                level = self._normalize_level(topic.get('level'))
                topic_id, _ = await self._ensure_topic(conn, title, level)
                await conn.execute("DELETE FROM questions WHERE topic_id=?", (topic_id,))
                rows = []
                for question in topic.get('questions', []):
                    try:
                        rows.append(self._prepare_question_row(topic_id, question, level))
                    except ValueError:
                        continue
                if rows:
                    await conn.executemany(
                        "INSERT INTO questions(topic_id,qtype,text,options,correct_index,ideal_answer,difficulty) VALUES(?,?,?,?,?,?,?)",
                        rows,
                    )
            await conn.commit()

    async def import_topics_from_payload(self, topics: Sequence[dict[str, Any]], *, replace_default: bool = True) -> dict[str, int]:
        if not isinstance(topics, Sequence):
            raise ValueError('Ожидается список тем')
        async with aiosqlite.connect(self.path) as conn:
            await self._ensure_topics_have_level(conn)
            stats = {"topics_created": 0, "topics_updated": 0, "questions_added": 0, "questions_skipped": 0}
            for topic in topics:
                title = _normalize_text(topic.get('title', ''))
                if not title:
                    continue
                level = self._normalize_level(topic.get('level'))
                replace = bool(topic.get('replace', replace_default))
                active = int(topic.get('active', 1))
                topic_id, created = await self._ensure_topic(conn, title, level, active=active)
                stats['topics_created' if created else 'topics_updated'] += 1
                existing: set[str] = set()
                if replace:
                    await conn.execute("DELETE FROM questions WHERE topic_id=?", (topic_id,))
                else:
                    async with conn.execute("SELECT text FROM questions WHERE topic_id=?", (topic_id,)) as cur:
                        async for (text,) in cur:
                            existing.add(text)
                rows = []
                for question in topic.get('questions', []):
                    row = self._prepare_question_row(topic_id, question, level)
                    if not replace and row[2] in existing:
                        stats['questions_skipped'] += 1
                        continue
                    rows.append(row)
                if rows:
                    await conn.executemany(
                        "INSERT INTO questions(topic_id,qtype,text,options,correct_index,ideal_answer,difficulty) VALUES(?,?,?,?,?,?,?)",
                        rows,
                    )
                    stats['questions_added'] += len(rows)
            await conn.commit()
            return stats

    async def get_or_create_user(self, telegram_id: int):
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute("SELECT telegram_id, subscribed FROM users WHERE telegram_id=?", (telegram_id,))
            row = await cur.fetchone()
            if row:
                return row
            await conn.execute("INSERT INTO users(telegram_id, subscribed) VALUES(?,0)", (telegram_id,))
            await conn.commit()
            return (telegram_id, 0)

    async def set_subscribed(self, telegram_id: int) -> None:
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute("UPDATE users SET subscribed=1 WHERE telegram_id=?", (telegram_id,))
            await conn.commit()

    async def list_levels(self) -> list[str]:
        async with aiosqlite.connect(self.path) as conn:
            levels: set[str] = set()
            async with conn.execute("SELECT DISTINCT level FROM topics WHERE active=1") as cur:
                async for (level,) in cur:
                    if level:
                        levels.add(level)
            return sorted(levels, key=lambda lvl: (0 if lvl == 'basic' else 1, lvl))

    async def list_topics(self, level: str | None = None) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as conn:
            query = (
                "SELECT t.id, t.title, t.level, COUNT(q.id) as question_count "
                "FROM topics t LEFT JOIN questions q ON q.topic_id=t.id WHERE t.active=1"
            )
            params: list[Any] = []
            if level:
                query += " AND t.level=?"
                params.append(level)
            query += " GROUP BY t.id ORDER BY t.title"
            items: list[dict[str, Any]] = []
            async with conn.execute(query, params) as cur:
                async for topic_id, title, lvl, question_count in cur:
                    items.append({
                        "id": topic_id,
                        "title": title,
                        "level": lvl,
                        "question_count": question_count,
                    })
            return items

    async def count_questions(self, topic_id: int) -> int:
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM questions WHERE topic_id=?", (topic_id,))
            row = await cur.fetchone()
            return int(row[0] if row and row[0] is not None else 0)

    async def pick_questions(self, topic_id: int, limit: int, *, randomize: bool = False):
        order_clause = "ORDER BY RANDOM()" if randomize else "ORDER BY id"
        async with aiosqlite.connect(self.path) as conn:
            result = []
            async with conn.execute(
                f"SELECT id,qtype,text,options,correct_index,ideal_answer,difficulty FROM questions WHERE topic_id=? {order_clause} LIMIT ?",
                (topic_id, limit),
            ) as cur:
                async for row in cur:
                    result.append({
                        "id": row[0],
                        "qtype": row[1],
                        "text": row[2],
                        "options": json.loads(row[3]) if row[3] else None,
                        "correct_index": row[4],
                        "ideal_answer": row[5],
                        "difficulty": row[6],
                    })
            return result

    async def start_session(self, telegram_id: int, topic_id: int, total: int, mode: str):
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute(
                "INSERT INTO sessions(telegram_id, topic_id, mode, total_questions, idx, correct_count) VALUES(?,?,?,?,0,0)",
                (telegram_id, topic_id, mode, total),
            )
            await conn.commit()
            return cur.lastrowid

    async def get_session(self, session_id: int):
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute(
                "SELECT id,telegram_id,topic_id,mode,total_questions,idx,correct_count FROM sessions WHERE id=?",
                (session_id,),
            )
            row = await cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "telegram_id": row[1],
                "topic_id": row[2],
                "mode": row[3],
                "total_questions": row[4],
                "idx": row[5],
                "correct_count": row[6],
            }

    async def update_session_progress(self, session_id: int, idx: int, correct_count: int) -> None:
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                "UPDATE sessions SET idx=?, correct_count=? WHERE id=?",
                (idx, correct_count, session_id),
            )
            await conn.commit()

    async def log_answer(self, session_id: int, question_id: int, user_text: str | None, chosen_index: int | None,
                         is_correct: int | None, ai_score: int | None, ai_comment: str | None) -> None:
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                "INSERT INTO answers(session_id,question_id,user_text,chosen_index,is_correct,ai_score,ai_comment) VALUES(?,?,?,?,?,?,?)",
                (session_id, question_id, user_text, chosen_index, is_correct, ai_score, ai_comment),
            )
            await conn.commit()

    async def answers_by_topic_stats(self, session_id: int):
        async with aiosqlite.connect(self.path) as conn:
            sql = """
                SELECT t.title, t.level, COUNT(*)
                FROM answers a
                JOIN questions q ON q.id = a.question_id
                JOIN topics t ON t.id = q.topic_id
                WHERE a.session_id=?
                  AND (
                    a.is_correct = 0
                    OR (
                        a.is_correct IS NULL
                        AND (a.ai_score IS NULL OR a.ai_score < 3)
                    )
                  )
                GROUP BY t.id
                ORDER BY COUNT(*) DESC
            """
            result = []
            async with conn.execute(sql, (session_id,)) as cur:
                async for title, level, count in cur:
                    result.append((title, level, count))
            return result


db = DB()
