"""Telegram launcher for the Taxi Forecast Mini App."""

import asyncio
import logging
import os

from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

import config
import database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()


def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="📱 Открыть Taxi Forecast",
                web_app=WebAppInfo(url=config.WEBAPP_URL),
            )
        ]],
        resize_keyboard=True,
    )


async def send_app_launcher(message: types.Message, text: str) -> None:
    await message.answer(text, reply_markup=types.ReplyKeyboardRemove())
    await message.answer(
        "Открой Mini App кнопкой ниже:",
        reply_markup=get_main_keyboard(),
    )


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = message.from_user
    if user:
        database.add_user(
            user.id,
            user.username or "",
            user.first_name or "",
            user.last_name or "",
        )

    await send_app_launcher(
        message,
        "🚕 Taxi Forecast — приложение для водителей такси и курьеров.\n\n"
        "Записывай смены, доходы и расходы, следи за прибылью "
        "и находи спрос по районам Москвы.",
    )


@dp.message(Command("app"))
async def cmd_app(message: types.Message):
    await send_app_launcher(message, "Запуск Taxi Forecast.")


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await send_app_launcher(
        message,
        "Все функции Taxi Forecast находятся в Mini App. "
        "Открой его кнопкой под этим сообщением.",
    )


@dp.message()
async def open_app_hint(message: types.Message):
    await send_app_launcher(message, "Открой Taxi Forecast кнопкой ниже.")


async def main():
    database.init_db()
    logger.info("База данных инициализирована")

    from mini_app import create_app

    app_runner = web.AppRunner(create_app())
    await app_runner.setup()
    site = web.TCPSite(app_runner, "0.0.0.0", int(os.getenv("PORT", "8080")))
    await site.start()
    logger.info("Mini App server started")

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот запущен!")
    try:
        await dp.start_polling(bot)
    finally:
        await app_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
