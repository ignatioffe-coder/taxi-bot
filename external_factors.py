"""Внешние факторы спроса: погода и плотность заведений.

Слой отделён от движка прогноза намеренно: `demand_forecasting` остаётся
чистой функцией, а эти данные подаются снаружи. Так прогноз можно тестировать
без сети, а при недоступности источника — работать без него.

Источники:
  * Open-Meteo — погода, бесплатно и без ключа (ТЗ, п. 12);
  * OpenStreetMap — плотность заведений по районам, снимок в
    static/district-poi.json (ТЗ, п. 8.2), строится tools/build_poi_density.py.
"""

import json
import math
import threading
import time
from pathlib import Path

import requests

import config

ROOT = Path(__file__).resolve().parent
POI_PATH = ROOT / "static" / "district-poi.json"

MOSCOW_LAT = 55.7558
MOSCOW_LON = 37.6173

# Первый этап — Москва (ТЗ, п. 2.3). Если в конфиге появится TZ, возьмём его.
TIMEZONE = getattr(config, "TZ", "Europe/Moscow")

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_TIMEOUT = 8
WEATHER_CACHE_TTL = 600

# Коды WMO -> (описание, эмодзи, насколько усиливает спрос 0..1)
WMO = {
    0: ("Ясно", "☀️", 0.0),
    1: ("Преимущественно ясно", "🌤️", 0.0),
    2: ("Переменная облачность", "⛅", 0.0),
    3: ("Пасмурно", "☁️", 0.1),
    45: ("Туман", "🌫️", 0.2),
    48: ("Изморозь", "🌫️", 0.2),
    51: ("Слабая морось", "🌦️", 0.3),
    53: ("Морось", "🌦️", 0.4),
    55: ("Сильная морось", "🌧️", 0.5),
    56: ("Ледяная морось", "🌧️", 0.5),
    57: ("Сильная ледяная морось", "🌧️", 0.6),
    61: ("Небольшой дождь", "🌦️", 0.4),
    63: ("Дождь", "🌧️", 0.6),
    65: ("Сильный дождь", "🌧️", 0.8),
    66: ("Ледяной дождь", "🌧️", 0.7),
    67: ("Сильный ледяной дождь", "🌧️", 0.9),
    71: ("Небольшой снег", "🌨️", 0.5),
    73: ("Снег", "🌨️", 0.7),
    75: ("Сильный снег", "❄️", 0.9),
    77: ("Снежная крупа", "🌨️", 0.6),
    80: ("Небольшой ливень", "🌦️", 0.5),
    81: ("Ливень", "🌧️", 0.7),
    82: ("Сильный ливень", "⛈️", 0.9),
    85: ("Небольшой снегопад", "🌨️", 0.6),
    86: ("Сильный снегопад", "❄️", 0.9),
    95: ("Гроза", "⛈️", 0.9),
    96: ("Гроза с градом", "⛈️", 1.0),
    99: ("Сильная гроза с градом", "⛈️", 1.0),
}

_lock = threading.Lock()
_weather_cache: dict = {"value": None, "at": 0.0}
_poi_cache: dict = {"value": None}


def describe_weather(code: int | None) -> tuple[str, str, float]:
    """Возвращает (описание, эмодзи, коэффициент влияния на спрос)."""
    if code is None:
        return ("нет данных", "", 0.0)
    return WMO.get(int(code), ("Неизвестно", "🌡️", 0.1))


def get_weather(force: bool = False) -> dict | None:
    """Текущая погода в Москве. None, если источник недоступен."""
    with _lock:
        cached = _weather_cache["value"]
        if cached and not force and time.monotonic() - _weather_cache["at"] < WEATHER_CACHE_TTL:
            return dict(cached)

    try:
        response = requests.get(
            WEATHER_URL,
            params={
                "latitude": MOSCOW_LAT,
                "longitude": MOSCOW_LON,
                "current": "temperature_2m,precipitation,weather_code,wind_speed_10m",
                "timezone": TIMEZONE,
            },
            timeout=WEATHER_TIMEOUT,
        )
        response.raise_for_status()
        current = response.json().get("current") or {}
        code = current.get("weather_code")
        text, emoji, impact = describe_weather(code)
        value = {
            "temperature": current.get("temperature_2m"),
            "precipitation": current.get("precipitation"),
            "wind": current.get("wind_speed_10m"),
            "code": code,
            "text": text,
            "emoji": emoji,
            "impact": impact,
            "source": "open-meteo",
        }
    except Exception:  # noqa: BLE001 - погода не должна ронять прогноз
        return None

    with _lock:
        _weather_cache["value"] = value
        _weather_cache["at"] = time.monotonic()
    return dict(value)


def load_poi_density() -> dict:
    """Плотность заведений по районам из локального снимка OSM."""
    with _lock:
        if _poi_cache["value"] is not None:
            return _poi_cache["value"]
    if not POI_PATH.exists():
        with _lock:
            _poi_cache["value"] = {}
        return {}
    try:
        payload = json.loads(POI_PATH.read_text(encoding="utf-8"))
        districts = payload.get("districts") or {}
    except Exception:  # noqa: BLE001 - битый снимок не должен ломать прогноз
        districts = {}
    with _lock:
        _poi_cache["value"] = districts
    return districts


POI_WEIGHTS = {"food": 1.0, "shop": 0.6, "education": 0.4, "service": 0.2}


def poi_scores(density: dict | None = None) -> dict[str, float]:
    """Нормированная 0..1 насыщенность районов заведениями.

    Считается один раз на словарь: ранг района относительно остальных,
    чтобы один аномальный район не сжимал шкалу для всех.
    """
    rows = density if density is not None else load_poi_density()
    if not rows:
        return {}
    raw = {
        name: sum(values.get(category, 0) * weight for category, weight in POI_WEIGHTS.items())
        for name, values in rows.items()
    }
    ordered = sorted(value for value in raw.values() if value > 0)
    if not ordered:
        return {}
    import bisect

    return {
        name: round(bisect.bisect_right(ordered, value) / len(ordered), 3)
        for name, value in raw.items()
        if value > 0
    }


def poi_index(district: str, density: dict | None = None) -> float | None:
    """0..1 — насколько район насыщен точками питания, магазинами и вузами."""
    return poi_scores(density).get(district)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между двумя точками в километрах."""
    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))


def load_live_factors(now=None) -> dict:
    """Факторы для передачи в demand_forecasting.build_forecast."""
    moment = now or config.get_moscow_time()
    airport_impacts: dict = {}
    event_impacts: dict = {}
    try:
        # Импорт внутри функции: airports и events сами используют этот модуль
        import airports
        import events

        centers = airports.load_centers()
        if centers:
            airport_impacts = airports.load_airport_impacts(moment, centers)
            event_impacts = events.load_event_impacts(moment, centers)
    except Exception:  # noqa: BLE001 - слой факторов не должен ронять прогноз
        airport_impacts, event_impacts = {}, {}

    return {
        "weather": get_weather(),
        "poi_density": load_poi_density(),
        "airports": airport_impacts,
        "events": event_impacts,
    }
