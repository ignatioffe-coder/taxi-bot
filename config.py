"""Конфигурация бота"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from districts import MOSCOW_DISTRICTS, MOSCOW_DISTRICTS_BY_OKRUG

# Загружаем переменные из .env файла (только для локального запуска)
load_dotenv()

# Токен бота — только из переменных окружения (безопасно!)
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError(
        "BOT_TOKEN не задан! Создай файл .env и добавь BOT_TOKEN=твой_токен\n"
        "или установи переменную окружения BOT_TOKEN."
    )

# Публичный HTTPS-адрес Mini App на Bothost; можно переопределить переменной среды.
WEBAPP_URL = (
    os.getenv("WEBAPP_URL") or
    "https://bot-1792186571-5969-taxi-bot.bothost.tech"
).strip().rstrip("/")
if WEBAPP_URL and not WEBAPP_URL.startswith("https://"):
    raise ValueError("WEBAPP_URL должен быть публичным HTTPS-адресом.")

# The browser-facing key is restricted to the Mini App domain in Yandex's dashboard.
YANDEX_MAPS_API_KEY = os.getenv("YANDEX_MAPS_API_KEY", "").strip()
TOMTOM_TRAFFIC_API_KEY = os.getenv("TOMTOM_TRAFFIC_API_KEY", "").strip()
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "").strip()
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "").strip()

# ID администратора (твой или брата) — для отчётов и управления
ADMIN_IDS = [7925732814]  # Сделано списком, как в основном коде

# Город
CITY = "Москва"

# === Файл базы данных ===
# Локально база лежит рядом с кодом. На хостинге код перезаписывается при
# каждом обновлении, поэтому базу надо держать в постоянной папке — иначе
# все накопленные коэффициенты стираются при деплое.
# Bothost (и похожие платформы) монтируют такую папку как /app/data и умеют
# передавать путь через переменную окружения DB_PATH.
def _resolve_db_path() -> str:
    env_path = os.getenv("DB_PATH") or os.getenv("DATABASE_PATH")
    if env_path:
        return env_path
    persistent = Path("/app/data")  # постоянное хранилище на хостинге
    if persistent.is_dir():
        return str(persistent / "taxi_data.db")
    return str(Path(__file__).resolve().parent / "taxi_data.db")


DB_PATH = _resolve_db_path()
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

# Минимум данных для рекомендации
MIN_DATA_POINTS = 5

# === Время ===
# Бот работает по московскому времени, а сервер (Railway/Heroku) живёт в UTC.
# Старая формула datetime.now() + 3 часа давала верный результат только на
# сервере в UTC: на машине, которая уже стоит в московской зоне, получался
# сдвиг на +3 часа, и весь анализ (часы пик, день недели) уезжал.
MOSCOW_TZ = timezone(timedelta(hours=3), name="MSK")


def get_moscow_time() -> datetime:
    """Текущее московское время (naive — в таком виде оно хранится в БД)."""
    return datetime.now(timezone.utc).astimezone(MOSCOW_TZ).replace(tzinfo=None)


def moscow_time_str(delta: timedelta = timedelta(0)) -> str:
    """Московское время в формате БД, сдвинутое на delta (например -30 дней)."""
    return (get_moscow_time() + delta).strftime('%Y-%m-%d %H:%M:%S')
