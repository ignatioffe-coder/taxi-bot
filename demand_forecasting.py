"""First-stage, data-limited demand signals for taxi and courier work."""

from collections import defaultdict
from datetime import datetime, timedelta

import config
from external_factors import poi_scores

TAXI_HISTORY_DAYS = 30
COURIER_HISTORY_DAYS = 90
COURIER_RECENT_DAYS = 45
COURIER_ORDER_MIN_SAMPLES = 3
COURIER_ORDER_MIN_DAYS = 2
TAXI_CURRENT_WINDOW = timedelta(hours=2)
TAXI_MIN_HISTORY_SAMPLES = 3

# Если личной истории мало, список дополняется стартовой оценкой по заведениям:
# одна-две смены не должны скрывать остальные районы.
BLEND_MIN_DISTRICTS = 5

# Насколько непогода усиливает спрос в баллах (ТЗ, п. 12: у такси и доставки
# влияние разное). Значение — доля, на которую умножается базовый балл.
WEATHER_IMPACT_TAXI = 0.12
WEATHER_IMPACT_COURIER = 0.10

# Горизонты прогноза из ТЗ, п. 6.4
HORIZONS = (30, 60, 120)

# Типичный профиль спроса по часам суток (0..23), нормирован относительно 1.0.
# Это начальная гипотеза для сравнения горизонтов: она не меняет текущий балл,
# а показывает, как он обычно смещается к указанному часу.
# Калибровать по факту наблюдений — ТЗ, п. 9.3.
TAXI_HOUR_PROFILE = (
    0.50, 0.40, 0.35, 0.30, 0.30, 0.35, 0.50, 0.85, 1.00, 0.95,
    0.85, 0.80, 0.85, 0.85, 0.80, 0.80, 0.90, 1.05, 1.00, 0.90,
    0.80, 0.75, 0.70, 0.60,
)
COURIER_HOUR_PROFILE = (
    0.20, 0.15, 0.10, 0.10, 0.15, 0.30, 0.50, 0.70, 0.80, 0.80,
    0.90, 1.00, 1.20, 1.15, 0.90, 0.80, 0.85, 1.00, 1.20, 1.15,
    1.00, 0.80, 0.50, 0.30,
)


def _hour_factor(moment: datetime, work_type: str) -> float:
    profile = TAXI_HOUR_PROFILE if work_type == "taxi" else COURIER_HOUR_PROFILE
    return profile[moment.hour % 24]


def _horizon_scores(score: int, now: datetime, work_type: str) -> dict[str, int]:
    """Балл на 30/60/120 минут вперёд.

    Балл `score` — это сигнал на ближайший час, поэтому горизонт 60 совпадает
    с ним, а 30 и 120 показывают ожидаемый сдвиг по типичному профилю суток.
    """
    base = _hour_factor(now, work_type)
    result: dict[str, int] = {}
    for minutes in HORIZONS:
        if minutes == 60:
            result["60"] = score
            continue
        moment = now + timedelta(minutes=minutes)
        ratio = _hour_factor(moment, work_type) / base if base else 1.0
        result[str(minutes)] = max(0, min(100, round(score * ratio)))
    return result



def _weather_multiplier(factors: dict, work_type: str) -> tuple[float, str | None]:
    """Множитель к баллу от погоды и подпись для заметки.

    Без данных о погоде возвращает 1.0 и None, поэтому оценка не меняется.
    """
    weather = factors.get("weather") or {}
    try:
        impact = float(weather.get("impact") or 0)
    except (TypeError, ValueError):
        return 1.0, None
    if impact <= 0:
        return 1.0, None
    share = WEATHER_IMPACT_TAXI if work_type == "taxi" else WEATHER_IMPACT_COURIER
    label = f"{weather.get('emoji', '')} {weather.get('text', '')}".strip()
    return 1.0 + share * impact, label or None


def _adjusted_score(score: int, multiplier: float) -> int:
    return max(0, min(100, round(score * multiplier)))


