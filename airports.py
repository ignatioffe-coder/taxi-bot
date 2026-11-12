"""Аэропорты и вокзалы (ТЗ, п. 7.3).

Влияние прилётов на спрос рядом с аэропортом считается отдельно от общего
прогноза: прилёты дают всплеск только в тех районах, что рядом.

Важно: само расписание рейсов ещё не значит «есть заказы» — в ТЗ это прямо
оговорено. Поэтому сигнал слабый (максимум +15% к баллу) и только когда рейсов
заметно больше обычного. Если источник расписаний не настроен, слой молчит и
прогноз считается без него.
"""

import json
import os

from external_factors import haversine_km

# Координаты и коды аэропортов Московского авиаузла
AIRPORTS = {
    "SVO": {"name": "Шереметьево", "lat": 55.9726, "lon": 37.4146},
    "DME": {"name": "Домодедово", "lat": 55.4088, "lon": 37.9063},
    "VKO": {"name": "Внуково", "lat": 55.5915, "lon": 37.2615},
}

# Сколько прилётов за час считаем «обычным» фоном и «пиком»
BASELINE_ARRIVALS_PER_HOUR = 8
PEAK_ARRIVALS_PER_HOUR = 20

# Радиус влияния аэропорта на районы, км
INFLUENCE_RADIUS_KM = 14.0

# Максимальная добавка к баллу района от аэропортов
MAX_BONUS = 0.15

# Шаблон URL источника расписаний. Должен поддерживать {iata} и вернуть JSON
# со списком прилётов; формат нормализуется в _normalize_arrivals.
FLIGHTS_API_URL = os.getenv("FLIGHTS_API_URL", "").strip()


def _normalize_arrivals(raw: list) -> list[dict]:
    """Приводит ответ источника к списку {"airport", "time"}."""
    normalized = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        airport = str(item.get("airport") or item.get("iata") or "").upper()
        moment = item.get("time") or item.get("scheduled") or item.get("arrival")
        if airport in AIRPORTS and moment:
            normalized.append({"airport": airport, "time": str(moment)})
    return normalized


def fetch_arrivals() -> list[dict]:
    """Загружает прилёты, если настроен источник. Иначе пустой список."""
    if not FLIGHTS_API_URL:
        return []
    try:
        import requests

        response = requests.get(FLIGHTS_API_URL, timeout=8)
        response.raise_for_status()
        payload = response.json()
    except Exception:  # noqa: BLE001 - расписание не должно ронять прогноз
        return []
    if isinstance(payload, dict):
        payload = payload.get("arrivals") or payload.get("data") or []
    return _normalize_arrivals(payload if isinstance(payload, list) else [])


def airport_pressure(arrivals: list[dict], now, hours: int = 1) -> dict[str, float]:
    """Насколько прилёты в ближайший час выше фона: 0..1 по каждому аэропорту."""
    from datetime import datetime, timedelta

    window_end = now + timedelta(hours=hours)
    counts: dict[str, int] = {code: 0 for code in AIRPORTS}
    for arrival in arrivals:
        code = arrival.get("airport")
        if code not in AIRPORTS:
            continue
        raw = arrival.get("time")
        try:
            moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if moment.tzinfo is not None:
            moment = moment.astimezone(now.tzinfo).replace(tzinfo=None) if now.tzinfo else moment.replace(tzinfo=None)
        if now <= moment <= window_end:
            counts[code] += 1

    span = PEAK_ARRIVALS_PER_HOUR - BASELINE_ARRIVALS_PER_HOUR
    pressure = {}
    for code, count in counts.items():
        if count <= BASELINE_ARRIVALS_PER_HOUR:
            pressure[code] = 0.0
        else:
            pressure[code] = min(1.0, (count - BASELINE_ARRIVALS_PER_HOUR) / span)
    return pressure


def district_impacts(pressure: dict[str, float], centers: dict[str, dict]) -> dict[str, float]:
    """Добавка к баллу по районам рядом с аэропортами (0..MAX_BONUS)."""
    impacts: dict[str, float] = {}
    for code, value in pressure.items():
        if value <= 0:
            continue
        airport = AIRPORTS.get(code)
        if not airport:
            continue
        for district, point in centers.items():
            distance = haversine_km(
                airport["lat"], airport["lon"], point["lat"], point["lon"]
            )
            if distance > INFLUENCE_RADIUS_KM:
                continue
            decay = 1.0 - distance / INFLUENCE_RADIUS_KM
            bonus = MAX_BONUS * value * decay
            impacts[district] = max(impacts.get(district, 0.0), round(bonus, 4))
    return impacts


def load_airport_impacts(now, centers: dict[str, dict]) -> dict[str, float]:
    """Готовые добавки по районам. Пустой словарь, если данных нет."""
    arrivals = fetch_arrivals()
    if not arrivals:
        return {}
    return district_impacts(airport_pressure(arrivals, now), centers)


def load_centers(path: str | None = None) -> dict[str, dict]:
    """Центры районов из снимка OSM-данных."""
    from pathlib import Path

    source = Path(path) if path else Path(__file__).resolve().parent / "static" / "district-centers.json"
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return payload.get("districts") or {}
