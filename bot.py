"""Telegram launcher for the MOVENTA Mini App."""

import asyncio
import logging
import os

from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)

import config
import database
import subscriptions
import yookassa

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()


def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="📱 Открыть MOVENTA",
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
        "🚕 MOVENTA — приложение для водителей такси и курьеров.\n\n"
        "Записывай смены, доходы и расходы, следи за прибылью "
        "и находи спрос по районам Москвы.",
    )


@dp.message(Command("app"))
async def cmd_app(message: types.Message):
    await send_app_launcher(message, "Запуск MOVENTA.")


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await send_app_launcher(
        message,
        "Все функции MOVENTA находятся в Mini App. "
        "Открой его кнопкой под этим сообщением.\n\n"
        "Пробный период — 7 дней, затем подписка 500 ₽ за 30 дней "
        "с автопродлением через ЮKassa. "
        "Условия: /terms. Вопросы по оплате: /paysupport.",
    )


@dp.message(Command("terms"))
async def cmd_terms(message: types.Message):
    await message.answer(subscriptions.TERMS_TEXT_RU)


@dp.message(Command("paysupport"))
async def cmd_payment_support(message: types.Message):
    user = message.from_user
    if user is None:
        return
    await asyncio.to_thread(database.set_payment_support_pending, user.id)
    await message.answer(
        "Опишите проблему с оплатой следующим сообщением — я передам её поддержке. "
        "Чтобы отменить обращение, отправьте /cancel."
    )


@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message):
    user = message.from_user
    if user and await asyncio.to_thread(
        database.cancel_payment_support_pending, user.id
    ):
        await message.answer("Обращение отменено.")
        return
    await message.answer("Активных обращений для отмены нет.")


@dp.message(Command("reply"))
async def cmd_support_reply(message: types.Message):
    admin = message.from_user
    if admin is None or admin.id not in config.ADMIN_IDS:
        await message.answer("Эта команда доступна только поддержке.")
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3 or not parts[1].isdigit():
        await message.answer("Формат команды: /reply <user_id> <сообщение>")
        return
    try:
        await bot.send_message(int(parts[1]), parts[2])
    except TelegramAPIError:
        logger.exception("Could not send support reply to user %s", parts[1])
        await message.answer("Не удалось отправить ответ пользователю.")
        return
    await message.answer("Ответ отправлен.")


@dp.message()
async def open_app_hint(message: types.Message):
    user = message.from_user
    if user and await asyncio.to_thread(
        database.consume_payment_support_pending, user.id
    ):
        delivered = False
        for admin_id in config.ADMIN_IDS:
            try:
                await bot.copy_message(
                    chat_id=admin_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                )
                await bot.send_message(
                    admin_id,
                    f"Вопрос по оплате от пользователя {user.id} "
                    f"(@{user.username or 'без username'}).",
                )
                delivered = True
            except TelegramAPIError:
                logger.exception(
                    "Could not forward payment support message from user %s",
                    user.id,
                )
        if delivered:
            await message.answer("Сообщение передано поддержке. Ответим в этом чате.")
        else:
            await asyncio.to_thread(database.set_payment_support_pending, user.id)
            await message.answer(
                "Не удалось передать сообщение поддержке. Попробуйте ещё раз позже."
            )
        return
    await send_app_launcher(message, "Открой MOVENTA кнопкой ниже.")


async def document_reminder_loop():
    while True:
        try:
            today = database.get_moscow_time().date()
            reminders = await asyncio.to_thread(
                database.get_due_document_reminders, today
            )
            for reminder in reminders:
                days_left = reminder["days_left"]
                when = "срок действия заканчивается сегодня" if days_left == 0 else (
                    f"срок действия закончится через {days_left} дн."
                )
                expiry = (
                    f"{reminder['expiry_date'][8:10]}."
                    f"{reminder['expiry_date'][5:7]}."
                    f"{reminder['expiry_date'][:4]}"
                )
                text = (
                    f"Напоминание: {reminder['document_label']} — {when} "
                    f"({expiry}).\n\nПроверь документ и обнови дату в профиле."
                )
                try:
                    await bot.send_message(
                        reminder["user_id"],
                        text,
                        reply_markup=get_main_keyboard(),
                    )
                except TelegramAPIError:
                    logger.exception(
                        "Could not send document reminder to user %s",
                        reminder["user_id"],
                    )
                    continue
                await asyncio.to_thread(database.log_document_reminder, reminder)
                logger.info(
                    "Sent %s document reminder to user %s",
                    reminder["document_key"],
                    reminder["user_id"],
                )
        except Exception:
            logger.exception("Document reminder cycle failed")
        await asyncio.sleep(60 * 60)