def _location_multiplier(factors: dict, district: str) -> float:
    """Добавка к баллу района от аэропортов и мероприятий (ТЗ, п. 7.3 и 13)."""
    airports_map = factors.get("airports") or {}
    events_map = factors.get("events") or {}
    try:
        bonus = float(airports_map.get(district, 0)) + float(events_map.get(district, 0))
    except (TypeError, ValueError):
        return 1.0
    if bonus <= 0:
        return 1.0
    return 1.0 + min(0.25, bonus)


def _reports_multiplier(factors: dict, district: str) -> float:
    """Множитель по сообщениям водителей и курьеров (ТЗ, п. 9.2, 10, 14)."""
    reports = factors.get("reports") or {}
    try:
        value = float(reports.get(district, 1.0))
    except (TypeError, ValueError):
        return 1.0
    return value if value > 0 else 1.0


def _traffic_multiplier(factors: dict, district: str) -> float:
    """Apply a small road-access penalty, not a direct demand measurement."""
    traffic = factors.get("traffic") or {}
    try:
        impact = float(traffic.get(district, 0))
    except (TypeError, ValueError):
        return 1.0
    return 1.0 - min(0.12, max(0.0, impact))


def _traffic_suffix(factors: dict) -> str:
    if not factors.get("traffic"):
        return ""
    return (
        " Дорожная обстановка TomTom слегка снижает оценку рядом с инцидентами; "
        "это не прямое измерение спроса."
    )


def _courier_item_signals(
    factors: dict, district: str, metric_type: str
) -> list[str]:
    if metric_type == "poi_baseline":
        signals = ["Плотность кафе и магазинов"]
    elif metric_type == "orders_per_hour":
        signals = ["Личные заказы/ч"]
    else:
        signals = ["Личный доход/ч"]

    _, weather_label = _weather_multiplier(factors, "courier")
    if weather_label:
        signals.append("Погодный спрос")

    for key, label in (
        ("events", "Событие рядом"),
        ("airports", "Аэропорт рядом"),
    ):
        try:
            if float((factors.get(key) or {}).get(district, 0)) > 0:
                signals.append(label)
        except (TypeError, ValueError):
            continue

    try:
        if float((factors.get("traffic") or {}).get(district, 0)) > 0:
            signals.append("Дорожные инциденты")
    except (TypeError, ValueError):
        pass

    try:
        reports = float((factors.get("reports") or {}).get(district, 1))
    except (TypeError, ValueError):
        reports = 1
    categories = set(
        ((factors.get("report_details") or {}).get(district) or {}).get(
            "report_categories"
        ) or []
    )
    if reports > 1.005:
        if "few_couriers" in categories:
            signals.append("Мало курьеров")
        elif "many_orders" in categories:
            signals.append("Много заказов")
        else:
            signals.append("Сообщения: спрос выше")
    elif reports < 0.995:
        if "many_couriers" in categories:
            signals.append("Высокая конкуренция")
        elif "long_wait" in categories:
            signals.append("Долгое ожидание")
        else:
            signals.append("Сообщения: есть сложности")

    return signals[:6]



def _parse_timestamp(value) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(config.MOSCOW_TZ).replace(tzinfo=None)
    return parsed


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _coefficient_score(value: float) -> int:
    return round(_clamp((value - 0.5) / 4.5 * 100, 0, 100))


