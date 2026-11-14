import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("BOT_TOKEN", "test-bot-token")

import config
import crowd_signals
from demand_forecasting import build_forecast

NOW = datetime(2026, 1, 7, 12, 0)


def stats(categories, reporters=2, age_minutes=0):
    return {
        "Арбат": {
            "report_count": len(categories) * reporters,
            "reporters": reporters,
            "report_categories": list(categories),
            "last_report_update": (NOW - timedelta(minutes=age_minutes)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }
    }


class FreshnessTests(unittest.TestCase):
    def test_fresh_report_has_full_weight(self):
        stamp = NOW.strftime("%Y-%m-%d %H:%M:%S")
        self.assertAlmostEqual(crowd_signals.freshness(stamp, NOW), 1.0)

    def test_half_life_halves_weight(self):
        stamp = (NOW - timedelta(minutes=crowd_signals.HALF_LIFE_MINUTES)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        self.assertAlmostEqual(crowd_signals.freshness(stamp, NOW), 0.5, places=6)

    def test_broken_or_missing_timestamp_has_no_weight(self):
        self.assertEqual(crowd_signals.freshness(None, NOW), 0.0)
        self.assertEqual(crowd_signals.freshness("мусор", NOW), 0.0)


class MultiplierTests(unittest.TestCase):
    def test_traffic_lowers_score(self):
        result = crowd_signals.district_multipliers(stats(["traffic"]), NOW)
        self.assertLess(result["Арбат"], 1.0)

    def test_high_demand_raises_score(self):
        result = crowd_signals.district_multipliers(stats(["high_demand"]), NOW)
        self.assertGreater(result["Арбат"], 1.0)

    def test_effect_is_capped(self):
        many = list(crowd_signals.CATEGORY_EFFECT) * 5
        result = crowd_signals.district_multipliers(stats(many, reporters=9), NOW)
        self.assertGreaterEqual(result["Арбат"], 1 - crowd_signals.MAX_EFFECT - 1e-9)
        self.assertLessEqual(result["Арбат"], 1 + crowd_signals.MAX_EFFECT + 1e-9)

    def test_single_reporter_is_weaker_than_confirmed(self):
        weak = crowd_signals.district_multipliers(stats(["accident"], reporters=1), NOW)
        strong = crowd_signals.district_multipliers(stats(["accident"], reporters=4), NOW)
        self.assertGreater(weak["Арбат"], strong["Арбат"])

    def test_pickup_delay_does_not_corroborate_demand_reports(self):
        stamp = NOW.strftime("%Y-%m-%d %H:%M:%S")
        mixed = {
            "Арбат": {
                "reporters": 2,
                "report_categories": ["high_demand", "pickup_delay"],
                "last_report_update": stamp,
                "category_details": {
                    "high_demand": {
                        "reporters": 1,
                        "last_report_update": stamp,
                    },
                    "pickup_delay": {
                        "reporters": 1,
                        "last_report_update": stamp,
                    },
                },
            }
        }
        demand_only = {
            "Арбат": {
                "reporters": 1,
                "report_categories": ["high_demand"],
                "last_report_update": stamp,
            }
        }
        self.assertEqual(
            crowd_signals.district_multipliers(mixed, NOW),
            crowd_signals.district_multipliers(demand_only, NOW),
        )

    def test_old_report_decays_and_disappears(self):
        old = crowd_signals.district_multipliers(
            stats(["road_closure"], age_minutes=60 * 24), NOW
        )
        self.assertEqual(old, {})

    def test_unknown_category_is_ignored(self):
        self.assertEqual(
            crowd_signals.district_multipliers(stats(["что_то_новое"]), NOW), {}
        )

    def test_empty_stats_are_safe(self):
        self.assertEqual(crowd_signals.district_multipliers({}, NOW), {})

    def test_describe_is_human_readable(self):
        self.assertIn("выше", crowd_signals.describe(1.05))
        self.assertIn("хуже", crowd_signals.describe(0.95))
        self.assertIn("учтены", crowd_signals.describe(1.0))


class ForecastIntegrationTests(unittest.TestCase):
    def _inputs(self):
        return {
            "reports": [
                {
                    "district": "Арбат",
                    "coefficient": 3.0,
                    "timestamp": (NOW - timedelta(minutes=10)).isoformat(),
                    "user_id": 1,
                }
            ]
        }

    def test_reports_factor_changes_score_and_is_reported(self):
        plain = build_forecast("taxi", self._inputs(), now=NOW)
        worse = build_forecast(
            "taxi", self._inputs(), now=NOW, factors={"reports": {"Арбат": 0.93}}
        )
        better = build_forecast(
            "taxi", self._inputs(), now=NOW, factors={"reports": {"Арбат": 1.07}}
        )
        base = plain["items"][0]["score"]
        self.assertLess(worse["items"][0]["score"], base)
        self.assertGreater(better["items"][0]["score"], base)
        self.assertTrue(better["factors"]["reports"])
        self.assertFalse(plain["factors"]["reports"])
        # базовый балл не искажается факторами
        self.assertEqual(better["items"][0]["score_base"], base)


if __name__ == "__main__":
    unittest.main()
