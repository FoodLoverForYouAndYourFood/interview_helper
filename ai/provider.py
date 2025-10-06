from typing import TypedDict

class ScoreResult(TypedDict):
    score: int
    comment: str

class AIProvider:
    async def score_open_answer(self, *, topic: str, question: str, gold: str, user_answer: str) -> ScoreResult:
        raise NotImplementedError
