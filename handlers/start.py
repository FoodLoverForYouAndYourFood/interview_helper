from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command, CommandStart
from aiogram.types import ChatMemberUpdated, Message

from keyboards.common import main_menu_keyboard, subscription_keyboard
from services.subscription import channel_url, ensure_subscription
from storage.db import db

router = Router()

_COMMANDS: list[tuple[str, str]] = [
    ("/start", "обновить приветствие и меню"),
    ("/quiz", "начать тренировочный квиз"),
    ("/help", "описание возможностей бота"),
]

WELCOME_TEXT = (
    "Привет! Я бот для подготовки к собеседованиям. "
    "Помогу потренироваться на реальных вопросах."
)

HELP_TEXT = (
    "Как пользоваться ботом:\n"
    "- Нажмите «🚀 Запустить квиз», чтобы выбрать тему.\n"
    "- Отвечайте на вопросы, вводя номер варианта или текст ответа.\n"
    "- В любой момент жмите «⬅️ Главное меню», чтобы вернуться."
)

INTRO_DETAILS = (
    "Зачем этот бот:\n"
    "• Практикуйтесь на подборке реальных вопросов.\n"
    "• Получайте пояснения и рекомендации после ответов.\n"
    "\nКак начать работу:\n"
    "1. Подпишитесь на обязательный канал (кнопка ниже).\n"
    "2. Отправьте /start, чтобы открыть меню.\n"
    "3. Выберите «🚀 Запустить квиз» и следуйте подсказкам."
)


def _commands_text() -> str:
    lines = ["Доступные команды:"]
    lines.extend(f"{cmd} - {desc}" for cmd, desc in _COMMANDS)
    lines.append("Можно также пользоваться кнопками ниже 👇")
    return "\n".join(lines)


async def _send_menu(message: Message, *, include_greeting: bool) -> None:
    parts: list[str] = []
    if include_greeting:
        parts.append(WELCOME_TEXT)
    parts.append(_commands_text())
    await message.answer("\n\n".join(parts), reply_markup=main_menu_keyboard())


@router.my_chat_member()
async def on_first_contact(event: ChatMemberUpdated, bot: Bot) -> None:
    if event.chat.type != "private":
        return
    if event.new_chat_member.status not in {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
    }:
        return
    if event.old_chat_member.status in {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
    }:
        return
    if not event.from_user:
        return
    await db.get_or_create_user(event.from_user.id)
    intro_text = "\n\n".join([WELCOME_TEXT, INTRO_DETAILS])
    url = channel_url()
    await bot.send_message(
        event.chat.id,
        intro_text,
        reply_markup=subscription_keyboard(url) if url else None,
    )


@router.message(CommandStart())
async def cmd_start(message: Message, subscription_verified: bool | None = None) -> None:
    if subscription_verified is False:
        return
    if not message.from_user:
        return
    await db.get_or_create_user(message.from_user.id)
    if subscription_verified is None:
        if not await ensure_subscription(message, silent=True):
            return
    await _send_menu(message, include_greeting=True)


@router.message(Command("menu"))
@router.message(F.text.casefold() == "📋 список команд".casefold())
async def cmd_menu(message: Message, subscription_verified: bool | None = None) -> None:
    if subscription_verified is False:
        return
    if not message.from_user:
        return
    await db.get_or_create_user(message.from_user.id)
    if subscription_verified is None:
        if not await ensure_subscription(message, silent=True):
            return
    await message.answer(_commands_text(), reply_markup=main_menu_keyboard())


@router.message(Command("help"))
@router.message(F.text.casefold() == "ℹ️ помощь".casefold())
async def cmd_help(message: Message, subscription_verified: bool | None = None) -> None:
    if subscription_verified is False:
        return
    if not message.from_user:
        return
    await db.get_or_create_user(message.from_user.id)
    if subscription_verified is None:
        if not await ensure_subscription(message, silent=True):
            return
    await message.answer(HELP_TEXT, reply_markup=main_menu_keyboard())