def _taxi_forecast(inputs: dict, now: datetime, factors: dict) -> dict:
    current_since = now - TAXI_CURRENT_WINDOW
    history_since = now - timedelta(days=TAXI_HISTORY_DAYS)
    valid_districts = set(config.MOSCOW_DISTRICTS)
    multiplier, weather_label = _weather_multiplier(factors, "taxi")
    recent_by_district = defaultdict(list)
    history_by_district = defaultdict(list)

    for report in inputs.get("reports", []):
        district = report.get("district")
        timestamp = _parse_timestamp(report.get("timestamp"))
        try:
            coefficient = float(report.get("coefficient"))
        except (TypeError, ValueError):
            continue
        if (
            district not in valid_districts
            or timestamp is None
            or timestamp > now
            or timestamp < history_since
            or not 0.5 <= coefficient <= 5
        ):
            continue
        if timestamp >= current_since:
            recent_by_district[district].append(report)
        elif (
            timestamp.weekday() == now.weekday()
            and abs(timestamp.hour - now.hour) <= 1
        ):
            history_by_district[district].append(report)

    items = []
    for district in set(recent_by_district) | set(history_by_district):
        recent = recent_by_district[district]
        history = history_by_district[district]
        current_average = (
            sum(float(row["coefficient"]) for row in recent) / len(recent)
            if recent else None
        )
        history_average = (
            sum(float(row["coefficient"]) for row in history) / len(history)
            if history else None
        )
        if current_average is None and len(history) < TAXI_MIN_HISTORY_SAMPLES:
            continue

        current_score = (
            _coefficient_score(current_average) if current_average is not None else None
        )
        history_score = (
            _coefficient_score(history_average) if history_average is not None else None
        )
        if current_score is not None and history_score is not None:
            score = round(current_score * 0.65 + history_score * 0.35)
        else:
            score = current_score if current_score is not None else history_score
        base_score = score
        score = _adjusted_score(
            base_score,
            multiplier
            * _location_multiplier(factors, district)
            * _traffic_multiplier(factors, district)
            * _reports_multiplier(factors, district),
        )

        unique_reporters = len({row.get("user_id") for row in recent if row.get("user_id")})
        if recent:
            confidence = min(
                75,
                25 + min(unique_reporters, 3) * 12 + min(len(recent), 6) * 3,
            )
            if history:
                confidence = round(confidence * 0.75 + min(len(history), 10) * 1.25)
        else:
            confidence = min(45, 10 + len(history) * 3)

        difference = (
            current_average - history_average
            if current_average is not None and history_average is not None
            else None
        )
        trend = (
            "rising" if difference is not None and difference >= 0.15
            else "falling" if difference is not None and difference <= -0.15
            else "steady" if difference is not None
            else "unknown"
        )
        items.append({
            "district": district,
            "score": score,
            "score_base": base_score,
            "confidence": confidence,
            "trend": trend,
            "sample_count": len(recent) + len(history),
            "recent_samples": len(recent),
            "historical_samples": len(history),
            "current_coefficient": round(current_average, 2) if current_average is not None else None,
            "historical_coefficient": round(history_average, 2) if history_average is not None else None,
            "metric_label": "коэффициент",
            "horizons": _horizon_scores(score, now, "taxi"),
        })

    items.sort(key=lambda item: (item["score"], item["confidence"]), reverse=True)
    status = (
        "insufficient_data" if not items
        else "available" if sum(item["sample_count"] for item in items) >= 5
        else "limited"
    )
    weather_suffix = (
        f" Погода учтена: {weather_label}."
        if weather_label
        else " Погода сейчас недоступна, оценка без неё."
    )
    return {
        "work_type": "taxi",
        "title": "Сигнал спроса на такси на ближайший час",
        "horizon_minutes": 60,
        "status": status,
        "note": (
            "Оценка учитывает свежие коэффициенты (за 2 часа), похожий день и "
            "час за последние 30 дней, погоду и сообщения водителей о пробках и "
            "спросе. Балл 0–100 — сравнительный ориентир, а не число заказов. "
            "Пока не учитываются реальное число машин на линии и расстояние от "
            "вашей точки." + weather_suffix + _traffic_suffix(factors)
        ),
        "empty_message": (
            "Пока недостаточно замеров на это время. Добавляй коэффициенты, "
            "чтобы постепенно сформировать историю."
        ),
        "items": items[:7],
    }


