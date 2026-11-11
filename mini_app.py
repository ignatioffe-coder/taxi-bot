"""Telegram Mini App HTTP endpoints and Telegram initData verification."""

import asyncio
import hashlib
import hmac
import json
import logging
import math
import sqlite3
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

import config
import database
import heatmap

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"
WEATHER_OPTIONS = {"", "Ясно", "Облачно", "Дождь", "Снег", "Туман", "Гроза"}
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
    for district, coefficients in district_values.items():
        if len(coefficients) >= 3:
            result.append({
                "district": district,
                "avg_coefficient": round(sum(coefficients) / len(coefficients), 2),
                "max_coefficient": round(max(coefficients), 2),
                "data_points": len(coefficients),
                "reliability": min(round(len(coefficients) * 10), 100),
            })
    return sorted(result, key=lambda item: item["avg_coefficient"], reverse=True)[:7]


async def index(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def dashboard(request: web.Request) -> web.Response:
    user = _authenticated_user(request)
    stats = database.get_user_stats(user["id"])
    return web.json_response({
        "user": {
            "first_name": user.get("first_name", ""),
            "username": user.get("username", ""),
        },
        "stats": {
            "total_records": stats.get("total_records", 0) or 0,
            "avg_coefficient": round(stats.get("avg_coef") or 0, 2),
            "last_record": stats.get("last_record"),
        },
        "recommendations": await asyncio.to_thread(_recommendations),
        "district_stats": database.get_districts_stats(),
        "districts": config.MOSCOW_DISTRICTS,
    })


async def demand_map(request: web.Request) -> web.Response:
    _authenticated_user(request)
    image = await asyncio.to_thread(heatmap.generate_map)
    return web.Response(
        body=image.getvalue(),
        content_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


async def submit_coefficient(request: web.Request) -> web.Response:
    user = _authenticated_user(request)
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

    database.add_user(
        user["id"],
        user.get("username", ""),
        user.get("first_name", ""),
        user.get("last_name", ""),
    )
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


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_static("/static/", path=STATIC_DIR)
    app.router.add_get("/api/dashboard", dashboard)
    app.router.add_get("/api/map", demand_map)
    app.router.add_post("/api/coefficients", submit_coefficient)
    return app
