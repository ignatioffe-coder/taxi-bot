"""
Taxi Forecast Bot — бот для таксистов Москвы
"""

import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, BufferedInputFile

import config
import database
import analytics
import heatmap

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()


def get_weather():
    """Получение реальной погоды в Москве через wttr.in"""
    try:
        import requests
        url = "https://wttr.in/Moscow?format=%C+%t&lang=ru"
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            text = response.text.strip()
            
            # Словарь для перевода погодных условий на русский с эмодзи
            weather_map = {
                "Clear": "☀️ Ясно",
                "Sunny": "☀️ Солнечно",
                "Partly cloudy": "🌤️ Переменная облачность",
                "Cloudy": "☁️ Облачно",
                "Overcast": "☁️ Пасмурно",
                "Rain": "🌧️ Дождь",
                "Light rain": "🌧️ Небольшой дождь",
                "Heavy rain": "🌧️ Сильный дождь",
                "Rain, Thunderstorm": "⛈️ Гроза с дождём",
                "Thunderstorm": "⛈️ Гроза",
                "Light Rain With Thunderstorm": "⛈️ Гроза с дождём",
                "Snow": "❄️ Снег",
                "Light snow": "❄️ Небольшой снег",
                "Heavy snow": "❄️ Сильный снег",
                "Fog": "🌫️ Туман",
                "Mist": "🌫️ Дымка"
            }
            
            # Ищем соответствие
            for eng, rus in weather_map.items():
                if eng.lower() in text.lower():
                    return rus
            
            # Если не нашли, возвращаем как есть
            return text.split()[0] if text else "☀️ Ясно"
    except Exception as e:
        logger.warning(f"Ошибка получения погоды: {e}")
    
    # Запасной вариант на основе времени суток
    hour = datetime.now().hour
    if 6 <= hour < 12:
        return "🌅 Утро"
    elif 12 <= hour < 18:
        return "☀️ День"
    elif 18 <= hour < 23:
        return "🌙 Вечер"
    else:
        return "🌙 Ночь"