def _courier_hints(inputs: dict) -> list[str]:
    """Почему часть смен не участвует в прогнозе — простым языком."""
    hints: list[str] = []
    excluded = inputs.get("excluded") or {}
    if excluded.get("no_district"):
        hints.append(
            f"{excluded['no_district']} смен(и) без района не учитываются — "
            "укажи район при сохранении."
        )
    if excluded.get("no_hours"):
        hints.append(
            f"{excluded['no_hours']} смен(и) без часов не учитываются — "
            "укажи длительность смены, иначе доход в час не посчитать."
        )
    other = inputs.get("other_direction_shifts") or 0
    if other:
        hints.append(
            f"Ещё {other} смен(и) записаны в другом направлении: такси и "
            "доставка считаются отдельно."
        )
    unknown_orders = inputs.get("orders_count_unknown")
    if unknown_orders is None:
        unknown_orders = (inputs.get("excluded") or {}).get("orders_count_unknown", 0)
    if unknown_orders:
        hints.append(
            f"В {unknown_orders} смен(ах) не указано число заказов; они не "
            "учитываются при расчёте заказов/ч. Укажи число заказов, включая 0."
        )
    return hints


def _baseline_items(
    factors: dict, now: datetime, skip: set[str] | None = None
) -> list[dict]:
    """Пункты стартовой оценки по плотности заведений (OpenStreetMap)."""
    density = factors.get("poi_density")
    if not density:
        # Снимок с диска специально не подхватывается автоматически:
        # без явно переданных данных поведение должно оставаться прежним.
        return []
    scores = poi_scores(density)
    if not scores:
        return []
    excluded = skip or set()
    multiplier, _ = _weather_multiplier(factors, "courier")
    items = []
    for district, index in scores.items():
        if district in excluded:
            continue
        base_score = round(index * 100)
        score = _adjusted_score(
            base_score,
            multiplier
            * _location_multiplier(factors, district)
            * _traffic_multiplier(factors, district)
            * _reports_multiplier(factors, district),
        )
        items.append({
            "district": district,
            "score": score,
            "score_base": base_score,
            "confidence": 25,
            "trend": "unknown",
            "sample_count": 0,
            "recent_samples": 0,
            "historical_samples": 0,
            "poi_index": index,
            "metric_label": "плотность заведений",
            "signals": _courier_item_signals(
                factors, district, "poi_baseline"
            ),
            "method": "poi_baseline",
            "horizons": _horizon_scores(score, now, "courier"),
        })
    items.sort(key=lambda item: (item["score"], item["confidence"]), reverse=True)
    return items


def _courier_poi_baseline(
    factors: dict, now: datetime, inputs: dict | None = None
) -> dict | None:
    """Стартовая оценка районов без личных смен — по плотности заведений.

    Решает холодный старт: у нового пользователя нет истории, но плотность
    кафе, магазинов и вузов (OpenStreetMap) уже известна.
    """
    if not factors.get("poi_density"):
        return None
    items = _baseline_items(factors, now)
    if not items:
        return None
    _, weather_label = _weather_multiplier(factors, "courier")
    weather_suffix = (
        f" Погода учтена: {weather_label}."
        if weather_label
        else " Погода сейчас недоступна."
    )
    return {
        "work_type": "courier",
        "title": "Районы по плотности заведений",
        "horizon_minutes": 60,
        "status": "baseline",
        "method": "poi_baseline",
        "note": (
            "Стартовая оценка по плотности кафе, магазинов и вузов "
            "(OpenStreetMap): кафе и рестораны — основной источник заказов "
            "доставки. Это ориентир без ваших смен; чем больше смен с районом "
            "ты сохранишь, тем точнее станет персональная оценка. Учтены погода "
            "и сообщения курьеров." + weather_suffix + _traffic_suffix(factors)
        ),
        "empty_message": "Нет данных о плотности заведений.",
        "hints": _courier_hints(inputs or {}),
        "items": items[:7],
    }


