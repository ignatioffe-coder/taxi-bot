"""Сообщения водителей как фактор прогноза (ТЗ, п. 9.2, 10, 14).

Водители и курьеры присылают наблюдения: пробки, ДТП, перекрытия, высокий
спрос, мало машин. До этого слоя они только показывались в ленте, но на балл
не влияли — здесь они превращаются в множитель к оценке района.

Правила осторожные:
  * одно сообщение не может сильно сдвинуть район;
  * вес растёт с числом разных авторов (подтверждение);
  * старые сообщения затухают, потому что обстановка меняется;
  * суммарный эффект ограничен, чтобы шум не ломал прогноз.
"""

import math
from datetime import datetime

# Насколько каждая категория двигает балл (знак и сила).
# Значения заданы в единицах эффекта, не в процентах.
CATEGORY_EFFECT = {
    # спрос выше обычного
    "high_demand": 0.070,
    "many_orders": 0.055,
    "few_cars": 0.045,
    # спрос ниже обычного
    "low_demand": -0.055,
    "low_activity": -0.050,
    "long_wait": -0.030,
    # дорожная обстановка (ТЗ, п. 14)
    "traffic": -0.060,
    "accident": -0.080,
    "road_closure": -0.090,
    # проблемы на точке
    "restaurant_busy": -0.030,
    "order_problem": -0.030,
    "delivery_problem": -0.030,
}

# Предел влияния сообщений на район
MAX_EFFECT = 0.12

# Через сколько минут сообщение теряет половину веса
HALF_LIFE_MINUTES = 90

# Сколько авторов считаем полным подтверждением
FULL_CONFIRMATION_REPORTERS = 2


def _parse(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def freshness(last_update, now: datetime, half_life_minutes: float = HALF_LIFE_MINUTES) -> float:
    """1.0 для свежего сообщения, 0.5 через half_life, дальше к нулю."""
    moment = _parse(last_update)
    if moment is None:
        return 0.0
    age_minutes = max(0.0, (now - moment).total_seconds() / 60.0)
    return math.pow(0.5, age_minutes / half_life_minutes)


def district_multipliers(
    stats: dict, now: datetime, max_effect: float = MAX_EFFECT
) -> dict[str, float]:
    """Множитель к баллу района по сообщениям водителей.

    Возвращает только районы, где эффект заметен: {район: 1.04} и т.п.
    """
    multipliers: dict[str, float] = {}
    for district, row in (stats or {}).items():
        categories = row.get("report_categories") or []
        effect = sum(CATEGORY_EFFECT.get(category, 0.0) for category in categories)
        if not effect:
            continue
        reporters = int(row.get("reporters") or 0)
        confirmation = min(1.0, max(1, reporters) / FULL_CONFIRMATION_REPORTERS)
        age_factor = freshness(row.get("last_report_update"), now)
        if age_factor <= 0:
            continue
        total = effect * confirmation * age_factor
        total = max(-max_effect, min(max_effect, total))
        if abs(total) < 0.005:
            continue
        multipliers[district] = round(1.0 + total, 4)
    return multipliers


def describe(multiplier: float) -> str:
    """Человеческая подпись к множителю."""
    if multiplier >= 1.005:
        return "сообщения водителей: спрос выше обычного"
    if multiplier <= 0.995:
        return "сообщения водителей: обстановка хуже обычной"
    return "сообщения водителей учтены"
