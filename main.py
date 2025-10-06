import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from config import config
from middlewares.subscription import SubscriptionMiddleware
from storage.db import db
from handlers.start import router as start_router
from handlers.quiz import router as quiz_router
from handlers.admin import router as admin_router


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    admin_list = ", ".join(str(admin_id) for admin_id in sorted(config.admins))
    if admin_list:
        logging.info("Admin IDs: %s", admin_list)
    else:
        logging.warning("Admin list is empty. Set ADMINS in .env to enable admin commands.")
    await db.init()
    await db.add_sample_data()

    bot = Bot(token=config.telegram_token)
    dp = Dispatcher()
    dp.message.middleware(SubscriptionMiddleware())
    dp.include_router(start_router)
    dp.include_router(quiz_router)
    dp.include_router(admin_router)

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Показать главное меню"),
            BotCommand(command="quiz", description="Начать тренировочный квиз"),
            BotCommand(command="help", description="Справка по боту"),
        ]
    )

    print("Bot is running...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
