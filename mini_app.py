"""Telegram Mini App HTTP endpoints and Telegram initData verification."""

import asyncio
import hashlib
import hmac
import json
import logging
import math
import sqlite3
import time
from datetime import date, datetime, time as datetime_time, timedelta
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import LabeledPrice

import config
import database
import demand_forecasting
import heatmap
import subscriptions

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"
PAYMENT_BOT_KEY = web.AppKey("payment_bot", Bot)
WEATHER_OPTIONS = {"", "Ясно", "Облачно", "Дождь", "Снег", "Туман", "Гроза"}
WORK_TYPES = {"taxi", "courier"}
VEHICLE_TYPES = {"", "car", "motorcycle", "bicycle", "scooter", "foot", "other"}
FUEL_TYPES = {"", "petrol", "diesel", "electric", "hybrid", "other"}
PERIODS = {"day", "week", "month"}
AMOUNT_FIELDS = {
    "gross_income",
    "tips",
    "fuel_cost",
    "commission_cost",
    "maintenance_cost",
    "other_cost",
}
MAX_INIT_DATA_AGE_SECONDS = 24 * 60 * 60


def validate_init_data(init_data: str, bot_token: str, now: int | None = None) -> dict:
    """Validate Telegram Web App initData and return its decoded user."""
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
        values = dict(pairs)
        if len(values) != len(pairs):
            raise ValueError("duplicate initData fields")
        received_hash = values.pop("hash")
        auth_date = int(values["auth_date"])
        current_time = int(time.time()) if now is None else now
        if auth_date > current_time + 60 or current_time - auth_date > MAX_INIT_DATA_AGE_SECONDS:
            raise ValueError("expired initData")

        data_check_string = "\n".join(
            f"{key}={value}" for key, value in sorted(values.items())
        )
        secret_key = hmac.new(
            b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256
        ).digest()
        expected_hash = hmac.new(
            secret_key, data_check_string.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected_hash, received_hash):
            raise ValueError("invalid initData hash")

        user = json.loads(values["user"])
        if not isinstance(user, dict) or type(user.get("id")) is not int:
            raise ValueError("invalid Telegram user")
        return user
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise web.HTTPUnauthorized(text="Telegram authorization is invalid or expired")


def _authenticated_user(request: web.Request) -> dict:
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not init_data:
        raise web.HTTPUnauthorized(text="Telegram authorization is required")
    return validate_init_data(init_data, config.BOT_TOKEN)


async def _onboarded_user(request: web.Request) -> dict:
    user = _authenticated_user(request)
    await asyncio.to_thread(
        database.add_user,
        user["id"],
        user.get("username", ""),
        user.get("first_name", ""),
        user.get("last_name", ""),
    )
    if not await asyncio.to_thread(database.is_user_onboarded, user["id"]):
        raise web.HTTPForbidden(text="Complete your profile before using Taxi Forecast")
    access = await asyncio.to_thread(database.get_subscription_access, user["id"])
    if not access["active"]:
        raise web.HTTPPaymentRequired(text="A current subscription is required")
    return user