async def notification_loop():
    """Рассылка уведомлений о спросе и погоде (ТЗ, п. 16)."""
    while True:
        try:
            sent = await notifications.send_due_notifications(bot)
            if sent:
                logger.info("Отправлено уведомлений: %s", sent)
        except Exception:
            logger.exception("Notification cycle failed")
        await asyncio.sleep(60 * 15)


async def notify_yookassa_renewal_failure(user_id: int) -> None:
    try:
        await bot.send_message(
            user_id,
            "Не удалось автоматически продлить подписку MOVENTA. "
            "Автопродление отключено; оформи подписку заново в профиле Mini App.",
            reply_markup=get_main_keyboard(),
        )
    except TelegramAPIError:
        logger.exception(
            "Could not notify user %s about renewal failure",
            user_id,
        )


async def yookassa_renewal_loop():
    """Run due recurring charges; ambiguous requests reuse the same idempotency key."""
    while True:
        if yookassa.is_configured():
            try:
                due_subscriptions = await asyncio.to_thread(
                    database.get_due_yookassa_subscriptions
                )
                for subscription in due_subscriptions:
                    user_id = subscription["user_id"]
                    try:
                        payment = await asyncio.to_thread(
                            database.start_yookassa_payment,
                            user_id,
                            "renewal",
                        )
                        if payment["provider_payment_id"]:
                            provider_payment = await asyncio.to_thread(
                                yookassa.get_payment,
                                payment["provider_payment_id"],
                            )
                        else:
                            provider_payment = await asyncio.to_thread(
                                yookassa.create_recurring_payment,
                                payment["payment_ref"],
                                user_id,
                                payment["payment_method_id"],
                                payment["idempotence_key"],
                            )
                            updated = await asyncio.to_thread(
                                database.update_yookassa_payment,
                                payment["payment_ref"],
                                provider_payment.get("id", ""),
                                provider_payment.get("status", "pending"),
                            )
                            if not updated:
                                logger.error(
                                    "Could not save YooKassa renewal %s",
                                    payment["payment_ref"],
                                )
                                continue
                        if provider_payment.get("status") == "succeeded":
                            recorded = await asyncio.to_thread(
                                database.record_yookassa_payment,
                                payment["payment_ref"],
                                provider_payment,
                            )
                            if not recorded:
                                logger.error(
                                    "Could not apply YooKassa renewal %s",
                                    payment["payment_ref"],
                                )
                        elif provider_payment.get("status") == "canceled":
                            canceled_user = await asyncio.to_thread(
                                database.cancel_yookassa_payment,
                                payment["payment_ref"],
                            )
                            if canceled_user is not None:
                                await notify_yookassa_renewal_failure(canceled_user)
                    except yookassa.YooKassaError as exc:
                        logger.exception(
                            "YooKassa renewal failed for user %s",
                            user_id,
                        )
                        if exc.status_code == 400:
                            canceled_user = await asyncio.to_thread(
                                database.cancel_yookassa_payment,
                                payment["payment_ref"],
                            )
                            if canceled_user is not None:
                                await notify_yookassa_renewal_failure(canceled_user)
                    except ValueError:
                        logger.exception(
                            "Invalid YooKassa renewal state for user %s",
                            user_id,
                        )
            except Exception:
                logger.exception("YooKassa renewal cycle failed")
        await asyncio.sleep(60 * 15)


async def main():
    database.init_db()
    logger.info("База данных инициализирована")

    from mini_app import create_app

    app_runner = web.AppRunner(create_app())
    await app_runner.setup()
    site = web.TCPSite(app_runner, "0.0.0.0", int(os.getenv("PORT", "8080")))
    await site.start()
    logger.info("Mini App server started")

    await bot.set_my_commands([
        BotCommand(command="start", description="Запустить MOVENTA"),
        BotCommand(command="app", description="Открыть Mini App"),
        BotCommand(command="help", description="Помощь"),
        BotCommand(command="terms", description="Условия подписки"),
        BotCommand(command="paysupport", description="Помощь с оплатой"),
    ])
    await bot.delete_webhook(drop_pending_updates=False)
    logger.info("Бот запущен!")
    reminder_task = asyncio.create_task(
        document_reminder_loop(), name="document-reminders"
    )
    notification_task = asyncio.create_task(
        notification_loop(), name="notifications"
    )
    renewal_task = asyncio.create_task(
        yookassa_renewal_loop(), name="yookassa-renewals"
    )
    try:
        await dp.start_polling(bot)
    finally:
        reminder_task.cancel()
        notification_task.cancel()
        renewal_task.cancel()
        for task in (reminder_task, notification_task, renewal_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        await app_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
