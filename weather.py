"""Погода в Москве.

Источник — wttr.in: бесплатный, без ключа и регистрации. Он отдаёт строку
вида «Ясно +11°C», а если проигнорирует параметр lang=ru — «Clear +11°C».

Раньше разбор этой строки был написан дважды: здесь и внутри bot.py.
В bot.py словарь перевода английских названий не срабатывал никогда
(из-за lang=ru приходит уже русский текст), а если бы сработал — потерялась бы
температура. Теперь разбор один: он всегда возвращает и состояние, и градусы,
и понимает оба языка.

Запуск отдельным файлом (python weather.py) требует заданного BOT_TOKEN —
его проверяет config, который нужен для московского времени.
"""

import re
import threading
import time

import requests

import config

WTTR_URL = "https://wttr.in/Moscow?format=%C+%t&lang=ru"
REQUEST_TIMEOUT = 6

# Кэш: wttr.in не любит частые запросы, а погода за 10 минут не меняется.
# Ошибку держим в кэше недолго, чтобы быстро попробовать снова.
CACHE_TTL = 600
ERROR_CACHE_TTL = 60

# Английские названия -> русские. Нужны, если сервис ответит не по-русски.
# Перебор идёт от длинных названий к коротким: иначе «Light rain» совпало бы
# с «rain» и превратилось бы просто в «Дождь» (в прежнем коде была эта ошибка).
_EN_CONDITIONS = {
    "light rain with thunderstorm": "⛈️ Гроза с дождём",
    "rain with thunderstorm": "⛈️ Гроза с дождём",
    "rain, thunderstorm": "⛈️ Гроза с дождём",
    "partly cloudy": "⛅ Переменная облачность",
    "heavy rain": "🌧️ Сильный дождь",
    "light rain": "🌦️ Небольшой дождь",
    "heavy snow": "❄️ Сильный снег",
    "light snow": "🌨️ Небольшой снег",
    "thunderstorm": "⛈️ Гроза",
    "overcast": "☁️ Пасмурно",
    "cloudy": "☁️ Облачно",
    "mist": "🌫️ Дымка",
    "fog": "🌫️ Туман",
    "rain": "🌧️ Дождь",
    "snow": "❄️ Снег",
    "clear": "☀️ Ясно",
    "sunny": "☀️ Солнечно",
}

# Эмодзи для русских названий: сервис отдаёт их без картинки.
_RU_EMOJI = {
    "переменная облачность": "⛅",
    "небольшой дождь": "🌦️",
    "небольшой снег": "🌨️",
    "ледяной дождь": "🌧️",
    "сильный дождь": "🌧️",
    "сильный снег": "❄️",
    "морось": "🌦️",
    "ливень": "🌧️",
    "пасмурно": "☁️",
    "облачно": "☁️",
    "солнечно": "☀️",
    "туман": "🌫️",
    "дымка": "🌫️",
    "гроза": "⛈️",
    "дождь": "🌧️",
    "снег": "❄️",
    "ясно": "☀️",
}

_TEMP_RE = re.compile(r"([+-]?\d+(?:[.,]\d+)?)\s*°?\s*([CF])?", re.IGNORECASE)

_lock = threading.Lock()
_cache = {"value": None, "at": 0.0, "ttl": CACHE_TTL}


def _describe(condition_raw: str) -> str:
    """Переводит состояние погоды на русский и добавляет эмодзи."""
    cleaned = condition_raw.strip()
    if not cleaned:
        return "🌡️ Погода"

    low = cleaned.lower()
    for key in sorted(_EN_CONDITIONS, key=len, reverse=True):
        if key in low:
            return _EN_CONDITIONS[key]

    # Сервис ответил по-русски — добавляем картинку, если состояние известно
    for key in sorted(_RU_EMOJI, key=len, reverse=True):
        if key in low:
            return f"{_RU_EMOJI[key]} {cleaned}"

    return cleaned


def parse_wttr_response(raw: str) -> dict:
    """Разбирает ответ wttr.in в понятный вид.

    «Ясно +11°C»  -> «☀️ Ясно, +11°C»
    «Clear +11°C» -> «☀️ Ясно, +11°C»   (температура больше не теряется)
    «Light rain +5°C» -> «🌦️ Небольшой дождь, +5°C»
    """
    text = (raw or "").strip().replace("−", "-").replace("–", "-")

    temp = None
    condition_raw = text
    match = _TEMP_RE.search(text)
    if match:
        unit = (match.group(2) or "C").upper()
        number = match.group(1).replace(",", ".")
        temp = f"{number}°{unit}"
        condition_raw = (text[:match.start()] + text[match.end():]).strip(" ,;·")

    condition = _describe(condition_raw)
    display = f"{condition}, {temp}" if temp else condition

    return {
        "text": display,
        "condition": condition,
        "temp": temp,
        "source": "wttr.in",
        "ok": True,
    }


def _fallback_by_time() -> dict:
    """Запасной вариант: только время суток, без выдуманных градусов."""
    hour = config.get_moscow_time().hour
    if 6 <= hour < 12:
        part, emoji = "Утро", "🌅"
    elif 12 <= hour < 18:
        part, emoji = "День", "☀️"
    elif 18 <= hour < 23:
        part, emoji = "Вечер", "🌙"
    else:
        part, emoji = "Ночь", "🌙"

    return {
        "text": f"{emoji} {part} · данные о погоде недоступны",
        "condition": f"{emoji} {part}",
        "temp": None,
        "source": "time",
        "ok": False,
    }


def _request_wttr() -> dict | None:
    try:
        response = requests.get(WTTR_URL, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            return None
        raw = response.text.strip()
        # Сервис умеет отвечать «Unknown location» и прочим мусором
        if not raw or "unknown" in raw.lower():
            return None
        return parse_wttr_response(raw)
    except Exception:
        return None


def get_weather(force: bool = False) -> dict:
    """Текущая погода в Москве.

    Возвращает словарь:
      text      — готовая строка для показа: «☀️ Ясно, +11°C»
      condition — только состояние (по нему бот подбирает совет)
      temp      — температура строкой или None
      source    — откуда данные: wttr.in или time
      ok        — удалось ли получить настоящие данные
    """
    with _lock:
        cached = _cache["value"]
        fresh = cached is not None and (time.monotonic() - _cache["at"]) < _cache["ttl"]
        if fresh and not force:
            return dict(cached)

    result = _request_wttr() or _fallback_by_time()

    with _lock:
        _cache["value"] = result
        _cache["at"] = time.monotonic()
        _cache["ttl"] = CACHE_TTL if result["ok"] else ERROR_CACHE_TTL

    return dict(result)


def get_weather_text(force: bool = False) -> str:
    """Готовая строка погоды — для сообщений и для записи в базу."""
    return get_weather(force=force)["text"]


if __name__ == "__main__":
    import json

    print(json.dumps(get_weather(force=True), ensure_ascii=False, indent=2))