def _recommendations() -> list[dict]:
    now = config.get_moscow_time()
    month_ago = config.moscow_time_str(timedelta(days=-30))

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT district, coefficient
            FROM coefficients
            WHERE day_of_week = ?
              AND hour BETWEEN ? AND ?
              AND timestamp > ?
            ORDER BY timestamp DESC
            """,
            (now.weekday(), max(0, now.hour - 2), min(23, now.hour + 2), month_ago),
        ).fetchall()
    finally:
        conn.close()

    district_values: dict[str, list[float]] = {}
    for row in rows:
        district_values.setdefault(row["district"], []).append(row["coefficient"])

    result = []
    valid_districts = set(config.MOSCOW_DISTRICTS)
    for district, coefficients in district_values.items():
        if district in valid_districts and len(coefficients) >= 3:
            result.append({
                "district": district,
                "avg_coefficient": round(sum(coefficients) / len(coefficients), 2),
                "max_coefficient": round(max(coefficients), 2),
                "data_points": len(coefficients),
                "reliability": min(round(len(coefficients) * 10), 100),
            })
    return sorted(result, key=lambda item: item["avg_coefficient"], reverse=True)[:7]


async def index(_request: web.Request) -> web.FileResponse:
    response = web.FileResponse(STATIC_DIR / "index.html")
    response.headers["Cache-Control"] = "no-store"
    return response
def _build_district_groups(market_stats: dict) -> list[dict]:
    return [
        {
            "name": okrug,
            "districts": [
                {
                    "name": district,
                    **market_stats.get(district, {
                        "avg_coefficient": None,
                        "records": 0,
                        "last_update": None,
                    }),
                }
                for district in districts
            ],
        }
        for okrug, districts in config.MOSCOW_DISTRICTS_BY_OKRUG.items()
    ]


async def district_catalog(_request: web.Request) -> web.Response:
    await _onboarded_user(_request)
    market_stats = await asyncio.to_thread(database.get_market_district_stats)
    return web.json_response(
        {"district_groups": _build_district_groups(market_stats)},
        headers={"Cache-Control": "no-store"},
    )


async def map_config(_request: web.Request) -> web.Response:
    return web.json_response(
        {"yandex_maps_api_key": config.YANDEX_MAPS_API_KEY},
        headers={"Cache-Control": "no-store"},
    )


def _period_start(period: str) -> datetime:
    now = config.get_moscow_time()
    if period == "day":
        date = now.date()
    elif period == "week":
        date = now.date() - timedelta(days=now.weekday())
    else:
        date = now.date().replace(day=1)
    return datetime.combine(date, datetime_time.min)


async def dashboard(request: web.Request) -> web.Response:
    user = await _onboarded_user(request)
    work_type = request.query.get("work_type", "taxi")
    period = request.query.get("period", "week")
    if work_type not in WORK_TYPES:
        raise web.HTTPBadRequest(text="Choose taxi or courier")
    if period not in PERIODS:
        raise web.HTTPBadRequest(text="Choose day, week, or month")

    summary, recommendations, market_stats, forecast_inputs = await asyncio.gather(
        asyncio.to_thread(
            database.get_work_summary,
            user["id"],
            work_type,
            _period_start(period),
        ),
        asyncio.to_thread(_recommendations),
        asyncio.to_thread(database.get_market_district_stats),
        asyncio.to_thread(
            database.get_demand_forecast_inputs,
            user["id"],
            work_type,
        ),
    )
    forecast = await asyncio.to_thread(
        demand_forecasting.build_forecast,
        work_type,
        forecast_inputs,
    )
    coefficient_stats = database.get_user_stats(user["id"])
    profile = await asyncio.to_thread(database.get_driver_profile, user["id"])
    weekly_summary = summary
    if period != "week":
        weekly_summary = await asyncio.to_thread(
            database.get_work_summary,
            user["id"],
            work_type,
            _period_start("week"),
        )
    district_groups = _build_district_groups(market_stats)
    return web.json_response({
        "user": {
            "first_name": profile.get("preferred_name") or user.get("first_name", ""),
            "username": user.get("username", ""),
        },
        "work_type": work_type,
        "period": period,
        "summary": summary,
        "weekly_goal": {
            "target": profile.get("weekly_income_goal") or 0,
            "net_income": weekly_summary["net_income"],
        },
        "coefficient_stats": {
            "total_records": coefficient_stats.get("total_records", 0) or 0,
            "avg_coefficient": round(coefficient_stats.get("avg_coef") or 0, 2),
        },
        "recommendations": recommendations,
        "forecast": forecast,
        "district_groups": district_groups,
    })


def _validated_amount(payload: dict, field: str, maximum: float = 1_000_000) -> float:
    value = payload.get(field, 0)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= maximum
    ):
        raise web.HTTPBadRequest(text=f"{field} must be between 0 and {maximum}")
    return round(float(value), 2)


async def submit_shift(request: web.Request) -> web.Response:
    user = await _onboarded_user(request)
    try:
        payload = await request.json()
    except (json.JSONDecodeError, web.HTTPException):
        raise web.HTTPBadRequest(text="Request body must be valid JSON")
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="Request body must be a JSON object")

    work_type = payload.get("work_type")
    if work_type not in WORK_TYPES:
        raise web.HTTPBadRequest(text="Choose taxi or courier")

    district = payload.get("district") or None
    if district is not None and district not in config.MOSCOW_DISTRICTS:
        raise web.HTTPBadRequest(text="Choose a district from the list")

    amounts = {
        field: _validated_amount(payload, field)
        for field in AMOUNT_FIELDS
    }
    hours_worked = _validated_amount(payload, "hours_worked", maximum=24)

    shift_id = await asyncio.to_thread(
        database.add_work_shift,
        user["id"],
        work_type,
        district,
        amounts["gross_income"],
        amounts["tips"],
        amounts["fuel_cost"],
        amounts["commission_cost"],
        amounts["maintenance_cost"],
        amounts["other_cost"],
        hours_worked,
    )
    return web.json_response({"saved": True, "shift_id": shift_id})


def _profile_response(profile: dict, subscription_access: dict | None = None) -> dict:
    fields = (
        "work_type", "vehicle_type", "vehicle_make", "vehicle_model",
        "vehicle_year", "fuel_type", "weekly_income_goal", "reminders_enabled",
        *database.DOCUMENT_FIELDS,
    )
    result = {}
    for field in fields:
        value = profile.get(field)
        if field == "reminders_enabled":
            result[field] = bool(value)
        elif field == "weekly_income_goal":
            result[field] = value or 0
        elif field == "vehicle_year":
            result[field] = value
        else:
            result[field] = value or ""
    result.update({
        "first_name": profile.get("first_name") or "",
        "last_name": profile.get("last_name") or "",
        "preferred_name": profile.get("preferred_name") or "",
        "onboarding_complete": bool(profile.get("onboarding_complete")),
        "subscription": subscription_access,
    })
    return result


async def get_profile(request: web.Request) -> web.Response:
    user = _authenticated_user(request)
    await asyncio.to_thread(
        database.add_user,
        user["id"],
        user.get("username", ""),
        user.get("first_name", ""),
        user.get("last_name", ""),
    )
    profile = await asyncio.to_thread(database.get_driver_profile, user["id"])
    subscription_access = None
    if profile.get("onboarding_complete") and profile.get("preferred_name"):
        subscription_access = await asyncio.to_thread(
            database.get_subscription_access, user["id"]
        )
        subscription_access.update({
            "price_stars": subscriptions.MONTHLY_PRICE_STARS,
            "period_days": subscriptions.SUBSCRIPTION_PERIOD_SECONDS // (24 * 60 * 60),
        })
    return web.json_response(_profile_response(profile, subscription_access))


async def complete_onboarding(request: web.Request) -> web.Response:
    user = _authenticated_user(request)
    try:
        payload = await request.json()
    except (json.JSONDecodeError, web.HTTPException):
        raise web.HTTPBadRequest(text="Request body must be valid JSON")
    if not isinstance(payload, dict) or set(payload) != {"name", "work_type"}:
        raise web.HTTPBadRequest(text="Name and work type are required")

    name = payload["name"]
    work_type = payload["work_type"]
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
        raise web.HTTPBadRequest(text="Name must be between 1 and 40 characters")
    if not isinstance(work_type, str) or work_type not in WORK_TYPES:
        raise web.HTTPBadRequest(text="Choose taxi or courier")

    await asyncio.to_thread(
        database.add_user,
        user["id"],
        user.get("username", ""),
        user.get("first_name", ""),
        user.get("last_name", ""),
    )
    await asyncio.to_thread(
        database.complete_onboarding,
        user["id"],
        name.strip(),
        work_type,
    )
    await asyncio.to_thread(database.get_subscription_access, user["id"])
    return web.json_response({"completed": True})


async def create_subscription_invoice(request: web.Request) -> web.Response:
    user = _authenticated_user(request)
    if not await asyncio.to_thread(database.is_user_onboarded, user["id"]):
        raise web.HTTPForbidden(text="Complete your profile before subscribing")

    try:
        payload = await request.json()
    except (json.JSONDecodeError, web.HTTPException):
        raise web.HTTPBadRequest(text="Request body must be valid JSON")
    if not isinstance(payload, dict) or payload.get("accept_terms") is not True:
        raise web.HTTPBadRequest(text="Accept the subscription terms before paying")

    access = await asyncio.to_thread(database.get_subscription_access, user["id"])
    if access["status"] == "paid":
        raise web.HTTPConflict(text="Your subscription is already active")

    payment_bot = request.app.get(PAYMENT_BOT_KEY)
    if payment_bot is None:
        raise web.HTTPServiceUnavailable(text="Telegram payments are not configured")

    invoice_payload = subscriptions.create_invoice_payload(user["id"])
    try:
        invoice_url = await payment_bot.create_invoice_link(
            title="Taxi Forecast — 30 дней",
            description=(
                f"Доступ ко всем функциям Taxi Forecast на 30 дней "
                f"за {subscriptions.MONTHLY_PRICE_STARS} ⭐. "
                "Автопродление — каждые 30 дней."
            ),
            payload=invoice_payload,
            provider_token="",
            currency="XTR",
            prices=[
                LabeledPrice(
                    label="Подписка на 30 дней",
                    amount=subscriptions.MONTHLY_PRICE_STARS,
                )
            ],
            subscription_period=subscriptions.SUBSCRIPTION_PERIOD_SECONDS,
        )
    except TelegramAPIError:
        logger.exception("Could not create Stars invoice for user %s", user["id"])
        raise web.HTTPBadGateway(text="Could not create a Telegram Stars invoice")

    await asyncio.to_thread(
        database.save_star_invoice,
        user["id"],
        invoice_payload,
    )
    return web.json_response({"invoice_url": invoice_url})


async def save_profile(request: web.Request) -> web.Response:
    user = await _onboarded_user(request)
    try:
        payload = await request.json()
    except (json.JSONDecodeError, web.HTTPException):
        raise web.HTTPBadRequest(text="Request body must be valid JSON")
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="Request body must be a JSON object")

    required_fields = {
        "work_type", "vehicle_type", "vehicle_make", "vehicle_model",
        "vehicle_year", "fuel_type", "weekly_income_goal", "reminders_enabled",
        *database.DOCUMENT_FIELDS,
    }
    if set(payload) != required_fields:
        raise web.HTTPBadRequest(text="Profile fields are incomplete or unsupported")
    if (
        not isinstance(payload["work_type"], str)
        or payload["work_type"] not in WORK_TYPES
    ):
        raise web.HTTPBadRequest(text="Choose taxi or courier")
    if (
        not isinstance(payload["vehicle_type"], str)
        or payload["vehicle_type"] not in VEHICLE_TYPES
    ):
        raise web.HTTPBadRequest(text="Choose a supported vehicle type")
    if (
        not isinstance(payload["fuel_type"], str)
        or payload["fuel_type"] not in FUEL_TYPES
    ):
        raise web.HTTPBadRequest(text="Choose a supported fuel type")
    if not isinstance(payload["reminders_enabled"], bool):
        raise web.HTTPBadRequest(text="reminders_enabled must be true or false")

    profile = {
        "work_type": payload["work_type"],
        "vehicle_type": payload["vehicle_type"],
        "fuel_type": payload["fuel_type"],
        "reminders_enabled": int(payload["reminders_enabled"] is True),
        "weekly_income_goal": _validated_amount(
            payload, "weekly_income_goal", maximum=1_000_000
        ),
    }
    for field in ("vehicle_make", "vehicle_model"):
        value = payload[field]
        if not isinstance(value, str) or len(value.strip()) > 80:
            raise web.HTTPBadRequest(text=f"{field} must be a string up to 80 characters")
        profile[field] = value.strip()

    vehicle_year = payload["vehicle_year"]
    if vehicle_year in (None, ""):
        profile["vehicle_year"] = None
    elif (
        isinstance(vehicle_year, bool)
        or not isinstance(vehicle_year, int)
        or not 1980 <= vehicle_year <= 2100
    ):
        raise web.HTTPBadRequest(text="vehicle_year is outside the supported range")
    else:
        profile["vehicle_year"] = vehicle_year

    for field in database.DOCUMENT_FIELDS:
        value = payload[field]
        if value in (None, ""):
            profile[field] = None
            continue
        if not isinstance(value, str):
            raise web.HTTPBadRequest(text=f"{field} must be a date or empty")
        try:
            parsed_date = date.fromisoformat(value)
        except ValueError:
            raise web.HTTPBadRequest(text=f"{field} must use YYYY-MM-DD format")
        if parsed_date.isoformat() != value:
            raise web.HTTPBadRequest(text=f"{field} must use YYYY-MM-DD format")
        profile[field] = value

    await asyncio.to_thread(database.save_driver_profile, user["id"], profile)
    return web.json_response({"saved": True})


async def submit_coefficient(request: web.Request) -> web.Response:
    user = await _onboarded_user(request)
    try:
        payload = await request.json()
    except (json.JSONDecodeError, web.HTTPException):
        raise web.HTTPBadRequest(text="Request body must be valid JSON")

    district = payload.get("district") if isinstance(payload, dict) else None
    coefficient = payload.get("coefficient") if isinstance(payload, dict) else None
    weather = payload.get("weather", "") if isinstance(payload, dict) else ""
    if district not in config.MOSCOW_DISTRICTS:
        raise web.HTTPBadRequest(text="Choose a district from the list")
    if isinstance(coefficient, bool) or not isinstance(coefficient, (int, float)):
        raise web.HTTPBadRequest(text="Coefficient must be a number from 0.5 to 5")
    if not math.isfinite(coefficient) or not 0.5 <= coefficient <= 5:
        raise web.HTTPBadRequest(text="Coefficient must be a number from 0.5 to 5")
    if weather not in WEATHER_OPTIONS:
        raise web.HTTPBadRequest(text="Choose a supported weather option")

    saved = database.add_coefficient(
        user_id=user["id"],
        username=user.get("username", ""),
        district=district,
        coefficient=float(coefficient),
        weather=weather,
    )
    if not saved:
        logger.error("Mini App could not save a coefficient for user %s", user["id"])
        raise web.HTTPInternalServerError(text="Could not save coefficient")
    return web.json_response({"saved": True})


def create_app(payment_bot: Bot | None = None) -> web.Application:
    app = web.Application()
    app[PAYMENT_BOT_KEY] = payment_bot
    app.router.add_get("/", index)
    app.router.add_static("/static/", path=STATIC_DIR)
    app.router.add_get("/api/districts", district_catalog)
    app.router.add_get("/api/map-config", map_config)
    app.router.add_get("/api/dashboard", dashboard)
    app.router.add_get("/api/profile", get_profile)
    app.router.add_post("/api/subscription/invoice", create_subscription_invoice)
    app.router.add_post("/api/onboarding", complete_onboarding)
    app.router.add_put("/api/profile", save_profile)
    app.router.add_post("/api/coefficients", submit_coefficient)
    app.router.add_post("/api/shifts", submit_shift)
    return app
