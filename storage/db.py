import aiosqlite
from typing import Any, Dict, List, Optional, Tuple
import json
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "bot_data.db")

class DB:
    def __init__(self, path: str = DB_PATH):
        self.path = os.path.abspath(path)

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("""            CREATE TABLE IF NOT EXISTS users (
              telegram_id INTEGER PRIMARY KEY,
              subscribed  INTEGER DEFAULT 0,
              created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")
            await db.execute("""            CREATE TABLE IF NOT EXISTS topics (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL UNIQUE,
              active INTEGER DEFAULT 1
            )""")
            await db.execute("""            CREATE TABLE IF NOT EXISTS questions (
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
            )""")
            await db.execute("""            CREATE TABLE IF NOT EXISTS sessions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              telegram_id INTEGER NOT NULL,
              topic_id INTEGER NOT NULL,
              mode TEXT NOT NULL CHECK (mode IN ('test','open','mixed')),
              total_questions INTEGER NOT NULL,
              idx INTEGER DEFAULT 0,
              correct_count INTEGER DEFAULT 0,
              started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              finished_at DATETIME
            )""")
            await db.execute("""            CREATE TABLE IF NOT EXISTS answers (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id INTEGER NOT NULL,
              question_id INTEGER NOT NULL,
              user_text TEXT,
              chosen_index INTEGER,
              is_correct INTEGER,
              ai_score INTEGER,
              ai_comment TEXT,
              answered_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")
            await db.commit()

    async def add_sample_data(self):
        async with aiosqlite.connect(self.path) as db:
            # insert topics if empty
            cur = await db.execute("SELECT COUNT(*) FROM topics")
            cnt = (await cur.fetchone())[0] # type: ignore
            if cnt == 0:
                await db.executemany("INSERT INTO topics(title) VALUES(?)",
                                     [("Python Basics",), ("SQL Basics",)])
                await db.commit()
            # fetch topic ids
            topics = {}
            async with db.execute("SELECT id,title FROM topics") as cur2:
                async for row in cur2:
                    topics[row[1]] = row[0]

            # insert Python
            cur = await db.execute("SELECT COUNT(*) FROM questions WHERE topic_id=?", (topics["Python Basics"],))
            if (await cur.fetchone())[0] == 0: # type: ignore
                await db.executemany(
                    "INSERT INTO questions(topic_id,qtype,text,options,correct_index,ideal_answer,difficulty) VALUES(?,?,?,?,?,?,?)",
                    [
                        (topics["Python Basics"], "mcq",
                         "Какой тип у литерала 3.14 в Python?",
                         json.dumps(["int","float","decimal","str"]), 1, None, "basic"),
                        (topics["Python Basics"], "mcq",
                         "Какой оператор сравнения проверяет равенство?",
                         json.dumps(["=","==","===","eq()"]), 1, None, "basic"),
                        (topics["Python Basics"], "open",
                         "Что такое списковое включение (list comprehension) и когда его стоит использовать?",
                         None, None,
                         "Списковое включение — синтаксический сахар для генерации списков в одной строке, например [x*x for x in range(5)] вместо цикла. Используется для компактного и читаемого построения коллекций.",
                         "basic"),
                    ]
                )
                await db.commit()

            # insert SQL
            cur = await db.execute("SELECT COUNT(*) FROM questions WHERE topic_id=?", (topics["SQL Basics"],))
            if (await cur.fetchone())[0] == 0: # type: ignore
                await db.executemany(
                    "INSERT INTO questions(topic_id,qtype,text,options,correct_index,ideal_answer,difficulty) VALUES(?,?,?,?,?,?,?)",
                    [
                        (topics["SQL Basics"], "mcq",
                         "Какая команда выбирает данные из таблицы?",
                         json.dumps(["GET","PULL","SELECT","SHOW"]), 2, None, "basic"),
                        (topics["SQL Basics"], "open",
                         "Объясни разницу между INNER JOIN и LEFT JOIN.",
                         None, None,
                         "INNER JOIN оставляет только строки с совпадением по ключу в обеих таблицах. LEFT JOIN берёт все строки из левой таблицы и добавляет совпадения из правой (несовпавшие поля NULL).",
                         "basic"),
                    ]
                )
                await db.commit()

    async def get_or_create_user(self, telegram_id: int):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT telegram_id, subscribed FROM users WHERE telegram_id=?", (telegram_id,))
            row = await cur.fetchone()
            if row: return row
            await db.execute("INSERT INTO users(telegram_id, subscribed) VALUES(?,0)", (telegram_id,))
            await db.commit()
            return (telegram_id, 0)

    async def set_subscribed(self, telegram_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE users SET subscribed=1 WHERE telegram_id=?", (telegram_id,))
            await db.commit()

    async def list_topics(self):
        async with aiosqlite.connect(self.path) as db:
            res = []
            async with db.execute("SELECT id,title FROM topics WHERE active=1 ORDER BY id") as cur:
                async for row in cur: res.append({"id":row[0], "title":row[1]})
            return res

    async def pick_questions(self, topic_id: int, limit: int):
        async with aiosqlite.connect(self.path) as db:
            res = []
            async with db.execute(
                "SELECT id,qtype,text,options,correct_index,ideal_answer FROM questions WHERE topic_id=? ORDER BY id LIMIT ?",
                (topic_id, limit)) as cur:
                async for row in cur:
                    res.append({
                        "id": row[0], "qtype": row[1], "text": row[2],
                        "options": json.loads(row[3]) if row[3] else None,
                        "correct_index": row[4], "ideal_answer": row[5]
                    })
            return res

    async def start_session(self, telegram_id: int, topic_id: int, total: int, mode: str):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO sessions(telegram_id, topic_id, mode, total_questions, idx, correct_count) VALUES(?,?,?,?,0,0)",
                (telegram_id, topic_id, mode, total))
            await db.commit()
            return cur.lastrowid

    async def get_session(self, session_id: int):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT id,telegram_id,topic_id,mode,total_questions,idx,correct_count FROM sessions WHERE id=?",
                                   (session_id,))
            row = await cur.fetchone()
            if not row: return None
            return {"id":row[0],"telegram_id":row[1],"topic_id":row[2],"mode":row[3],
                    "total_questions":row[4],"idx":row[5],"correct_count":row[6]}

    async def update_session_progress(self, session_id: int, idx: int, correct_count: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE sessions SET idx=?, correct_count=? WHERE id=?",
                             (idx, correct_count, session_id))
            await db.commit()

    async def log_answer(self, session_id:int, question_id:int, user_text:str|None, chosen_index:int|None,
                        is_correct:int|None, ai_score:int|None, ai_comment:str|None):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO answers(session_id,question_id,user_text,chosen_index,is_correct,ai_score,ai_comment) VALUES(?,?,?,?,?,?,?)",
                (session_id, question_id, user_text, chosen_index, is_correct, ai_score, ai_comment)
            )
            await db.commit()

    async def answers_by_topic_stats(self, session_id: int):
        async with aiosqlite.connect(self.path) as db:
            q = """            SELECT t.title, COUNT(*)
            FROM answers a
            JOIN questions q ON q.id=a.question_id
            JOIN topics t ON t.id=q.topic_id
            WHERE a.session_id=? AND (a.is_correct=0 OR (a.is_correct IS NULL AND (a.ai_score IS NULL OR a.ai_score<3)))
            GROUP BY t.title
            ORDER BY COUNT(*) DESC
            """
            res = []
            async with db.execute(q, (session_id,)) as cur:
                async for row in cur:
                    res.append((row[0], row[1]))
            return res

db = DB()
