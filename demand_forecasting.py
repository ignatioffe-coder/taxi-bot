"""First-stage, data-limited demand signals for taxi and courier work."""

from collections import defaultdict
from datetime import datetime, timedelta

import config

TAXI_HISTORY_DAYS = 30
COURIER_HISTORY_DAYS = 90
TAXI_CURRENT_WINDOW = timedelta(hours=2)
TAXI_MIN_HISTORY_SAMPLES = 3


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


def _taxi_forecast(inputs: dict, now: datetime) -> dict:
    current_since = now - TAXI_CURRENT_WINDOW
    history_since = now - timedelta(days=TAXI_HISTORY_DAYS)
    valid_districts = set(config.MOSCOW_DISTRICTS)
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
            "confidence": confidence,
            "trend": trend,
            "sample_count": len(recent) + len(history),
            "recent_samples": len(recent),
            "historical_samples": len(history),
            "current_coefficient": round(current_average, 2) if current_average is not None else None,
            "historical_coefficient": round(history_average, 2) if history_average is not None else None,
            "metric_label": "коэффициент",
        })

    items.sort(key=lambda item: (item["score"], item["confidence"]), reverse=True)
    status = (
        "insufficient_data" if not items
        else "available" if sum(item["sample_count"] for item in items) >= 5
        else "limited"
    )
    return {
        "work_type": "taxi",
        "title": "Сигнал спроса на такси на ближайший час",
        "horizon_minutes": 60,
        "status": status,
        "note": (
            "Оценка построена по свежим сообщениям о коэффициентах и похожим "
            "дню недели и часу за последние 30 дней. Балл 0–100 нормирован "
            "по коэффициенту и не является числом заказов: "
            "данные о машинах-конкурентах, пробках, мероприятиях и расстоянии "
            "от водителя пока не подключены."
        ),
        "empty_message": (
            "Пока недостаточно замеров на это время. Добавляй коэффициенты, "
            "чтобы постепенно сформировать историю."
        ),
        "items": items[:7],
    }


def _courier_forecast(inputs: dict) -> dict:
    valid_districts = set(config.MOSCOW_DISTRICTS)
    district_totals = defaultdict(lambda: {"income": 0.0, "hours": 0.0, "shifts": 0, "days": set()})

    for shift in inputs.get("shifts", []):
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
            )
        except (TypeError, ValueError):
            continue
        timestamp = _parse_timestamp(shift.get("timestamp"))
        if district not in valid_districts or hours <= 0 or timestamp is None:
            continue
        total = district_totals[district]
        total["income"] += income
        total["hours"] += hours
        total["shifts"] += 1
        total["days"].add(timestamp.date())

    rates = {
        district: total["income"] / total["hours"]
        for district, total in district_totals.items()
        if total["hours"] > 0
    }
    if not rates:
        return {
            "work_type": "courier",
            "title": "Районы по вашей истории смен",
            "horizon_minutes": None,
            "status": "insufficient_data",
            "note": (
                "Для персональной оценки нужны сохранённые курьерские смены "
                "с районом и количеством отработанных часов."
            ),
            "empty_message": (
                "Пока нет подходящих смен. Указывай район и часы при сохранении "
                "курьерской смены."
            ),
            "items": [],
        }

    minimum_rate = min(rates.values())
    maximum_rate = max(rates.values())
    items = []
    for district, rate in rates.items():
        total = district_totals[district]
        score = (
            50 if maximum_rate == minimum_rate
            else round((rate - minimum_rate) / (maximum_rate - minimum_rate) * 100)
        )
        confidence = min(
            95,
            20 + min(total["shifts"], 4) * 15 + min(len(total["days"]), 5) * 5,
        )
        items.append({
            "district": district,
            "score": score,
            "confidence": confidence,
            "trend": "unknown",
            "sample_count": total["shifts"],
            "net_income_per_hour": round(rate, 2),
            "metric_label": "чистый доход/ч",
        })

    items.sort(key=lambda item: (item["score"], item["confidence"]), reverse=True)
    total_shifts = sum(item["sample_count"] for item in items)
    return {
        "work_type": "courier",
        "title": "Районы по вашей истории смен",
        "horizon_minutes": None,
        "status": "available" if total_shifts >= 3 else "limited",
        "note": (
            "Сравнение основано на вашей истории чистого дохода за час по районам "
            "за последние 90 дней; балл сравнивает районы внутри этой истории. "
            "Это не прогноз числа заказов и не учитывает "
            "время начала смен, тип транспорта, погоду или конкуренцию."
        ),
        "empty_message": "Нет сохранённых смен с районом и часами работы.",
        "items": items[:7],
    }


def build_forecast(work_type: str, inputs: dict, now: datetime | None = None) -> dict:
    """Build a transparent, first-stage forecast from available app data."""
    if work_type not in {"taxi", "courier"}:
        raise ValueError("Unsupported work type")
    generated_at = now or config.get_moscow_time()
    result = (
        _taxi_forecast(inputs, generated_at)
        if work_type == "taxi"
        else _courier_forecast(inputs)
    )
    result["generated_at"] = generated_at.isoformat(timespec="minutes")
    return result
