"""Уведомления пользователям (ТЗ, п. 16).

Правила:
  * уважаем настройки из профиля (какие уведомления включены);
  * уважаем «время тишины» и частоту (сразу / раз в час / раз в день);
  * не повторяем одно и то же чаще, чем позволяет частота;
  * пишем коротко — это сообщение в Telegram, а не отчёт.

Вся логика отбора вынесена в чистые функции, чтобы её можно было
проверять тестами без сети и без базы.
"""

import asyncio
import logging
from datetime import datetime, timedelta

import config
import database
import demand_forecasting
import external_factors

logger = logging.getLogger(__name__)

# Минимальный промежуток между уведомлениями одного вида
FREQUENCY_MINUTES = {"immediate": 15, "hourly": 60, "daily": 1440}

# Какие настройки включают каждый вид уведомления
KIND_PREFERENCES = {
    "demand": ("demand_changes", "promising_areas"),
    "weather": ("weather_changes",),
}

# Порог, ниже которого район не упоминаем
MIN_SCORE = 60
# Заметная разница между «сейчас» и горизонтом 30 минут
HORIZON_NOTICE_DELTA = 5
# Погода начинает интересовать, когда влияние достаточно сильное
WEATHER_MIN_IMPACT = 0.5

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _clock_minutes(value: str | None) -> int | None:
    """«22:30» -> 1350. Пустая строка или мусор -> None."""
    if not value:
        return None
    parts = str(value).strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return hours * 60 + minutes


def in_quiet_hours(now: datetime, quiet_start: str | None, quiet_end: str | None) -> bool:
    """Попадает ли время в «тихие часы». Понимает переход через полночь."""
    start = _clock_minutes(quiet_start)
    end = _clock_minutes(quiet_end)
    if start is None or end is None or start == end:
        return False
    current = now.hour * 60 + now.minute
    if start < end:
        return start <= current < end
    return current >= start or current < end


def is_due(frequency: str | None, now: datetime, last_sent_at: datetime | None) -> bool:
    """Пора ли отправлять, если учитывать частоту."""
    if last_sent_at is None:
        return True
    interval = FREQUENCY_MINUTES.get(frequency or "daily", 1440)
    return now - last_sent_at >= timedelta(minutes=interval)


def _parse_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], DATETIME_FORMAT)
    except ValueError:
        return None


def build_demand_message(work_type: str, forecast: dict) -> str | None:
    """Короткое сообщение о перспективных районах. None, если хвастаться нечем."""
    items = forecast.get("items") or []
    leaders = [item for item in items if int(item.get("score") or 0) >= MIN_SCORE]
    if not leaders:
        return None

    subject = "такси" if work_type == "taxi" else "доставку"
    lines = [f"📈 Растёт спрос на {subject}"]
    for item in leaders[:3]:
        district = item.get("district") or "район"
        score = int(item.get("score") or 0)
        horizon = (item.get("horizons") or {}).get("30")
        if horizon is not None and int(horizon) - score >= HORIZON_NOTICE_DELTA:
            lines.append(f"• {district} — {score} (через 30 мин: {int(horizon)})")
        else:
            lines.append(f"• {district} — {score}")
    lines.append("Открой MOVENTA, чтобы увидеть карту.")
    return "\n".join(lines)


def build_weather_message(work_type: str, weather: dict | None) -> str | None:
    """Сообщение о погоде, если она заметно влияет на спрос."""
    if not weather:
        return None
    try:
        impact = float(weather.get("impact") or 0)
    except (TypeError, ValueError):
        return None
    if impact < WEATHER_MIN_IMPACT:
        return None

    emoji = weather.get("emoji") or "🌧️"
    text = (weather.get("text") or "непогода").lower()
    subject = "такси" if work_type == "taxi" else "доставку"
    return (
        f"{emoji} Сейчас {text}. В такую погоду спрос на {subject} обычно растёт — "
        "хорошее время выйти на смену."
    )


def collect_messages(recipient: dict, forecast: dict, weather: dict | None) -> list[tuple[str, str]]:
    """Что и в каком виде отправлять этому пользователю с учётом его настроек."""
    work_type = recipient.get("work_type") or "taxi"
    messages: list[tuple[str, str]] = []

    if any(recipient.get(field) for field in KIND_PREFERENCES["demand"]):
        text = build_demand_message(work_type, forecast)
        if text:
            messages.append(("demand", text))

    if recipient.get("weather_changes"):
        text = build_weather_message(work_type, weather)
        if text:
            messages.append(("weather", text))

    return messages


async def send_due_notifications(bot) -> int:
    """Один цикл рассылки. Возвращает число отправленных сообщений."""
    now = config.get_moscow_time()
    factors = await asyncio.to_thread(external_factors.load_live_factors)
    weather = factors.get("weather")
    recipients = await asyncio.to_thread(database.get_notification_recipients)
    sent = 0

    for recipient in recipients:
        if in_quiet_hours(now, recipient.get("quiet_start"), recipient.get("quiet_end")):
            continue
        work_type = recipient.get("work_type") or "taxi"
        try:
            inputs = await asyncio.to_thread(
                database.get_demand_forecast_inputs,
                recipient["user_id"],
                work_type,
            )
            forecast = demand_forecasting.build_forecast(
                work_type, inputs, now=now, factors=factors
            )
        except Exception:
            logger.exception(
                "Не удалось построить прогноз для уведомления, user=%s",
                recipient.get("user_id"),
            )
            continue

        for kind, text in collect_messages(recipient, forecast, weather):
            last_raw = await asyncio.to_thread(
                database.get_last_notification_at, recipient["user_id"], kind
            )
            if not is_due(recipient.get("frequency"), now, _parse_datetime(last_raw)):
                continue
            try:
                await bot.send_message(recipient["user_id"], text)
            except Exception:  # noqa: BLE001 - заблокировал бота и т.п.
                logger.warning(
                    "Не удалось отправить уведомление %s, user=%s",
                    kind,
                    recipient.get("user_id"),
                )
                continue
            await asyncio.to_thread(
                database.log_notification,
                recipient["user_id"],
                kind,
                text,
                now.strftime(DATETIME_FORMAT),
                f"work_type={work_type}",
            )
            sent += 1

    return sent
