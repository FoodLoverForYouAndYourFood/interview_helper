import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    openai_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    required_channel: str = os.getenv("REQUIRED_CHANNEL", "@BigFrinedlyCat")
    admins: set[int] = None # type: ignore
    default_session_n: int = int(os.getenv("DEFAULT_SESSION_N", "5"))

    def __post_init__(self):
        admins_raw = os.getenv("ADMINS", "")
        self.admins = set(int(x) for x in admins_raw.split(",") if x.strip().isdigit())
        if not self.telegram_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
        if not self.openai_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

config = Config()
