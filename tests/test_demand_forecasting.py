import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("BOT_TOKEN", "test-bot-token")

import config
from demand_forecasting import build_forecast


class DemandForecastingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 7, 10, 30)
        self.district = config.MOSCOW_DISTRICTS[0]

    def test_taxi_forecast_uses_recent_and_similar_time_reports(self):
        reports = [
            {
                "district": self.district,
                "coefficient": 3.0,
                "timestamp": (self.now - timedelta(minutes=20)).isoformat(),
                "user_id": 1,
            },
            *[
                {
                    "district": self.district,
                    "coefficient": 1.5,
                    "timestamp": (
                        self.now - timedelta(days=7 * week)
                    ).isoformat(),
                    "user_id": week + 1,
                }
                for week in range(1, 4)
            ],
            {
                "district": "Unknown district",
                "coefficient": 5,
                "timestamp": self.now.isoformat(),
                "user_id": 5,
            },
            {
                "district": self.district,
                "coefficient": 5,
                "timestamp": (self.now + timedelta(minutes=1)).isoformat(),
                "user_id": 6,
            },
        ]

        forecast = build_forecast("taxi", {"reports": reports}, now=self.now)

        self.assertEqual(forecast["status"], "limited")
        self.assertEqual(len(forecast["items"]), 1)
        item = forecast["items"][0]
        self.assertEqual(item["sample_count"], 4)
        self.assertEqual(item["recent_samples"], 1)
        self.assertEqual(item["historical_samples"], 3)
        self.assertEqual(item["trend"], "rising")
        self.assertEqual(item["current_coefficient"], 3.0)

    def test_courier_forecast_ranks_net_income_per_hour(self):
        shifts = [
            {
                "district": self.district,
                "gross_income": 900,
                "tips": 100,
                "fuel_cost": 100,
                "commission_cost": 100,
                "hours_worked": 10,
                "timestamp": (self.now - timedelta(days=day)).isoformat(),
            }
            for day in (1, 2, 3)
        ] + [
            {
                "district": config.MOSCOW_DISTRICTS[1],
                "gross_income": 400,
                "tips": 0,
                "fuel_cost": 0,
                "commission_cost": 0,
                "hours_worked": 10,
                "timestamp": (self.now - timedelta(days=day)).isoformat(),
            }
            for day in (1, 2, 3)
        ]

        forecast = build_forecast("courier", {"shifts": shifts}, now=self.now)

        self.assertEqual(forecast["status"], "available")
        self.assertEqual(forecast["items"][0]["district"], self.district)
        self.assertEqual(forecast["items"][0]["net_income_per_hour"], 80)
        self.assertEqual(forecast["items"][0]["score"], 100)
        self.assertEqual(forecast["items"][1]["score"], 0)

    def test_courier_trend_compares_recent_and_previous_45_day_periods(self):
        shifts = [
            {
                "district": self.district,
                "gross_income": income,
                "hours_worked": 10,
                "timestamp": (self.now - timedelta(days=day)).isoformat(),
            }
            for day, income in ((5, 1000), (15, 1000), (50, 500), (60, 500))
        ]

        forecast = build_forecast("courier", {"shifts": shifts}, now=self.now)

        item = forecast["items"][0]
        self.assertEqual(item["trend"], "rising")
        self.assertEqual(item["recent_samples"], 2)
        self.assertEqual(item["historical_samples"], 2)
        self.assertEqual(item["net_income_per_hour"], 100)
        self.assertEqual(item["historical_net_income_per_hour"], 50)

    def test_courier_forecast_matches_vehicle_and_shift_start_time(self):
        matching_shifts = [
            {
                "district": self.district,
                "gross_income": 1200,
                "hours_worked": 10,
                "timestamp": (self.now - timedelta(days=day)).isoformat(),
                "started_at": (
                    self.now - timedelta(days=day) + timedelta(minutes=30)
                ).isoformat(),
                "vehicle_type": "bicycle",
            }
            for day in (7, 14, 21)
        ]
        unrelated_shifts = [
            {
                "district": config.MOSCOW_DISTRICTS[1],
                "gross_income": 5000,
                "hours_worked": 10,
                "timestamp": (self.now - timedelta(days=7)).isoformat(),
                "started_at": (
                    self.now - timedelta(days=7) + timedelta(hours=8)
                ).isoformat(),
                "vehicle_type": "bicycle",
            },
            {
                "district": config.MOSCOW_DISTRICTS[2],
                "gross_income": 5000,
                "hours_worked": 10,
                "timestamp": (self.now - timedelta(days=1)).isoformat(),
                "vehicle_type": "car",
            },
        ]

        forecast = build_forecast(
            "courier",
            {
                "shifts": matching_shifts + unrelated_shifts,
                "vehicle_type": "bicycle",
            },
            now=self.now,
        )

        self.assertTrue(forecast["horizon_supported"])
        self.assertEqual(forecast["transport_basis"], "matched")
        self.assertEqual(forecast["transport_type"], "bicycle")
        personal_items = [
            item for item in forecast["items"] if item["sample_count"] > 0
        ]
        self.assertEqual([item["district"] for item in personal_items], [self.district])
        self.assertEqual(personal_items[0]["net_income_per_hour"], 120)

    def test_empty_inputs_return_explanation_without_recommendations(self):
        taxi = build_forecast("taxi", {"reports": []}, now=self.now)
        courier = build_forecast("courier", {"shifts": []}, now=self.now)

        self.assertEqual(taxi["status"], "insufficient_data")
        self.assertTrue(taxi["empty_message"])
        self.assertEqual(courier["status"], "insufficient_data")
        self.assertTrue(courier["empty_message"])
        self.assertEqual(taxi["items"], [])
        self.assertEqual(courier["items"], [])


class CourierHintTests(unittest.TestCase):
    now = datetime(2026, 1, 7, 12, 0)

    def test_excluded_shifts_produce_explaining_hints(self):
        inputs = {
            "shifts": [],
            "excluded": {"no_district": 1, "no_hours": 2},
            "other_direction_shifts": 1,
        }
        forecast = build_forecast("courier", inputs, now=self.now)
        hints = forecast["hints"]
        self.assertEqual(len(hints), 3)
        self.assertTrue(any("без района" in hint for hint in hints))
        self.assertTrue(any("без часов" in hint for hint in hints))
        self.assertTrue(any("другом направлении" in hint for hint in hints))

    def test_no_hints_when_everything_counted(self):
        forecast = build_forecast("courier", {"shifts": []}, now=self.now)
        self.assertEqual(forecast["hints"], [])


if __name__ == "__main__":
    unittest.main()
