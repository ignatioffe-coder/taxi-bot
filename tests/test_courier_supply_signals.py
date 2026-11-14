import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("BOT_TOKEN", "test-bot-token")

import crowd_signals
import config
from demand_forecasting import build_forecast
import mini_app


class CourierSupplySignalTests(unittest.TestCase):
    now = datetime(2026, 11, 14, 12, 0)

    def _multiplier(self, category):
        return crowd_signals.district_multipliers(
            {
                "Арбат": {
                    "report_categories": [category],
                    "reporters": 2,
                    "last_report_update": self.now.strftime("%Y-%m-%d %H:%M:%S"),
                }
            },
            self.now,
        )["Арбат"]

    def test_supply_reports_change_the_estimate_in_expected_directions(self):
        self.assertGreater(self._multiplier("few_couriers"), 1)
        self.assertLess(self._multiplier("many_couriers"), 1)

    def test_courier_report_categories_allow_supply_observations(self):
        self.assertIn("few_couriers", mini_app.REPORT_CATEGORIES["courier"])
        self.assertIn("many_couriers", mini_app.REPORT_CATEGORIES["courier"])

    def test_forecast_explains_personal_and_local_factors(self):
        district = config.MOSCOW_DISTRICTS[0]
        other_district = config.MOSCOW_DISTRICTS[1]
        shifts = [
            {
                "district": area,
                "gross_income": 500,
                "hours_worked": 2,
                "orders_count": 4,
                "orders_count_known": True,
                "timestamp": (self.now - timedelta(days=day)).isoformat(),
            }
            for area in (district, other_district)
            for day in (1, 2, 3)
        ]
        forecast = build_forecast(
            "courier",
            {"shifts": shifts},
            now=self.now,
            factors={
                "weather": {"impact": 1, "text": "дождь"},
                "events": {district: 0.1},
                "traffic": {district: 0.05},
                "reports": {district: self._multiplier("few_couriers")},
                "report_details": {
                    district: {"report_categories": ["few_couriers"]}
                },
            },
        )

        item = next(item for item in forecast["items"] if item["district"] == district)
        self.assertIn("Личные заказы/ч", item["signals"])
        self.assertIn("Погодный спрос", item["signals"])
        self.assertIn("Событие рядом", item["signals"])
        self.assertIn("Дорожные инциденты", item["signals"])
        self.assertIn("Мало курьеров", item["signals"])


if __name__ == "__main__":
    unittest.main()
