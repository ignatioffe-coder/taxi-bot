"""Мероприятия и их влияние на спрос (ТЗ, п. 13).

Источник — локальный файл static/events.json, который заполняется вручную или
скриптом tools/add_event.py. Автоматический сбор афиш не подключён: публичные
афиши не дают стабильного открытого API, а парсинг чужих сайтов ненадёжен.

Влияние считается по расстоянию от площадки до центра района и спадает после
окончания события.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from external_factors import haversine_km

EVENTS_PATH = Path(__file__).resolve().parent / "static" / "events.json"

# Радиус влияния площадки, км
INFLUENCE_RADIUS_KM = 3.0
# За сколько часов до начала события начинаем учитывать
LEAD_HOURS = 4
# Сколько часов после окончания эффект ещё заметен
TAIL_HOURS = 2
# Насколько крупное событие может поднять балл района
MAX_BONUS = 0.20
# Посещаемость, при которой событие считаем максимально крупным
BIG_EVENT_ATTENDANCE = 20000


def load_events(path: Path | None = None) -> list[dict]:
    """Читает список событий. Битый файл не ломает прогноз."""
    source = path or EVENTS_PATH
    if not source.exists():
        return []
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    events = payload.get("events") if isinstance(payload, dict) else payload
    return [event for event in (events or []) if isinstance(event, dict)]


def _parse_moment(value) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _event_weight(event: dict, now: datetime) -> float:
    """0..1 — насколько событие актуально прямо сейчас."""
    start = _parse_moment(event.get("start"))
    if start is None:
        return 0.0
    end = _parse_moment(event.get("end")) or (start + timedelta(hours=3))

    if now < start - timedelta(hours=LEAD_HOURS):
        return 0.0
    if now > end + timedelta(hours=TAIL_HOURS):
        return 0.0

    if now < start:
        # Нарастание к началу: за 4 часа 0.25, к началу 1.0
        lead = (now - (start - timedelta(hours=LEAD_HOURS))).total_seconds()
        progress = lead / (LEAD_HOURS * 3600)
        timing = 0.25 + 0.75 * max(0.0, min(1.0, progress))
    elif now <= end:
        timing = 1.0
    else:
        tail = (now - end).total_seconds() / (TAIL_HOURS * 3600)
        timing = max(0.0, 1.0 - tail)

    try:
        attendance = float(event.get("attendance") or 0)
    except (TypeError, ValueError):
        attendance = 0.0
    size = min(1.0, attendance / BIG_EVENT_ATTENDANCE) if attendance > 0 else 0.3
    return round(timing * max(0.3, size), 4)


def event_impacts(events: list[dict], now: datetime, centers: dict[str, dict]) -> dict[str, float]:
    """Добавка к баллу районов рядом с площадками (0..MAX_BONUS)."""
    impacts: dict[str, float] = {}
    for event in events:
        weight = _event_weight(event, now)
        if weight <= 0:
            continue
        try:
            lat = float(event["lat"])
            lon = float(event["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        for district, point in centers.items():
            distance = haversine_km(lat, lon, point["lat"], point["lon"])
            if distance > INFLUENCE_RADIUS_KM:
                continue
            decay = 1.0 - distance / INFLUENCE_RADIUS_KM
            bonus = MAX_BONUS * weight * decay
            impacts[district] = max(impacts.get(district, 0.0), round(bonus, 4))
    return impacts


def load_event_impacts(now: datetime, centers: dict[str, dict]) -> dict[str, float]:
    events = load_events()
    if not events:
        return {}
    return event_impacts(events, now, centers)
