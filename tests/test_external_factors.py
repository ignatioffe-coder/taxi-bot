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

    def test_single_shift_is_blended_with_baseline(self):
        shifts = [{
            "district": "Арбат",
            "gross_income": 900,
            "hours_worked": 10,
            "timestamp": (self.now - timedelta(days=1)).isoformat(),
        }]
        forecast = build_forecast(
            "courier",
            {"shifts": shifts},
            now=self.now,
            factors={"poi_density": DENSITY},
        )
        # Первым идёт район из личной истории, остальные — стартовая оценка:
        # одна смена не должна скрывать остальные районы
        self.assertGreater(len(forecast["items"]), 1)
        self.assertEqual(forecast["items"][0]["district"], "Арбат")
        self.assertNotEqual(forecast["items"][0].get("method"), "poi_baseline")
        self.assertTrue(any(i.get("method") == "poi_baseline" for i in forecast["items"]))
        self.assertIn("стартовая оценка", forecast["note"])

    def test_rich_history_does_not_need_baseline(self):
        districts = list(config.MOSCOW_DISTRICTS)[:6]
        shifts = [
            {
                "district": district,
                "gross_income": 600,
                "hours_worked": 10,
                "timestamp": (self.now - timedelta(days=1)).isoformat(),
            }
            for district in districts
        ]
        forecast = build_forecast(
            "courier",
            {"shifts": shifts},
            now=self.now,
            factors={"poi_density": DENSITY},
        )
        self.assertEqual(len(forecast["items"]), 6)
        self.assertFalse(
            any(i.get("method") == "poi_baseline" for i in forecast["items"])
        )


class HorizonTests(unittest.TestCase):
    def setUp(self):
        self.taxi_inputs = lambda now: {
            "reports": [
                {
                    "district": "Тверской",
                    "coefficient": 2.0,
                    "timestamp": (now - timedelta(minutes=15)).isoformat(),
                    "user_id": 1,
                }
            ]
        }

    def test_item_has_all_three_horizons_and_60_matches_score(self):
        now = datetime(2026, 1, 7, 10, 30)
        forecast = build_forecast("taxi", self.taxi_inputs(now), now=now)
        item = forecast["items"][0]
        self.assertEqual(set(item["horizons"]), {"30", "60", "120"})
        self.assertEqual(item["horizons"]["60"], item["score"])
        self.assertEqual(forecast["horizons_minutes"], [30, 60, 120])
        for value in item["horizons"].values():
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, 100)

    def test_morning_taxi_forecast_grows_towards_rush_hour(self):
        # 06:30 -> через два часа 08:30, утренний пик в профиле выше
        now = datetime(2026, 1, 7, 6, 30)
        item = build_forecast("taxi", self.taxi_inputs(now), now=now)["items"][0]
        self.assertGreater(item["horizons"]["120"], item["score"])

    def test_courier_baseline_grows_towards_lunch(self):
        now = datetime(2026, 1, 7, 11, 30)  # через 30 минут — 12:00, обеденный пик
        forecast = build_forecast(
            "courier", {"shifts": []}, now=now, factors={"poi_density": DENSITY}
        )
        arbat = next(i for i in forecast["items"] if i["district"] == "Арбат")
        self.assertGreater(arbat["horizons"]["30"], arbat["score"])


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
