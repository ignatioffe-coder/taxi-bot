import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("BOT_TOKEN", "test-bot-token")

import notifications


def forecast_with(items):
    return {"items": items}


class QuietHoursTests(unittest.TestCase):
    def test_clock_parsing(self):
        self.assertEqual(notifications._clock_minutes("22:30"), 1350)
        self.assertEqual(notifications._clock_minutes("00:00"), 0)
        self.assertIsNone(notifications._clock_minutes(""))
        self.assertIsNone(notifications._clock_minutes(None))
        self.assertIsNone(notifications._clock_minutes("25:00"))
        self.assertIsNone(notifications._clock_minutes("abc"))

    def test_window_inside_one_day(self):
        now = datetime(2026, 1, 7, 13, 30)
        self.assertTrue(notifications.in_quiet_hours(now, "13:00", "14:00"))
        self.assertFalse(notifications.in_quiet_hours(now, "14:00", "15:00"))

    def test_window_crossing_midnight(self):
        self.assertTrue(
            notifications.in_quiet_hours(datetime(2026, 1, 7, 23, 0), "22:00", "08:00")
        )
        self.assertTrue(
            notifications.in_quiet_hours(datetime(2026, 1, 7, 3, 0), "22:00", "08:00")
        )
        self.assertFalse(
            notifications.in_quiet_hours(datetime(2026, 1, 7, 12, 0), "22:00", "08:00")
        )

    def test_empty_or_equal_bounds_disable_quiet_hours(self):
        now = datetime(2026, 1, 7, 12, 0)
        self.assertFalse(notifications.in_quiet_hours(now, "", ""))
        self.assertFalse(notifications.in_quiet_hours(now, "12:00", "12:00"))


class FrequencyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 7, 12, 0)

    def test_first_notification_is_always_due(self):
        self.assertTrue(notifications.is_due("daily", self.now, None))

    def test_immediate_waits_fifteen_minutes(self):
        recent = self.now - timedelta(minutes=10)
        old = self.now - timedelta(minutes=20)
        self.assertFalse(notifications.is_due("immediate", self.now, recent))
        self.assertTrue(notifications.is_due("immediate", self.now, old))

    def test_daily_waits_a_day(self):
        self.assertFalse(
            notifications.is_due("daily", self.now, self.now - timedelta(hours=23))
        )
        self.assertTrue(
            notifications.is_due("daily", self.now, self.now - timedelta(hours=25))
        )

    def test_missing_frequency_defaults_to_daily(self):
        self.assertFalse(
            notifications.is_due(None, self.now, self.now - timedelta(hours=2))
        )


class MessageTests(unittest.TestCase):
    def test_no_leaders_means_no_message(self):
        forecast = forecast_with([{"district": "Арбат", "score": 40}])
        self.assertIsNone(notifications.build_demand_message("taxi", forecast))
        self.assertIsNone(notifications.build_demand_message("courier", {"items": []}))

    def test_message_lists_leaders_and_mentions_horizon(self):
        forecast = forecast_with([
            {"district": "Тверской", "score": 80, "horizons": {"30": 88}},
            {"district": "Арбат", "score": 65, "horizons": {"30": 66}},
        ])
        text = notifications.build_demand_message("taxi", forecast)
        self.assertIn("Тверской — 80", text)
        self.assertIn("через 30 мин: 88", text)
        self.assertIn("Арбат — 65", text)
        self.assertNotIn("через 30 мин: 66", text)  # разница слишком мала

    def test_courier_message_says_delivery(self):
        forecast = forecast_with([{"district": "Тверской", "score": 90}])
        self.assertIn("доставку", notifications.build_demand_message("courier", forecast))

    def test_weather_message_only_for_strong_impact(self):
        strong = {"impact": 0.9, "text": "Дождь", "emoji": "🌧️"}
        weak = {"impact": 0.1, "text": "Ясно", "emoji": "☀️"}
        # В тексте состояние идёт строчной буквой посреди предложения
        self.assertIn("дождь", notifications.build_weather_message("courier", strong))
        self.assertIsNone(notifications.build_weather_message("courier", weak))
        self.assertIsNone(notifications.build_weather_message("courier", None))


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.forecast = forecast_with([{"district": "Тверской", "score": 90}])
        self.weather = {"impact": 0.9, "text": "Дождь", "emoji": "🌧️"}

    def test_all_disabled_means_nothing_to_send(self):
        recipient = {"work_type": "taxi", "demand_changes": False,
                     "promising_areas": False, "weather_changes": False}
        self.assertEqual(
            notifications.collect_messages(recipient, self.forecast, self.weather), []
        )

    def test_demand_flag_produces_demand_message(self):
        recipient = {"work_type": "taxi", "demand_changes": True,
                     "promising_areas": False, "weather_changes": False}
        messages = notifications.collect_messages(recipient, self.forecast, self.weather)
        self.assertEqual([kind for kind, _ in messages], ["demand"])

    def test_weather_flag_produces_weather_message(self):
        recipient = {"work_type": "courier", "demand_changes": False,
                     "promising_areas": False, "weather_changes": True}
        messages = notifications.collect_messages(recipient, self.forecast, self.weather)
        self.assertEqual([kind for kind, _ in messages], ["weather"])


class TrafficMessageTests(unittest.TestCase):
    NOW = datetime(2026, 1, 7, 12, 0)

    def _stats(self, categories, age_minutes=5):
        return {
            "Тверской": {
                "report_count": 2,
                "reporters": 2,
                "report_categories": list(categories),
                "last_report_update": (
                    self.NOW - timedelta(minutes=age_minutes)
                ).strftime("%Y-%m-%d %H:%M:%S"),
            }
        }

    def test_fresh_traffic_report_is_mentioned(self):
        text = notifications.build_traffic_message(self._stats(["traffic"]), self.NOW)
        self.assertIn("пробки", text)
        self.assertIn("Тверской", text)

    def test_accident_and_closure_have_human_labels(self):
        text = notifications.build_traffic_message(
            self._stats(["accident", "road_closure"]), self.NOW
        )
        self.assertIn("ДТП", text)
        self.assertIn("перекрытие", text)

    def test_old_report_is_not_mentioned(self):
        self.assertIsNone(
            notifications.build_traffic_message(
                self._stats(["traffic"], age_minutes=60 * 5), self.NOW
            )
        )

    def test_non_traffic_categories_are_ignored(self):
        self.assertIsNone(
            notifications.build_traffic_message(self._stats(["high_demand"]), self.NOW)
        )

    def test_empty_stats_are_safe(self):
        self.assertIsNone(notifications.build_traffic_message({}, self.NOW))
        self.assertIsNone(notifications.build_traffic_message(None, self.NOW))

    def test_traffic_flag_produces_traffic_message(self):
        recipient = {"work_type": "taxi", "demand_changes": False,
                     "promising_areas": False, "weather_changes": False,
                     "traffic_events": True}
        forecast = {"items": []}
        messages = notifications.collect_messages(
            recipient, forecast, None, self._stats(["traffic"]), self.NOW
        )
        self.assertEqual([kind for kind, _ in messages], ["traffic"])

    def test_without_now_no_traffic_message(self):
        recipient = {"work_type": "taxi", "traffic_events": True}
        messages = notifications.collect_messages(
            recipient, {"items": []}, None, self._stats(["traffic"])
        )
        self.assertEqual(messages, [])


if __name__ == "__main__":
    unittest.main()
