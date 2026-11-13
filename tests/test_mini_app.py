import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlencode

os.environ.setdefault("BOT_TOKEN", "test-bot-token")

import config
import subscriptions
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from mini_app import create_app, validate_init_data


class FakePaymentBot:
    def __init__(self):
        self.invoice_calls = []

    async def create_invoice_link(self, **kwargs):
        self.invoice_calls.append(kwargs)
        return "https://t.me/$test_invoice"


def signed_init_data(user: dict, bot_token: str, auth_date: int) -> str:
    values = {
        "auth_date": str(auth_date),
        "user": json.dumps(user, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(values)


class TelegramInitDataTests(unittest.TestCase):
    def test_accepts_valid_data(self):
        user = {"id": 123, "first_name": "Driver"}
        init_data = signed_init_data(user, "test-bot-token", int(time.time()))
        self.assertEqual(validate_init_data(init_data, "test-bot-token")["id"], 123)

    def test_rejects_bad_signature_and_expired_data(self):
        now = int(time.time())
        init_data = signed_init_data({"id": 123}, "test-bot-token", now)
        with self.assertRaises(web.HTTPUnauthorized):
            validate_init_data(init_data, "wrong-token", now=now)

        expired = signed_init_data(
            {"id": 123}, "test-bot-token", now - 24 * 60 * 60 - 1
        )
        with self.assertRaises(web.HTTPUnauthorized):
            validate_init_data(expired, "test-bot-token", now=now)


class MiniAppApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_db_path = config.DB_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test-mini-app.db")
        config.DB_PATH = self.db_path
        import database

        database.init_db()
        self.payment_bot = FakePaymentBot()
        self.client = TestClient(TestServer(create_app(self.payment_bot)))
        await self.client.start_server()
        self.init_data = signed_init_data(
            {"id": 123456, "first_name": "Driver", "username": "driver"},
            config.BOT_TOKEN,
            int(time.time()),
        )

    async def complete_onboarding(self, headers=None, name="Driver", work_type="taxi"):
        headers = headers or {"X-Telegram-Init-Data": self.init_data}
        await self.client.get("/api/profile", headers=headers)
        response = await self.client.post(
            "/api/onboarding",
            headers=headers,
            json={"name": name, "work_type": work_type},
        )
        self.assertEqual(response.status, 200)

    async def asyncTearDown(self):
        await self.client.close()
        config.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    async def test_dashboard_requires_valid_telegram_auth(self):
        response = await self.client.get("/api/dashboard")
        self.assertEqual(response.status, 401)

        headers = {"X-Telegram-Init-Data": self.init_data}
        await self.client.get("/api/profile", headers=headers)
        response = await self.client.get(
            "/api/dashboard?work_type=taxi&period=week",
            headers=headers,
        )
        self.assertEqual(response.status, 403)
        await self.complete_onboarding(headers)
        response = await self.client.get(
            "/api/dashboard?work_type=taxi&period=week",
            headers=headers,
        )
        self.assertEqual(response.status, 200)
        dashboard = await response.json()
        self.assertEqual(dashboard["user"]["first_name"], "Driver")
        self.assertEqual(dashboard["weekly_goal"], {"target": 0, "net_income": 0})
        self.assertEqual(dashboard["forecast"]["work_type"], "taxi")
        self.assertEqual(dashboard["forecast"]["status"], "insufficient_data")
        self.assertEqual(len(dashboard["district_groups"]), 12)
        self.assertEqual(
            sum(len(group["districts"]) for group in dashboard["district_groups"]),
            132,
        )

    async def test_dashboard_forecast_scopes_courier_data_to_current_user(self):
        headers = {"X-Telegram-Init-Data": self.init_data}
        await self.complete_onboarding(headers)
        import database

        district = config.MOSCOW_DISTRICTS[0]
        other_district = config.MOSCOW_DISTRICTS[1]
        timestamp = database.get_moscow_time().strftime("%Y-%m-%d %H:%M:%S")
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executemany(
                """
                INSERT INTO coefficients (user_id, district, coefficient, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (123456, district, 2.8, timestamp),
                    (987654, district, 3.2, timestamp),
                ],
            )
            conn.executemany(
                """
                INSERT INTO work_shifts (
                    user_id, work_type, district, gross_income, hours_worked, timestamp
                )
                VALUES (?, 'courier', ?, ?, 10, ?)
                """,
                [
                    (123456, district, 900, timestamp),
                    (987654, other_district, 5000, timestamp),
                ],
            )
            conn.commit()

        taxi_response = await self.client.get(
            "/api/dashboard?work_type=taxi&period=week",
            headers=headers,
        )
        taxi_forecast = (await taxi_response.json())["forecast"]
        self.assertEqual(taxi_forecast["items"][0]["district"], district)
        self.assertEqual(taxi_forecast["items"][0]["sample_count"], 2)

        courier_response = await self.client.get(
            "/api/dashboard?work_type=courier&period=week",
            headers=headers,
        )
        courier_forecast = (await courier_response.json())["forecast"]
        # Личных районов ровно один — смена второго пользователя не должна
        # попасть в расчёт. Остальные пункты — публичная стартовая оценка
        # по плотности заведений, она добавляется намеренно.
        personal = [
            item for item in courier_forecast["items"]
            if item.get("method") != "poi_baseline"
        ]
        self.assertEqual(len(personal), 1)
        self.assertEqual(personal[0]["district"], district)
        self.assertEqual(personal[0]["net_income_per_hour"], 90)
        self.assertNotIn(
            500,
            [item.get("net_income_per_hour") for item in courier_forecast["items"]],
        )

    async def test_driver_reports_expire_and_are_rate_limited(self):
        headers = {"X-Telegram-Init-Data": self.init_data}
        await self.complete_onboarding(headers)
        district = config.MOSCOW_DISTRICTS[0]
        payload = {
            "work_type": "taxi",
            "category": "high_demand",
            "district": district,
            "details": "Много заказов",
        }

        response = await self.client.post(
            "/api/reports",
            headers=headers,
            json=payload,
        )
        self.assertEqual(response.status, 200)
        report_id = (await response.json())["report_id"]
        response = await self.client.get(
            "/api/reports?work_type=taxi",
            headers=headers,
        )
        reports = (await response.json())["reports"]
        self.assertEqual(reports[0]["id"], report_id)
        self.assertEqual(reports[0]["confidence"], "single")
        self.assertNotIn("user_id", reports[0])

        catalog_response = await self.client.get(
            "/api/districts?work_type=taxi",
            headers=headers,
        )
        self.assertEqual(catalog_response.status, 200)
        catalog = await catalog_response.json()
        districts = [
            item
            for group in catalog["district_groups"]
            for item in group["districts"]
        ]
        reported_district = next(
            item for item in districts if item["name"] == district
        )
        self.assertEqual(reported_district["report_count"], 1)

        duplicate = await self.client.post(
            "/api/reports",
            headers=headers,
            json=payload,
        )
        self.assertEqual(duplicate.status, 409)

        categories = [
            "low_demand", "airport_queue", "few_cars", "traffic", "accident",
        ]
        for index, category in enumerate(categories, start=1):
            limited = await self.client.post(
                "/api/reports",
                headers=headers,
                json={
                    **payload,
                    "category": category,
                    "district": config.MOSCOW_DISTRICTS[index],
                },
            )
            expected_status = 429 if index == len(categories) else 200
            self.assertEqual(limited.status, expected_status)

    async def test_driver_reports_are_corroborated_and_admin_can_moderate(self):
        headers = {"X-Telegram-Init-Data": self.init_data}
        await self.complete_onboarding(headers)
        import database

        district = config.MOSCOW_DISTRICTS[0]
        first = database.add_driver_report(
            123456, "taxi", "high_demand", district, "Большой спрос"
        )
        second = database.add_driver_report(
            987654, "taxi", "high_demand", district, "Есть заказы"
        )
        public_reports = database.get_active_driver_reports("taxi")
        self.assertEqual(public_reports[0]["id"], second["report_id"])
        self.assertEqual(public_reports[0]["confidence"], "corroborated")
        self.assertEqual(public_reports[0]["corroborating_reporters"], 2)

        admin_headers = {
            "X-Telegram-Init-Data": signed_init_data(
                {"id": config.ADMIN_IDS[0], "first_name": "Admin"},
                config.BOT_TOKEN,
                int(time.time()),
            )
        }
        summary_response = await self.client.get(
            "/api/admin/summary",
            headers=admin_headers,
        )
        self.assertEqual(summary_response.status, 200)
        summary = await summary_response.json()
        self.assertEqual(summary["active_reports"], 2)

        moderation_response = await self.client.patch(
            f"/api/admin/reports/{first['report_id']}",
            headers=admin_headers,
            json={"status": "hidden"},
        )
        self.assertEqual(moderation_response.status, 200)
        self.assertEqual(len(database.get_active_driver_reports("taxi")), 1)

    async def test_forecast_runs_are_deduplicated_and_evaluated_against_observations(self):
        import database

        district = config.MOSCOW_DISTRICTS[0]
        generated_at = database.get_moscow_time().replace(
            year=2026, month=1, day=1, hour=10, minute=0, second=0, microsecond=0
        )
        taxi_forecast = {
            "items": [{
                "district": district,
                "score": 70,
                "confidence": 60,
                "current_coefficient": 2.0,
            }]
        }
        taxi_run = database.record_forecast_snapshot(
            123456, "taxi", taxi_forecast, generated_at
        )
        duplicate_run = database.record_forecast_snapshot(
            123456,
            "taxi",
            taxi_forecast,
            generated_at + timedelta(minutes=5),
        )
        self.assertEqual(duplicate_run, taxi_run)

        courier_run = database.record_forecast_snapshot(
            123456,
            "courier",
            {
                "items": [{
                    "district": district,
                    "score": 80,
                    "confidence": 70,
                    "net_income_per_hour": 100,
                }]
            },
            generated_at,
        )
        self.assertNotEqual(courier_run, taxi_run)
        with closing(sqlite3.connect(config.DB_PATH)) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO coefficients (
                        user_id, district, coefficient, timestamp
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        123456,
                        district,
                        3.0,
                        (generated_at + timedelta(minutes=65)).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO work_shifts (
                        user_id, work_type, district, gross_income, hours_worked,
                        timestamp
                    )
                    VALUES (?, 'courier', ?, ?, ?, ?)
                    """,
                    (
                        123456,
                        district,
                        800,
                        10,
                        (generated_at + timedelta(days=2)).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                    ),
                )

        database.evaluate_forecast_snapshots(
            generated_at + timedelta(days=8, hours=1)
        )
        quality = database.get_forecast_quality_summary()
        self.assertEqual(quality["taxi"]["evaluated"], 1)
        self.assertEqual(quality["taxi"]["mean_absolute_error"], 1)
        self.assertEqual(quality["courier"]["evaluated"], 1)
        self.assertEqual(quality["courier"]["mean_absolute_error"], 20)

    async def test_district_catalog_requires_an_active_trial_or_subscription(self):
        response = await self.client.get("/api/districts")
        self.assertEqual(response.status, 401)

        headers = {"X-Telegram-Init-Data": self.init_data}
        await self.complete_onboarding(headers)
        response = await self.client.get("/api/districts", headers=headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        catalog = await response.json()
        self.assertEqual(len(catalog["district_groups"]), 12)
        self.assertEqual(
            sum(len(group["districts"]) for group in catalog["district_groups"]),
            132,
        )

    async def test_trial_expires_once_and_blocks_paid_features(self):
        import database

        headers = {"X-Telegram-Init-Data": self.init_data}
        await self.complete_onboarding(headers)
        access = database.get_subscription_access(123456)
        self.assertEqual(access["status"], "trial")
        self.assertEqual(
            access["trial_expires_at"] - access["trial_started_at"],
            subscriptions.TRIAL_SECONDS,
        )

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "UPDATE user_subscriptions SET trial_expires_at = ? WHERE user_id = ?",
                (int(time.time()) - 1, 123456),
            )
            conn.commit()
        profile_response = await self.client.get("/api/profile", headers=headers)
        profile = await profile_response.json()
        self.assertEqual(profile["subscription"]["status"], "expired")
        self.assertFalse(profile["subscription"]["active"])

        dashboard_response = await self.client.get(
            "/api/dashboard?work_type=taxi&period=week",
            headers=headers,
        )
        self.assertEqual(dashboard_response.status, 402)
        districts_response = await self.client.get("/api/districts", headers=headers)
        self.assertEqual(districts_response.status, 402)

        later_access = database.get_subscription_access(
            123456, now=int(time.time()) + subscriptions.TRIAL_SECONDS
        )
        self.assertEqual(later_access["status"], "expired")
        self.assertEqual(
            later_access["trial_started_at"], access["trial_started_at"]
        )

    async def test_stars_invoice_requires_terms_and_activates_recurring_access(self):
        import database

        headers = {"X-Telegram-Init-Data": self.init_data}
        await self.complete_onboarding(headers)
        self.assertEqual(
            database.get_subscription_access(123456)["status"], "trial"
        )

        response = await self.client.post(
            "/api/subscription/invoice",
            headers=headers,
            json={"accept_terms": False},
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(self.payment_bot.invoice_calls, [])

        response = await self.client.post(
            "/api/subscription/invoice",
            headers=headers,
            json={"accept_terms": True},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["invoice_url"], "https://t.me/$test_invoice")
        invoice = self.payment_bot.invoice_calls[0]
        self.assertEqual(invoice["currency"], "XTR")
        self.assertEqual(invoice["prices"][0].amount, subscriptions.MONTHLY_PRICE_STARS)
        self.assertEqual(
            invoice["subscription_period"],
            subscriptions.SUBSCRIPTION_PERIOD_SECONDS,
        )
        profile_response = await self.client.get("/api/profile", headers=headers)
        profile = await profile_response.json()
        self.assertEqual(profile["subscription"]["status"], "trial")
        self.assertTrue(
            database.validate_star_invoice(
                123456,
                invoice["payload"],
                "XTR",
                subscriptions.MONTHLY_PRICE_STARS,
            )
        )
        self.assertFalse(
            database.validate_star_invoice(
                123456,
                invoice["payload"],
                "XTR",
                subscriptions.MONTHLY_PRICE_STARS - 1,
            )
        )

        expiration = int(time.time()) + subscriptions.SUBSCRIPTION_PERIOD_SECONDS
        self.assertTrue(database.record_star_payment(
            123456,
            invoice["payload"],
            "XTR",
            subscriptions.MONTHLY_PRICE_STARS,
            "charge-1",
            expiration,
        ))
        self.assertTrue(database.record_star_payment(
            123456,
            invoice["payload"],
            "XTR",
            subscriptions.MONTHLY_PRICE_STARS,
            "charge-1",
            expiration,
        ))
        self.assertEqual(database.get_subscription_access(123456)["status"], "paid")
        profile_response = await self.client.get("/api/profile", headers=headers)
        profile = await profile_response.json()
        self.assertEqual(profile["subscription"]["status"], "paid")
        duplicate_invoice = await self.client.post(
            "/api/subscription/invoice",
            headers=headers,
            json={"accept_terms": True},
        )
        self.assertEqual(duplicate_invoice.status, 409)
        self.assertEqual(len(self.payment_bot.invoice_calls), 1)
        self.assertEqual(
            (
                await self.client.get(
                    "/api/dashboard?work_type=taxi&period=week",
                    headers=headers,
                )
            ).status,
            200,
        )

    async def test_serves_mini_app_assets(self):
        response = await self.client.get("/")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        html = await response.text()
        self.assertIn("MOVENTA", html)
        self.assertNotIn("leaflet", html.lower())

        response = await self.client.get("/static/app.js")
        self.assertEqual(response.status, 200)
        self.assertIn("Telegram.WebApp", await response.text())
        self.assertIn("api-maps.yandex.ru/2.1/", await response.text())

        response = await self.client.get("/static/district-centers.json")
        self.assertEqual(response.status, 200)
        map_data = await response.json()
        self.assertEqual(map_data["license"], "ODbL 1.0")
        self.assertEqual(len(map_data["districts"]), 132)

        response = await self.client.get("/static/district-boundaries.json")
        self.assertEqual(response.status, 200)
        boundaries = await response.json()
        self.assertEqual(boundaries["license"], "ODbL 1.0")
        self.assertEqual(set(boundaries["districts"]), set(config.MOSCOW_DISTRICTS))
        self.assertTrue(all(
            geometry["type"] in {"Polygon", "MultiPolygon"}
            for geometry in boundaries["districts"].values()
        ))

    async def test_market_coefficients_expire_after_two_hours_but_remain_in_history(self):
        import database

        district = config.MOSCOW_DISTRICTS[0]
        expired_at = config.moscow_time_str(timedelta(hours=-2, seconds=-1))
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO coefficients (user_id, district, coefficient, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (987654, district, 4.5, expired_at),
            )
            conn.commit()
        self.assertTrue(database.add_coefficient(123456, "driver", district, 1.7))

        stats = database.get_market_district_stats()[district]
        self.assertEqual(stats["records"], 1)
        self.assertEqual(stats["avg_coefficient"], 1.7)
        self.assertEqual(len(database.get_stats_by_district(district)), 2)

    async def test_yandex_maps_config_is_public_for_browser_loader(self):
        previous_api_key = config.YANDEX_MAPS_API_KEY
        config.YANDEX_MAPS_API_KEY = "browser-restricted-test-key"
        try:
            response = await self.client.get("/api/map-config")
            self.assertEqual(response.status, 200)
            self.assertEqual(
                (await response.json())["yandex_maps_api_key"],
                "browser-restricted-test-key",
            )
        finally:
            config.YANDEX_MAPS_API_KEY = previous_api_key

    async def test_profile_requires_auth_and_saves_document_dates(self):
        response = await self.client.get("/api/profile")
        self.assertEqual(response.status, 401)

        headers = {"X-Telegram-Init-Data": self.init_data}
        response = await self.client.get("/api/profile", headers=headers)
        self.assertEqual(response.status, 200)
        profile = await response.json()
        self.assertEqual(profile["work_type"], "taxi")
        self.assertTrue(profile["reminders_enabled"])
        self.assertFalse(profile["onboarding_complete"])
        await self.complete_onboarding(headers, name="Анна", work_type="courier")

        import database

        today = database.get_moscow_time().date()
        expiry_week = (today + timedelta(days=7)).isoformat()
        expiry_tomorrow = (today + timedelta(days=1)).isoformat()
        payload = {
            "work_type": "courier",
            "vehicle_type": "bicycle",
            "vehicle_make": "Test",
            "vehicle_model": "Bike",
            "vehicle_year": None,
            "fuel_type": "",
            "weekly_income_goal": 45000,
            "reminders_enabled": True,
            "medical_book_expiry": expiry_week,
            "license_expiry": expiry_tomorrow,
            "osago_expiry": today.isoformat(),
            "inspection_expiry": "",
            "taxi_permit_expiry": "",
        }
        response = await self.client.put(
            "/api/profile", headers=headers, json=payload
        )
        self.assertEqual(response.status, 200)

        response = await self.client.get("/api/profile", headers=headers)
        saved = await response.json()
        self.assertEqual(saved["work_type"], "courier")
        self.assertEqual(saved["medical_book_expiry"], expiry_week)
        self.assertEqual(saved["weekly_income_goal"], 45000)

        due = database.get_due_document_reminders(today)
        self.assertEqual({reminder["days_left"] for reminder in due}, {0, 1, 7})
        self.assertEqual(
            {reminder["document_key"] for reminder in due},
            {"medical_book", "license", "osago"},
        )
        for reminder in due:
            self.assertTrue(database.log_document_reminder(reminder))
        self.assertEqual(database.get_due_document_reminders(today), [])

    async def test_profile_can_store_both_work_directions_independently(self):
        headers = {"X-Telegram-Init-Data": self.init_data}
        await self.complete_onboarding(headers, work_type="taxi")

        profile_response = await self.client.get("/api/profile", headers=headers)
        profile = await profile_response.json()
        self.assertEqual(profile["directions"], ["taxi"])

        missing_direction = await self.client.get(
            "/api/profile?work_type=courier",
            headers=headers,
        )
        self.assertEqual(missing_direction.status, 404)

        added = await self.client.post(
            "/api/profile/directions",
            headers=headers,
            json={"work_type": "courier"},
        )
        self.assertEqual(added.status, 200)
        self.assertEqual((await added.json())["directions"], ["taxi", "courier"])

        courier_profile = {
            "work_type": "courier",
            "vehicle_type": "bicycle",
            "vehicle_make": "Test",
            "vehicle_model": "Bike",
            "vehicle_year": None,
            "fuel_type": "",
            "weekly_income_goal": 25000,
            "reminders_enabled": True,
            "medical_book_expiry": "",
            "license_expiry": "",
            "osago_expiry": "",
            "inspection_expiry": "",
            "taxi_permit_expiry": "",
        }
        saved = await self.client.put(
            "/api/profile",
            headers=headers,
            json=courier_profile,
        )
        self.assertEqual(saved.status, 200)

        taxi = await (
            await self.client.get(
                "/api/profile?work_type=taxi",
                headers=headers,
            )
        ).json()
        courier = await (
            await self.client.get(
                "/api/profile?work_type=courier",
                headers=headers,
            )
        ).json()
        self.assertEqual(taxi["vehicle_type"], "")
        self.assertEqual(courier["vehicle_type"], "bicycle")
        self.assertEqual(courier["weekly_income_goal"], 25000)

    async def test_notification_preferences_validate_and_disable_document_reminders(self):
        headers = {"X-Telegram-Init-Data": self.init_data}
        await self.complete_onboarding(headers)
        profile = await (
            await self.client.get("/api/profile", headers=headers)
        ).json()
        self.assertTrue(profile["notifications"]["financial_reminders"])
        import database

        profile_data = database.get_driver_profile(123456)
        profile_data["medical_book_expiry"] = (
            database.get_moscow_time().date() + timedelta(days=7)
        ).isoformat()
        database.save_driver_profile(123456, profile_data)
        self.assertEqual(len(database.get_due_document_reminders(
            database.get_moscow_time().date()
        )), 1)

        payload = {
            "demand_changes": True,
            "promising_areas": False,
            "weather_changes": True,
            "city_events": False,
            "traffic_events": True,
            "personal_recommendations": True,
            "financial_reminders": False,
            "frequency": "hourly",
            "quiet_start": "22:00",
            "quiet_end": "07:00",
        }
        response = await self.client.put(
            "/api/notifications/preferences",
            headers=headers,
            json=payload,
        )
        self.assertEqual(response.status, 200)
        saved = await (
            await self.client.get("/api/profile", headers=headers)
        ).json()
        self.assertFalse(saved["notifications"]["financial_reminders"])
        self.assertEqual(saved["notifications"]["frequency"], "hourly")
        self.assertEqual(
            database.get_due_document_reminders(database.get_moscow_time().date()),
            [],
        )

        invalid = await self.client.put(
            "/api/notifications/preferences",
            headers=headers,
            json={**payload, "quiet_start": "24:00"},
        )
        self.assertEqual(invalid.status, 400)
        invalid_boolean = await self.client.put(
            "/api/notifications/preferences",
            headers=headers,
            json={**payload, "financial_reminders": "no"},
        )
        self.assertEqual(invalid_boolean.status, 400)

    async def test_weather_endpoint_marks_real_source_status(self):
        headers = {"X-Telegram-Init-Data": self.init_data}
        await self.complete_onboarding(headers)
        with patch(
            "mini_app.weather.get_weather",
            return_value={
                "text": "☀️ Ясно, +20°C",
                "condition": "☀️ Ясно",
                "temp": "+20°C",
                "source": "wttr.in",
                "ok": True,
            },
        ):
            response = await self.client.get("/api/weather", headers=headers)
        self.assertEqual(response.status, 200)
        weather_data = await response.json()
        self.assertTrue(weather_data["ok"])
        self.assertEqual(weather_data["source"], "wttr.in")

    async def test_legacy_single_direction_profile_is_migrated(self):
        import database

        database.add_user(123456, "driver", "Driver", "")
        with closing(sqlite3.connect(config.DB_PATH)) as conn:
            with conn:
                conn.execute("DROP TABLE driver_profiles")
                conn.execute("""
                    CREATE TABLE driver_profiles (
                        user_id INTEGER PRIMARY KEY,
                        work_type TEXT NOT NULL DEFAULT 'taxi',
                        vehicle_type TEXT NOT NULL DEFAULT '',
                        vehicle_make TEXT NOT NULL DEFAULT '',
                        vehicle_model TEXT NOT NULL DEFAULT '',
                        vehicle_year INTEGER,
                        fuel_type TEXT NOT NULL DEFAULT '',
                        weekly_income_goal REAL NOT NULL DEFAULT 0,
                        reminders_enabled INTEGER NOT NULL DEFAULT 1,
                        medical_book_expiry TEXT,
                        license_expiry TEXT,
                        osago_expiry TEXT,
                        inspection_expiry TEXT,
                        taxi_permit_expiry TEXT,
                        updated_at DATETIME NOT NULL
                    )
                """)
                conn.execute(
                    """
                    INSERT INTO driver_profiles (
                        user_id, work_type, vehicle_type, vehicle_make, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        123456,
                        "courier",
                        "bicycle",
                        "Example",
                        "2024-01-01 12:00:00",
                    ),
                )

        database.init_db()
        self.assertEqual(database.get_driver_directions(123456), ["courier"])
        profile = database.get_driver_profile(123456, "courier")
        self.assertEqual(profile["vehicle_type"], "bicycle")
        self.assertEqual(profile["vehicle_make"], "Example")

    async def test_profile_rejects_invalid_document_dates_and_flags(self):
        headers = {"X-Telegram-Init-Data": self.init_data}
        await self.complete_onboarding(headers)
        payload = {
            "work_type": "taxi",
            "vehicle_type": "",
            "vehicle_make": "",
            "vehicle_model": "",
            "vehicle_year": None,
            "fuel_type": "",
            "weekly_income_goal": 0,
            "reminders_enabled": True,
            "medical_book_expiry": "not-a-date",
            "license_expiry": "",
            "osago_expiry": "",
            "inspection_expiry": "",
            "taxi_permit_expiry": "",
        }
        response = await self.client.put(
            "/api/profile", headers=headers, json=payload
        )
        self.assertEqual(response.status, 400)
        payload["medical_book_expiry"] = ""
        payload["reminders_enabled"] = 1
        response = await self.client.put(
            "/api/profile", headers=headers, json=payload
        )
        self.assertEqual(response.status, 400)

    async def test_submission_validates_district_and_saves_for_verified_user(self):
        headers = {"X-Telegram-Init-Data": self.init_data}
        await self.complete_onboarding(headers)
        response = await self.client.post(
            "/api/coefficients",
            headers=headers,
            json={"district": "not a district", "coefficient": 1.8},
        )
        self.assertEqual(response.status, 400)

        response = await self.client.post(
            "/api/coefficients",
            headers=headers,
            json={
                "district": config.MOSCOW_DISTRICTS[0],
                "coefficient": 1.8,
                "weather": "Ясно",
            },
        )
        self.assertEqual(response.status, 200)
        self.assertTrue((await response.json())["saved"])

        import database

        self.assertEqual(database.get_user_stats(123456)["total_records"], 1)

    async def test_shift_submission_and_period_totals(self):
        headers = {"X-Telegram-Init-Data": self.init_data}
        await self.complete_onboarding(headers)
        payload = {
            "work_type": "taxi",
            "district": config.MOSCOW_DISTRICTS[0],
            "gross_income": 10000,
            "tips": 1200,
            "fuel_cost": 1500,
            "commission_cost": 1000,
            "maintenance_cost": 500,
            "other_cost": 100,
            "hours_worked": 8,
        }
        response = await self.client.post(
            "/api/shifts",
            headers=headers,
            json=payload,
        )
        self.assertEqual(response.status, 200)

        import database

        profile = database.get_driver_profile(123456)
        profile["weekly_income_goal"] = 20000
        database.save_driver_profile(123456, profile)

        response = await self.client.get(
            "/api/dashboard?work_type=taxi&period=day",
            headers=headers,
        )
        self.assertEqual(response.status, 200)
        dashboard = await response.json()
        summary = dashboard["summary"]
        self.assertEqual(summary["gross_income"], 10000)
        self.assertEqual(summary["total_expenses"], 3100)
        self.assertEqual(summary["net_income"], 8100)
        self.assertEqual(summary["average_per_hour"], 1012.5)
        self.assertEqual(summary["shifts"], 1)
        self.assertEqual(len(summary["chart"]), 7)
        self.assertEqual(
            dashboard["weekly_goal"],
            {"target": 20000, "net_income": 8100},
        )

        response = await self.client.get(
            "/api/dashboard?work_type=courier&period=week",
            headers=headers,
        )
        self.assertEqual((await response.json())["summary"]["shifts"], 0)

    async def test_shift_rejects_invalid_values(self):
        headers = {"X-Telegram-Init-Data": self.init_data}
        await self.complete_onboarding(headers)
        response = await self.client.post(
            "/api/shifts",
            headers=headers,
            json={
                "work_type": "taxi",
                "gross_income": -10,
                "hours_worked": 25,
            },
        )
        self.assertEqual(response.status, 400)

    async def test_financial_summary_separates_cash_and_economic_profit(self):
        headers = {"X-Telegram-Init-Data": self.init_data}
        await self.complete_onboarding(headers)
        response = await self.client.post(
            "/api/shifts",
            headers=headers,
            json={
                "work_type": "taxi",
                "gross_income": 10000,
                "tips": 0,
                "fuel_cost": 1000,
                "rental_cost": 500,
                "charging_cost": 100,
                "connectivity_cost": 50,
                "depreciation_cost": 200,
                "hours_worked": 10,
                "distance_km": 100,
                "orders_count": 5,
            },
        )
        self.assertEqual(response.status, 200)

        dashboard_response = await self.client.get(
            "/api/dashboard?work_type=taxi&period=week",
            headers=headers,
        )
        summary = (await dashboard_response.json())["summary"]
        self.assertEqual(summary["total_expenses"], 1650)
        self.assertEqual(summary["economic_expenses"], 1850)
        self.assertEqual(summary["net_income"], 8350)
        self.assertEqual(summary["economic_profit"], 8150)
        self.assertEqual(summary["expenses_per_km"], 16.5)
        self.assertEqual(summary["average_check"], 2000)
        self.assertEqual(summary["average_order_duration_minutes"], 120)

    async def test_personal_data_export_and_deletion_are_user_scoped(self):
        headers = {"X-Telegram-Init-Data": self.init_data}
        await self.complete_onboarding(headers)
        import database

        await self.client.post(
            "/api/shifts",
            headers=headers,
            json={
                "work_type": "taxi",
                "gross_income": 5000,
                "hours_worked": 5,
            },
        )
        database.add_user(987654, "other", "Other", "")
        database.add_work_shift(
            987654, "taxi", None, 1000, 0, 0, 0, 0, 0, 1
        )

        export_response = await self.client.get(
            "/api/privacy/export",
            headers=headers,
        )
        self.assertEqual(export_response.status, 200)
        exported = await export_response.json()
        self.assertEqual(exported["user"]["user_id"], 123456)
        self.assertEqual(len(exported["shifts"]), 1)

        with closing(sqlite3.connect(config.DB_PATH)) as conn:
            with conn:
                conn.execute(
                    "UPDATE user_subscriptions SET paid_until = ? WHERE user_id = ?",
                    (int(time.time()) + 3600, 123456),
                )
        blocked = await self.client.post(
            "/api/privacy/delete",
            headers=headers,
            json={"confirm": True},
        )
        self.assertEqual(blocked.status, 409)
        self.assertTrue(database.is_user_onboarded(123456))

        with closing(sqlite3.connect(config.DB_PATH)) as conn:
            with conn:
                conn.execute(
                    "UPDATE user_subscriptions SET paid_until = 0 WHERE user_id = ?",
                    (123456,),
                )
        deleted = await self.client.post(
            "/api/privacy/delete",
            headers=headers,
            json={"confirm": True},
        )
        self.assertEqual(deleted.status, 200)
        self.assertFalse(database.is_user_onboarded(123456))
        self.assertEqual(database.get_work_summary(
            123456,
            "taxi",
            database.get_moscow_time() - timedelta(days=30),
        )["shifts"], 0)
        self.assertEqual(
            database.get_work_summary(
                987654,
                "taxi",
                database.get_moscow_time() - timedelta(days=30),
            )["shifts"],
            1,
        )

    async def test_onboarding_saves_name_and_work_type_only_once(self):
        headers = {"X-Telegram-Init-Data": self.init_data}
        response = await self.client.get("/api/profile", headers=headers)
        self.assertFalse((await response.json())["onboarding_complete"])

        response = await self.client.post(
            "/api/onboarding",
            headers=headers,
            json={"name": " ", "work_type": "taxi"},
        )
        self.assertEqual(response.status, 400)

        await self.complete_onboarding(headers, name="Аня", work_type="courier")
        response = await self.client.post(
            "/api/onboarding",
            headers=headers,
            json={"name": "Другое имя", "work_type": "taxi"},
        )
        self.assertEqual(response.status, 200)

        response = await self.client.get("/api/profile", headers=headers)
        profile = await response.json()
        self.assertTrue(profile["onboarding_complete"])
        self.assertEqual(profile["preferred_name"], "Аня")
        self.assertEqual(profile["work_type"], "courier")

        response = await self.client.get(
            "/api/dashboard?work_type=courier&period=week",
            headers=headers,
        )
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["user"]["first_name"], "Аня")

    async def test_legacy_users_are_prompted_after_onboarding_migration(self):
        import database

        legacy_path = os.path.join(self.temp_dir.name, "legacy.db")
        previous_db_path = config.DB_PATH
        try:
            config.DB_PATH = legacy_path
            with closing(sqlite3.connect(legacy_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        first_name TEXT,
                        last_name TEXT,
                        joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        is_active INTEGER DEFAULT 1
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO users (user_id, first_name) VALUES (?, ?)",
                    (789, "Existing"),
                )
                conn.commit()
            database.init_db()
            self.assertFalse(database.is_user_onboarded(789))
            self.assertTrue(database.complete_onboarding(789, "Existing", "taxi"))
            self.assertTrue(database.is_user_onboarded(789))
        finally:
            config.DB_PATH = previous_db_path


if __name__ == "__main__":
    unittest.main()
