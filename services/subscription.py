from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from config import config
from keyboards.common import subscription_keyboard
from storage.db import db

SUBSCRIPTION_PROMPT = (
    "Чтобы пользоваться ботом, подпишитесь на канал и затем снова отправьте /start."
)


def channel_url() -> str:
    channel = config.required_channel.strip()
    if channel.startswith("@"):
        return f"https://t.me/{channel[1:]}"
    return channel


async def check_subscription(bot: Bot, user_id: int) -> bool:
    channel = config.required_channel.strip()
    if not channel:
        return True
    try:
        member = await bot.get_chat_member(channel, user_id)
    except TelegramAPIError as exc:
        logging.warning(
            "Не удалось проверить подписку пользователя %s в %s: %s",
            user_id,
            channel,
            exc,
        )
        return False
    status = getattr(member, "status", None)
    return status in {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
    }


async def ensure_subscription(message: Message, *, silent: bool = False) -> bool:
    if not message.from_user:
        return False
    user_id = message.from_user.id
    if user_id in config.admins:
        await db.set_subscribed(user_id)
        return True
    is_subscribed = await check_subscription(message.bot, user_id)  # type: ignore[arg-type]
    if is_subscribed:
        await db.set_subscribed(user_id)
        return True
    if not silent:
        url = channel_url()
        await message.answer(
            SUBSCRIPTION_PROMPT,
            reply_markup=subscription_keyboard(url) if url else None,
        )
    return False
