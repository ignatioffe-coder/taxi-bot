import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("BOT_TOKEN", "test-bot-token")

import config
import external_factors
from demand_forecasting import build_forecast

DENSITY = {
    "Тверской": {"food": 100, "shop": 10, "education": 1, "service": 5},
    "Арбат": {"food": 10, "shop": 5, "education": 0, "service": 1},
}
RAIN = {"impact": 1.0, "text": "Сильный дождь", "emoji": "🌧️", "temperature": 2.0}


class WeatherDescriptionTests(unittest.TestCase):
    def test_known_code_returns_russian_text_and_impact(self):
        text, emoji, impact = external_factors.describe_weather(65)
        self.assertEqual(text, "Сильный дождь")
        self.assertEqual(emoji, "🌧️")
        self.assertAlmostEqual(impact, 0.8)

    def test_clear_weather_has_no_impact(self):
        text, _, impact = external_factors.describe_weather(0)
        self.assertEqual(text, "Ясно")
        self.assertEqual(impact, 0.0)

    def test_unknown_and_missing_codes_fall_back(self):
        self.assertEqual(external_factors.describe_weather(None)[0], "нет данных")
        self.assertEqual(external_factors.describe_weather(12345)[0], "Неизвестно")


class PoiDensityTests(unittest.TestCase):
    def test_scores_are_normalised_to_rank(self):
        scores = external_factors.poi_scores(DENSITY)
        self.assertAlmostEqual(scores["Тверской"], 1.0)
        self.assertAlmostEqual(scores["Арбат"], 0.5)

    def test_empty_density_returns_empty_mapping(self):
        self.assertEqual(external_factors.poi_scores({}), {})

    def test_all_zero_density_returns_empty_mapping(self):
        empty = {"Район": {"food": 0, "shop": 0, "education": 0, "service": 0}}
        self.assertEqual(external_factors.poi_scores(empty), {})


class ForecastFactorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 7, 10, 30)
        self.district = "Тверской"

    def _taxi_inputs(self, coefficient=2.0):
        return {
            "reports": [
                {
                    "district": self.district,
                    "coefficient": coefficient,
                    "timestamp": (self.now - timedelta(minutes=20)).isoformat(),
                    "user_id": 1,
                }
            ]
        }

    def test_taxi_forecast_without_factors_is_unchanged(self):
        forecast = build_forecast("taxi", self._taxi_inputs(), now=self.now)
        item = forecast["items"][0]
        self.assertEqual(item["score"], item["score_base"])
        self.assertFalse(forecast["factors"]["weather"])
        self.assertIsNone(forecast["weather"])
        self.assertIn("Погода сейчас недоступна", forecast["note"])

    def test_rain_increases_taxi_score_and_keeps_base(self):
        dry = build_forecast("taxi", self._taxi_inputs(), now=self.now)
        wet = build_forecast(
            "taxi", self._taxi_inputs(), now=self.now, factors={"weather": RAIN}
        )
        self.assertGreater(wet["items"][0]["score"], dry["items"][0]["score"])
        self.assertEqual(wet["items"][0]["score_base"], dry["items"][0]["score"])
        self.assertTrue(wet["factors"]["weather"])
        self.assertIn("Сильный дождь", wet["note"])

    def test_score_never_exceeds_100_after_weather_bonus(self):
        forecast = build_forecast(
            "taxi",
            self._taxi_inputs(coefficient=5.0),
            now=self.now,
            factors={"weather": RAIN},
        )
        self.assertLessEqual(forecast["items"][0]["score"], 100)

    def test_courier_without_shifts_uses_poi_baseline(self):
        forecast = build_forecast(
            "courier", {"shifts": []}, now=self.now, factors={"poi_density": DENSITY}
        )
        self.assertEqual(forecast["status"], "baseline")
        self.assertEqual(forecast["method"], "poi_baseline")
        self.assertEqual(len(forecast["items"]), 2)
        self.assertEqual(forecast["items"][0]["district"], "Тверской")
        self.assertEqual(forecast["items"][0]["metric_label"], "плотность заведений")
        self.assertEqual(forecast["items"][0]["confidence"], 25)

    def test_courier_without_shifts_or_poi_stays_insufficient(self):
        forecast = build_forecast("courier", {"shifts": []}, now=self.now)
        self.assertEqual(forecast["status"], "insufficient_data")
        self.assertEqual(forecast["items"], [])

    def test_courier_poi_baseline_respects_weather(self):
        dry = build_forecast(
            "courier", {"shifts": []}, now=self.now, factors={"poi_density": DENSITY}
        )
        wet = build_forecast(
            "courier",
            {"shifts": []},
            now=self.now,
            factors={"poi_density": DENSITY, "weather": RAIN},
        )
        # Берём не топовый район: у лидера балл уже 100 и расти некуда.
        dry_arbat = next(i for i in dry["items"] if i["district"] == "Арбат")
        wet_arbat = next(i for i in wet["items"] if i["district"] == "Арбат")
        self.assertGreater(wet_arbat["score"], dry_arbat["score"])
        self.assertEqual(wet_arbat["score_base"], dry_arbat["score"])

    def test_courier_history_still_wins_over_baseline(self):
        shifts = [
            {
                "district": self.district,
                "gross_income": 900,
                "hours_worked": 10,
                "timestamp": (self.now - timedelta(days=day)).isoformat(),
            }
            for day in (1, 2, 3)
        ]
        forecast = build_forecast(
            "courier",
            {"shifts": shifts},
            now=self.now,
            factors={"poi_density": DENSITY},
        )
        self.assertEqual(forecast["status"], "available")
        self.assertNotEqual(forecast.get("method"), "poi_baseline")
        self.assertEqual(forecast["items"][0]["net_income_per_hour"], 90)


class LiveFactorsTests(unittest.TestCase):
    def test_live_factors_have_expected_shape(self):
        factors = external_factors.load_live_factors()
        self.assertIn("weather", factors)
        self.assertIn("poi_density", factors)
        self.assertIsInstance(factors["poi_density"], dict)

    def test_real_density_snapshot_covers_moscow_districts(self):
        density = external_factors.load_poi_density()
        self.assertTrue(density, "ожидался снимок static/district-poi.json")
        self.assertIn("Тверской", density)
        scores = external_factors.poi_scores(density)
        self.assertGreater(scores["Тверской"], scores["Арбат"])


if __name__ == "__main__":
    unittest.main()