def _courier_forecast(inputs: dict, now: datetime, factors: dict) -> dict:
    multiplier, weather_label = _weather_multiplier(factors, "courier")
    valid_districts = set(config.MOSCOW_DISTRICTS)
    recent_since = now - timedelta(days=COURIER_RECENT_DAYS)
    history_since = now - timedelta(days=COURIER_HISTORY_DAYS)
    target_time = now + timedelta(minutes=60)
    all_shifts = inputs.get("shifts", [])
    selected_vehicle = inputs.get("vehicle_type") or ""
    matching_shifts = [
        shift for shift in all_shifts
        if shift.get("vehicle_type") == selected_vehicle and selected_vehicle
    ]
    legacy_shifts = [
        shift for shift in all_shifts if not shift.get("vehicle_type")
    ]
    if selected_vehicle and matching_shifts:
        shifts = matching_shifts
        transport_basis = "matched"
    elif selected_vehicle and legacy_shifts:
        shifts = legacy_shifts
        transport_basis = "unspecified"
    elif selected_vehicle:
        shifts = []
        transport_basis = "unavailable"
    else:
        shifts = all_shifts
        transport_basis = "all"

    def period_totals():
        return {
            "income": 0.0,
            "hours": 0.0,
            "shifts": 0,
            "days": set(),
            "orders": 0,
            "order_hours": 0.0,
            "order_shifts": 0,
            "order_days": set(),
        }

    def district_totals():
        return {
            "income": 0.0,
            "hours": 0.0,
            "shifts": 0,
            "days": set(),
            "orders": 0,
            "order_hours": 0.0,
            "order_shifts": 0,
            "order_days": set(),
            "recent": period_totals(),
            "historical": period_totals(),
        }

    totals_by_district = defaultdict(district_totals)
    timed_totals_by_district = defaultdict(district_totals)

    for shift in shifts:
        district = shift.get("district")
        try:
            hours = float(shift.get("hours_worked") or 0)
            income = (
                float(shift.get("gross_income") or 0)
                + float(shift.get("tips") or 0)
                - float(shift.get("fuel_cost") or 0)
                - float(shift.get("commission_cost") or 0)
                - float(shift.get("maintenance_cost") or 0)
                - float(shift.get("other_cost") or 0)
                - float(shift.get("rental_cost") or 0)
                - float(shift.get("insurance_cost") or 0)
                - float(shift.get("tax_cost") or 0)
                - float(shift.get("charging_cost") or 0)
                - float(shift.get("connectivity_cost") or 0)
            )
        except (TypeError, ValueError):
            continue
        start_time = _parse_timestamp(shift.get("started_at"))
        timestamp = start_time or _parse_timestamp(shift.get("timestamp"))
        if (
            district not in valid_districts
            or hours <= 0
            or timestamp is None
            or timestamp > now
            or timestamp < history_since
        ):
            continue
        total = totals_by_district[district]
        total["income"] += income
        total["hours"] += hours
        total["shifts"] += 1
        total["days"].add(timestamp.date())
        period = total["recent"] if timestamp >= recent_since else total["historical"]
        period["income"] += income
        period["hours"] += hours
        period["shifts"] += 1
        period["days"].add(timestamp.date())

        has_orders_field = (
            "orders_count" in shift and shift["orders_count"] is not None
        )
        orders_count_known = shift.get("orders_count_known")
        if orders_count_known is None:
            orders_count_known = has_orders_field
        try:
            orders_count = int(shift.get("orders_count") or 0)
        except (TypeError, ValueError):
            orders_count = -1
        if orders_count_known and orders_count >= 0:
            for aggregate in (total, period):
                aggregate["orders"] += orders_count
                aggregate["order_hours"] += hours
                aggregate["order_shifts"] += 1
                aggregate["order_days"].add(timestamp.date())

        if start_time is not None:
            hour_difference = abs(start_time.hour - target_time.hour)
            hour_difference = min(hour_difference, 24 - hour_difference)
            if (
                start_time.weekday() == target_time.weekday()
                and hour_difference <= 1
            ):
                timed = timed_totals_by_district[district]
                timed["income"] += income
                timed["hours"] += hours
                timed["shifts"] += 1
                timed["days"].add(start_time.date())
                if orders_count_known and orders_count >= 0:
                    timed["orders"] += orders_count
                    timed["order_hours"] += hours
                    timed["order_shifts"] += 1
                    timed["order_days"].add(start_time.date())

    timed_sample_count = sum(
        total["shifts"] for total in timed_totals_by_district.values()
    )
    timed_day_count = len({
        day
        for total in timed_totals_by_district.values()
        for day in total["days"]
    })
    income_time_match_supported = timed_sample_count >= 3 and timed_day_count >= 2

    def order_rates(source):
        return {
            district: total["orders"] / total["order_hours"]
            for district, total in source.items()
            if total["order_hours"] > 0
        }

    all_order_rates = order_rates(totals_by_district)
    order_sample_count = sum(
        total["order_shifts"] for total in totals_by_district.values()
    )
    order_day_count = len({
        day
        for total in totals_by_district.values()
        for day in total["order_days"]
    })
    order_data_supported = (
        order_sample_count >= COURIER_ORDER_MIN_SAMPLES
        and order_day_count >= COURIER_ORDER_MIN_DAYS
        and len(all_order_rates) >= 2
    )
    timed_order_rates = order_rates(timed_totals_by_district)
    timed_order_sample_count = sum(
        total["order_shifts"] for total in timed_totals_by_district.values()
    )
    timed_order_day_count = len({
        day
        for total in timed_totals_by_district.values()
        for day in total["order_days"]
    })
    order_time_match_supported = (
        timed_order_sample_count >= COURIER_ORDER_MIN_SAMPLES
        and timed_order_day_count >= COURIER_ORDER_MIN_DAYS
        and len(timed_order_rates) >= 2
    )
    use_order_rate = order_data_supported
    time_match_supported = (
        order_time_match_supported
        if use_order_rate else income_time_match_supported
    )
    ranking_totals = (
        timed_totals_by_district if time_match_supported else totals_by_district
    )
    rates = (
        order_rates(ranking_totals)
        if use_order_rate
        else {
            district: total["income"] / total["hours"]
            for district, total in ranking_totals.items()
            if total["hours"] > 0
        }
    )
    if not rates:
        baseline = _courier_poi_baseline(factors, now, inputs)
        if baseline is not None:
            baseline["horizon_supported"] = False
            baseline["transport_basis"] = transport_basis
            baseline["transport_type"] = selected_vehicle or None
            if transport_basis == "unavailable":
                baseline["note"] += " В истории нет смен на выбранном транспорте."
            return baseline
        return {
            "work_type": "courier",
            "title": "Районы по вашей истории смен",
            "horizon_minutes": None,
            "horizon_supported": False,
            "transport_basis": transport_basis,
            "transport_type": selected_vehicle or None,
            "status": "insufficient_data",
            "note": (
                "Для персональной оценки нужны сохранённые курьерские смены "
                "с районом, транспортом и количеством отработанных часов."
            ),
            "empty_message": (
                "Пока нет подходящих смен. Указывай район и часы при сохранении "
                "курьерской смены."
            ),
            "hints": _courier_hints(inputs),
            "items": [],
        }

    comparison_rates = {
        district: (
            (
                totals_by_district[district]["recent"]["orders"]
                / totals_by_district[district]["recent"]["order_hours"]
                if use_order_rate else
                totals_by_district[district]["recent"]["income"]
                / totals_by_district[district]["recent"]["hours"]
            )
            if not time_match_supported
            and (
                totals_by_district[district]["recent"]["order_hours"] > 0
                if use_order_rate else
                totals_by_district[district]["recent"]["hours"] > 0
            )
            else rates[district]
        )
        for district in ranking_totals
        if district in rates
    }
    minimum_rate = min(comparison_rates.values())
    maximum_rate = max(comparison_rates.values())
    items = []
    for district, rate in comparison_rates.items():
        total = ranking_totals[district]
        trend_total = totals_by_district[district]
        recent_total = trend_total["recent"]
        historical_total = trend_total["historical"]
        if use_order_rate:
            recent_rate = (
                recent_total["orders"] / recent_total["order_hours"]
                if recent_total["order_hours"] > 0 else None
            )
            historical_rate = (
                historical_total["orders"] / historical_total["order_hours"]
                if historical_total["order_hours"] > 0 else None
            )
            sample_count = total["order_shifts"]
            sample_days = total["order_days"]
            recent_samples = recent_total["order_shifts"]
            historical_samples = historical_total["order_shifts"]
        else:
            recent_rate = (
                recent_total["income"] / recent_total["hours"]
                if recent_total["hours"] > 0 else None
            )
            historical_rate = (
                historical_total["income"] / historical_total["hours"]
                if historical_total["hours"] > 0 else None
            )
            sample_count = total["shifts"]
            sample_days = total["days"]
            recent_samples = recent_total["shifts"]
            historical_samples = historical_total["shifts"]
        recent_metric_has_data = (
            recent_total["order_hours"] > 0
            if use_order_rate else recent_total["hours"] > 0
        )
        rate_period = (
            "similar_time"
            if time_match_supported
            else "recent_45"
            if recent_metric_has_data
            else "history_90"
        )
        change = (
            recent_rate - historical_rate
            if recent_rate is not None and historical_rate is not None
            else None
        )
        threshold = (
            max(abs(historical_rate) * 0.1, 0.1 if use_order_rate else 25)
            if historical_rate is not None else None
        )
        trend = (
            "rising" if change is not None and change >= threshold
            else "falling" if change is not None and change <= -threshold
            else "steady" if change is not None
            else "unknown"
        )
        base_score = (
            50 if maximum_rate == minimum_rate
            else round((rate - minimum_rate) / (maximum_rate - minimum_rate) * 100)
        )
        score = _adjusted_score(
            base_score,
            multiplier
            * _location_multiplier(factors, district)
            * _traffic_multiplier(factors, district)
            * _reports_multiplier(factors, district),
        )
        net_income_per_hour = (
            rate
            if not use_order_rate
            else total["income"] / total["hours"]
            if total["hours"] > 0
            else None
        )
        orders_per_hour = (
            rate
            if use_order_rate
            else total["orders"] / total["order_hours"]
            if total["order_hours"] > 0
            else None
        )
        confidence = min(
            95,
            20 + min(sample_count, 4) * 15 + min(len(sample_days), 5) * 5,
        )
        items.append({
            "district": district,
            "score": score,
            "score_base": base_score,
            "confidence": confidence,
            "trend": trend,
            "sample_count": sample_count,
            "recent_samples": recent_samples,
            "historical_samples": historical_samples,
            "metric_type": (
                "orders_per_hour" if use_order_rate else "net_income_per_hour"
            ),
            "signals": _courier_item_signals(
                factors,
                district,
                "orders_per_hour" if use_order_rate else "net_income_per_hour",
            ),
            "rate_period": rate_period,
            "metric_label": "заказов/ч" if use_order_rate else "чистый доход/ч",
            "orders_per_hour": (
                round(orders_per_hour, 2) if orders_per_hour is not None else None
            ),
            "recent_orders_per_hour": (
                round(recent_rate, 2)
                if use_order_rate and recent_rate is not None else None
            ),
            "historical_orders_per_hour": (
                round(historical_rate, 2)
                if use_order_rate and historical_rate is not None else None
            ),
            "net_income_per_hour": (
                round(net_income_per_hour, 2)
                if net_income_per_hour is not None else None
            ),
            "historical_net_income_per_hour": (
                round(
                    historical_total["income"] / historical_total["hours"], 2
                )
                if historical_total["hours"] > 0 else None
            ),
            "horizons": _horizon_scores(score, now, "courier"),
        })

    # Одна-две смены не должны скрывать остальные районы: дополняем список
    # стартовой оценкой по плотности заведений (ТЗ, п. 8.2).
    if len(items) < BLEND_MIN_DISTRICTS:
        known = {item["district"] for item in items}
        items.extend(_baseline_items(factors, now, skip=known))
    items.sort(
        key=lambda item: (
            item.get("method") == "poi_baseline",
            -item["score"],
            -item["confidence"],
        )
    )
    total_shifts = sum(item["sample_count"] for item in items)
    blend_suffix = (
        " Районы без ваших смен отмечены как стартовая оценка по заведениям."
        if any(item.get("method") == "poi_baseline" for item in items)
        else ""
    )
    transport_name = {
        "foot": "пешком",
        "bicycle": "велосипед",
        "scooter": "самокат",
        "motorcycle": "мотоцикл",
        "car": "автомобиль",
    }.get(selected_vehicle, selected_vehicle)
    transport_suffix = (
        f" Учтены смены на транспорте «{transport_name}»."
        if transport_basis == "matched" and transport_name
        else " В части истории транспорт не указан, поэтому эти смены нельзя разделить по типу."
        if transport_basis == "unspecified"
        else " В истории нет смен на выбранном транспорте."
        if transport_basis == "unavailable"
        else ""
    )
    time_suffix = (
        " Для рейтинга учтены смены, начатые в похожие день недели и час."
        if time_match_supported
        else " Для сравнения времени начала смен пока недостаточно наблюдений; "
        "использована вся доступная история."
    )
    weather_suffix = (
        f" Погода учтена: {weather_label}."
        if weather_label
        else " Погода сейчас недоступна."
    )
    metric_note = (
        "При достаточной истории рейтинг основан на заказах в час; чистый "
        "доход в час показан отдельно."
        if use_order_rate
        else "Пока нет достаточной истории с указанным числом заказов; рейтинг "
        "временно основан на чистом доходе в час. Указывай число заказов в смене."
    )
    blended = any(item.get("method") == "poi_baseline" for item in items)
    return {
        "work_type": "courier",
        "title": (
            "Ваши районы и стартовая оценка"
            if blended
            else "Районы по вашей истории смен"
        ),
        "horizon_minutes": None,
        "horizon_supported": time_match_supported,
        "transport_basis": transport_basis,
        "transport_type": selected_vehicle or None,
        "status": "available" if total_shifts >= 3 else "limited",
        "note": (
            f"Балл сравнивает районы по данным смен за последние 90 дней. {metric_note} "
            "Динамика сравнивает последние 45 дней с предыдущими 45. Учтены погода "
            "и сообщения курьеров об обстановке."
            + transport_suffix + time_suffix + weather_suffix + blend_suffix
            + _traffic_suffix(factors)
        ),
        "empty_message": "Нет сохранённых смен с районом и часами работы.",
        "hints": _courier_hints(inputs),
        "items": items[:7],
    }


def build_forecast(
    work_type: str,
    inputs: dict,
    now: datetime | None = None,
    factors: dict | None = None,
) -> dict:
    """Build a transparent, first-stage forecast from available app data.

    `factors` — внешние данные (погода, плотность заведений). Если их нет,
    оценка считается ровно как раньше: без погоды и без стартовой базы.
    """
    if work_type not in {"taxi", "courier"}:
        raise ValueError("Unsupported work type")
    generated_at = now or config.get_moscow_time()
    factors = factors or {}
    result = (
        _taxi_forecast(inputs, generated_at, factors)
        if work_type == "taxi"
        else _courier_forecast(inputs, generated_at, factors)
    )
    result["generated_at"] = generated_at.isoformat(timespec="minutes")
    result["weather"] = factors.get("weather")
    result["horizons_minutes"] = list(HORIZONS)
    result["factors"] = {
        "weather": bool(factors.get("weather")),
        "poi_density": bool(factors.get("poi_density")),
        "airports": bool(factors.get("airports")),
        "events": bool(factors.get("events")),
        "traffic": bool(factors.get("traffic")),
        "reports": bool(factors.get("reports")),
    }
    return result