def get_main_keyboard():
    kb = [
        [KeyboardButton(text="📊 Рекомендации сейчас")],
        [KeyboardButton(text="📍 Прислать коэффициент")],
        [KeyboardButton(text="📈 Моя статистика")],
        [KeyboardButton(text="🏆 Топ моменты")],
        [KeyboardButton(text="🗺️ Карта спроса")],
        [KeyboardButton(text="🌡️ Погода")],
        [KeyboardButton(text="❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def get_districts_keyboard():
    districts = config.MOSCOW_DISTRICTS
    kb = []
    for i in range(0, len(districts), 2):
        row = []
        row.append(KeyboardButton(text=districts[i]))
        if i + 1 < len(districts):
            row.append(KeyboardButton(text=districts[i + 1]))
        kb.append(row)
    kb.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def get_weather_keyboard():
    kb = [
        [KeyboardButton(text="☀️ Ясно"), KeyboardButton(text="🌤️ Облачно")],
        [KeyboardButton(text="🌧️ Дождь"), KeyboardButton(text="❄️ Снег")],
        [KeyboardButton(text="🌫️ Туман"), KeyboardButton(text="🌩️ Гроза")],
        [KeyboardButton(text="⏩ Пропустить")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def get_confirm_keyboard():
    kb = [
        [KeyboardButton(text="✅ Всё верно")],
        [KeyboardButton(text="❌ Изменить")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


user_temp_data = {}


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = message.from_user
    database.add_user(user.id, user.username or "", user.first_name or "", user.last_name or "")

    text = (
        f"👋 Привет, {user.first_name}!\n\n"
        f"🚕 Taxi Forecast Bot — помощник для таксистов Москвы.\n\n"
        f"Я собираю данные о коэффициентах по районам и помогаю понять, "
        f"где и когда выгоднее работать.\n\n"
        f"📍 Как пользоваться:\n"
        f"1. Нажми «Прислать коэффициент»\n"
        f"2. Выбери район\n"
        f"3. Введи текущий коэффициент (например: 1.3)\n"
        f"4. Получай рекомендации и смотри статистику!\n\n"
        f"💡 Чем больше водителей присылают данные — тем точнее прогнозы."
    )
    await message.answer(text, reply_markup=get_main_keyboard())


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    text = (
        "❓ Помощь\n\n"
        "📍 Прислать коэффициент — сообщи текущий коэффициент в своём районе\n\n"
        "📊 Рекомендации сейчас — куда лучше ехать прямо сейчас\n\n"
        "📈 Моя статистика — сколько данных ты прислал\n\n"
        "🏆 Топ моменты — лучшие коэффициенты за всё время\n\n"
        "🗺️ Карта спроса — визуальная карта высокого спроса\n\n"
        "🌡️ Погода — узнать погоду в Москве\n\n"
        "💡 Советы:\n"
        "• Присылай данные регулярно — хотя бы раз в час\n"
        "• Указывай погоду — это влияет на точность\n"
        "• Делись ботом с коллегами — чем больше данных, тем лучше прогнозы"
    )
    await message.answer(text, reply_markup=get_main_keyboard())


@dp.message(Command("weather"))
async def cmd_weather(message: types.Message):
    weather = get_weather()
    await message.answer(f"🌡️ Погода в Москве сейчас:\n\n{weather}", reply_markup=get_main_keyboard())


@dp.message(Command("map"))
async def cmd_map(message: types.Message):
    await map_handler(message)


@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ Нет доступа")
        return

    report = analytics.get_weekly_report()
    await message.answer(report)


@dp.message(F.text == "📊 Рекомендации сейчас")
async def recommendations_handler(message: types.Message):
    await message.answer("⏳ Анализирую данные...")
    
    # Получаем погоду
    weather = get_weather()
    
    # Основные рекомендации
    text = analytics.get_current_recommendations()
    
    # Добавляем погоду
    full_text = f"🌡️ {weather}\n\n{text}"
    
    # Добавляем совет по погоде
    if "дождь" in weather.lower() or "гроза" in weather.lower():
        full_text += "\n\n💡 Совет: Из-за дождя спрос выше обычного! 👍"
    elif "снег" in weather.lower():
        full_text += "\n\n💡 Совет: Снегопад = много заказов, но езжайте осторожнее! ❄️"
    elif "солнечно" in weather.lower() or "ясно" in weather.lower():
        full_text += "\n\n💡 Совет: Хорошая погода = больше поездок по городу ☀️"
    elif "ночь" in weather.lower():
        full_text += "\n\n💡 Совет: Ночью аэропорты и клубы дают высокие коэффициенты 🌙"
    
    await message.answer(full_text, reply_markup=get_main_keyboard())


@dp.message(F.text == "📍 Прислать коэффициент")
async def send_coefficient_start(message: types.Message):
    user_temp_data[message.from_user.id] = {"step": "district"}
    text = (
        "📍 Выбери район\n\n"
        "Где ты сейчас работаешь? Выбери из списка:"
    )
    await message.answer(text, reply_markup=get_districts_keyboard())


@dp.message(F.text == "📈 Моя статистика")
async def my_stats_handler(message: types.Message):
    stats = database.get_user_stats(message.from_user.id)
    if not stats or stats.get('total_records', 0) == 0:
        await message.answer(
            "📊 Ты пока не присылал коэффициентов.\n\n"
            "Нажми «Прислать коэффициент», чтобы начать!",
            reply_markup=get_main_keyboard()
        )
        return

    text = (
        f"📈 Твоя статистика\n\n"
        f"📝 Всего записей: {stats['total_records']}\n"
        f"📈 Средний коэффициент: {round(stats['avg_coef'], 2)}x\n"
        f"🕐 Последняя запись: {stats['last_record']}\n\n"
        f"💡 Присылай данные чаще — помогаешь себе и коллегам!"
    )
    await message.answer(text, reply_markup=get_main_keyboard())


@dp.message(F.text == "🏆 Топ моменты")
async def top_moments_handler(message: types.Message):
    text = analytics.get_top_moments()
    await message.answer(text, reply_markup=get_main_keyboard())


@dp.message(F.text == "🗺️ Карта спроса")
async def map_handler(message: types.Message):
    await message.answer("🗺️ Генерирую карту спроса... Подождите секунду...")
    
    try:
        # Генерируем карту
        img_buf = heatmap.generate_map()
        
        # Отправляем как фото
        photo = BufferedInputFile(img_buf.getvalue(), filename="demand_map.png")
        
        await message.answer_photo(
            photo, 
            caption="🗺️ Карта спроса такси в Москве\n\n"
                    "🟢 Зелёный — низкий (<1.3x)\n"
                    "🟡 Жёлтый — средний (1.3-1.7x)\n"
                    "🟠 Оранжевый — высокий (1.7-2.1x)\n"
                    "🔴 Красный — очень высокий (2.1-2.5x)\n"
                    "🟣 Фиолетовый — максимальный (>2.5x)",
            reply_markup=get_main_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка при создании карты: {e}")
        await message.answer(
            "❌ Не удалось создать карту.\n\n"
            "Возможные причины:\n"
            "• Недостаточно данных в базе\n"
            "• Ошибка библиотек\n\n"
            "Попробуйте позже, когда накопится больше данных.",
            reply_markup=get_main_keyboard()
        )


@dp.message(F.text == "🌡️ Погода")
async def weather_button_handler(message: types.Message):
    weather = get_weather()
    await message.answer(f"🌡️ Погода в Москве сейчас:\n\n{weather}", reply_markup=get_main_keyboard())


@dp.message(F.text == "❓ Помощь")
async def help_button_handler(message: types.Message):
    await cmd_help(message)


@dp.message(F.text == "⬅️ Назад")
async def back_handler(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_temp_data:
        del user_temp_data[user_id]
    await message.answer("Главное меню:", reply_markup=get_main_keyboard())


@dp.message(lambda msg: msg.from_user.id in user_temp_data)
async def state_handler(message: types.Message):
    user_id = message.from_user.id
    data = user_temp_data.get(user_id, {})
    step = data.get("step")

    if step == "district":
        district = message.text
        if district == "⬅️ Назад":
            del user_temp_data[user_id]
            await message.answer("Главное меню:", reply_markup=get_main_keyboard())
            return

        data["district"] = district
        data["step"] = "coefficient"
        user_temp_data[user_id] = data

        await message.answer(
            f"📍 Район: {district}\n\n"
            f"Теперь введи текущий коэффициент (только число, например: 1.3 или 2.0)",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    if step == "coefficient":
        try:
            coef = float(message.text.replace(",", "."))
            if coef < 0.5 or coef > 5.0:
                await message.answer("⚠️ Коэффициент выглядит странно. Обычно это 1.0-3.0. Введи ещё раз:")
                return

            data["coefficient"] = coef
            data["step"] = "weather"
            user_temp_data[user_id] = data

            await message.answer(
                f"✅ Коэффициент: {coef}x\n\n"
                f"Какая сейчас погода? (это помогает точности прогнозов)",
                reply_markup=get_weather_keyboard()
            )
            return

        except ValueError:
            await message.answer("⚠️ Введи число, например: 1.3")
            return

    if step == "weather":
        weather = message.text
        if weather == "⏩ Пропустить":
            weather = ""
        elif weather in ["☀️ Ясно", "🌤️ Облачно", "🌧️ Дождь", "❄️ Снег", "🌫️ Туман", "🌩️ Гроза"]:
            weather = weather[2:]  # убираем эмодзи

        data["weather"] = weather
        data["step"] = "confirm"
        user_temp_data[user_id] = data

        text = (
            f"📋 Проверь данные:\n\n"
            f"📍 Район: {data['district']}\n"
            f"📈 Коэффициент: {data['coefficient']}x\n"
            f"🌤️ Погода: {weather or 'не указана'}\n\n"
            f"Всё верно?"
        )
        await message.answer(text, reply_markup=get_confirm_keyboard())
        return

    if step == "confirm":
        if message.text == "✅ Всё верно":
            # Если погода не была указана вручную, подставляем автоматическую
            weather_value = data.get("weather", "")
            if not weather_value:
                weather_value = get_weather()
            
            success = database.add_coefficient(
                user_id=user_id,
                username=message.from_user.username or "",
                district=data["district"],
                coefficient=data["coefficient"],
                weather=weather_value
            )

            if success:
                forecast = analytics.get_district_forecast(data["district"])
                await message.answer(
                    f"✅ Сохранено!\n\n"
                    f"Спасибо за данные! Вот что я знаю об этом районе:\n\n{forecast}",
                    reply_markup=get_main_keyboard()
                )
            else:
                await message.answer(
                    "❌ Ошибка сохранения. Попробуй ещё раз.",
                    reply_markup=get_main_keyboard()
                )

            del user_temp_data[user_id]

        elif message.text == "❌ Изменить":
            data["step"] = "district"
            user_temp_data[user_id] = data
            await message.answer(
                "Ок, начнём заново. Выбери район:",
                reply_markup=get_districts_keyboard()
            )
        return


@dp.message()
async def any_text_handler(message: types.Message):
    try:
        coef = float(message.text.replace(",", "."))
        if 0.5 <= coef <= 5.0:
            user_temp_data[message.from_user.id] = {
                "step": "district",
                "coefficient": coef
            }
            await message.answer(
                f"Похоже, ты прислал коэффициент {coef}x\n\n"
                f"Теперь выбери район:",
                reply_markup=get_districts_keyboard()
            )
            return
    except ValueError:
        pass

    await message.answer(
        "Не понял команду. Используй кнопки ниже или /help",
        reply_markup=get_main_keyboard()
    )


async def main():
    database.init_db()
    logger.info("База данных инициализирована")

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

# Добавьте в bot.py новые обработчики

@dp.message(Command("traffic"))
async def traffic_forecast(message: types.Message):
    """Прогноз пробок"""
    await message.answer("🚗 Анализирую пробки...")
    text = analytics.get_traffic_forecast()
    await message.answer(text, parse_mode='Markdown')


@dp.message(Command("district_traffic"))
async def district_traffic_handler(message: types.Message):
    """Анализ пробок по району"""
    # Просим ввести район
    await message.answer("Введите район Москвы (например: Центр (Тверская, Арбат), Курский вокзал, Аэропорт):")
    
    @dp.message(lambda m: m.text and not m.text.startswith('/'))
    async def get_district(m):
        text = analytics.get_district_traffic(m.text)
        await m.answer(text, parse_mode='Markdown')


# Добавьте кнопку в главное меню
def get_main_keyboard():
    kb = [
        [KeyboardButton(text="📊 Рекомендации сейчас")],
        [KeyboardButton(text="📍 Прислать коэффициент")],
        [KeyboardButton(text="📈 Моя статистика")],
        [KeyboardButton(text="🏆 Топ моменты")],
        [KeyboardButton(text="🗺️ Карта спроса")],
        [KeyboardButton(text="🚗 Пробки")],  # Новая кнопка
        [KeyboardButton(text="📊 Прогноз пробок")],  # Новая кнопка
        [KeyboardButton(text="🚕 Mini App")],
        [KeyboardButton(text="🌡️ Погода")],
        [KeyboardButton(text="❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


# Обработчики новых кнопок
@dp.message(F.text == "🚗 Пробки")
async def traffic_button(message: types.Message):
    await traffic_forecast(message)


@dp.message(F.text == "📊 Прогноз пробок")
async def traffic_forecast_button(message: types.Message):
    await traffic_forecast(message)