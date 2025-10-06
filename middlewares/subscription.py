from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message

from config import config
from services.subscription import ensure_subscription
from storage.db import db


class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)
        if event.chat.type != "private":
            return await handler(event, data)
        if not event.from_user:
            return await handler(event, data)

        await db.get_or_create_user(event.from_user.id)

        if event.from_user.id in config.admins:
            data["subscription_verified"] = True
            return await handler(event, data)

        is_subscribed = await ensure_subscription(event)
        data["subscription_verified"] = is_subscribed
        if not is_subscribed:
            return None
        return await handler(event, data)
