import json
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from config import config
from .provider import AIProvider, ScoreResult

class EvalSchema(BaseModel):
    score: int = Field(ge=0, le=5)
    comment: str

class ChatGPTProvider(AIProvider):
    def __init__(self) -> None:
        self.client = AsyncOpenAI(api_key=config.openai_key)
        self.model = config.openai_model

    async def score_open_answer(self, *, topic: str, question: str, gold: str, user_answer: str) -> ScoreResult:
        system = (
            "Ты — строгий, но доброжелательный экзаменатор. "
            "Сравни ответ студента с эталоном и оцени кратко. "
            "Верни строго JSON {\"score\":0..5,\"comment\":\"...\"}. Не раскрывай полностью эталон."
        )
        user = (
            f"Тема: {topic}\n"
            f"Вопрос: {question}\n"
            f"Эталонный ответ: {gold}\n"
            f"Ответ пользователя: {user_answer}\n"
            "Оцени близость к эталону. Краткий комментарий — что добавить/исправить."
        )
        resp = await self.client.chat.completions.create(
            model=self.model,
            temperature=0.2,
            messages=[{"role":"system","content":system},
                      {"role":"user","content":user}],
        )
        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)
        obj = EvalSchema(**data)
        return {"score": obj.score, "comment": obj.comment}

chatgpt = ChatGPTProvider()
