import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from urllib.parse import urlencode

os.environ.setdefault("BOT_TOKEN", "test-bot-token")

import config
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from mini_app import create_app, validate_init_data


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
        self.client = TestClient(TestServer(create_app()))
        await self.client.start_server()
        self.init_data = signed_init_data(
            {"id": 123456, "first_name": "Driver", "username": "driver"},
            config.BOT_TOKEN,
            int(time.time()),
        )

    async def asyncTearDown(self):
        await self.client.close()
        config.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    async def test_dashboard_requires_valid_telegram_auth(self):
        response = await self.client.get("/api/dashboard")
        self.assertEqual(response.status, 401)

        response = await self.client.get(
            "/api/dashboard?work_type=taxi&period=week",
            headers={"X-Telegram-Init-Data": self.init_data},
        )
        self.assertEqual(response.status, 200)
        dashboard = await response.json()
        self.assertEqual(dashboard["user"]["first_name"], "Driver")
        self.assertEqual(len(dashboard["district_groups"]), 12)
        self.assertEqual(
            sum(len(group["districts"]) for group in dashboard["district_groups"]),
            132,
        )

    async def test_district_catalog_is_public(self):
        response = await self.client.get("/api/districts")
        self.assertEqual(response.status, 200)
        catalog = await response.json()
        self.assertEqual(len(catalog["district_groups"]), 12)
        self.assertEqual(
            sum(len(group["districts"]) for group in catalog["district_groups"]),
            132,
        )

    async def test_serves_mini_app_assets(self):
        response = await self.client.get("/")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("Taxi Forecast", await response.text())

        response = await self.client.get("/static/app.js")
        self.assertEqual(response.status, 200)
        self.assertIn("Telegram.WebApp", await response.text())

    async def test_submission_validates_district_and_saves_for_verified_user(self):
        headers = {"X-Telegram-Init-Data": self.init_data}
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

        response = await self.client.get(
            "/api/dashboard?work_type=taxi&period=week",
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

        response = await self.client.get(
            "/api/dashboard?work_type=courier&period=week",
            headers=headers,
        )
        self.assertEqual((await response.json())["summary"]["shifts"], 0)

    async def test_shift_rejects_invalid_values(self):
        headers = {"X-Telegram-Init-Data": self.init_data}
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


if __name__ == "__main__":
    unittest.main()
