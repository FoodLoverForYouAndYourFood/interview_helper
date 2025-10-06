# Interview Bot (aiogram + SQLite + ChatGPT)

## Quick start
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # fill in tokens
python main.py
```

- /start — приветствие + проверка подписки
- /quiz — выбор темы и запуск теста (MCQ + открытые)
- /cancel — прервать текущую сессию
- Админ: /add_topic, /add_q, /reload, /stats
