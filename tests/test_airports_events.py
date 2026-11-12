import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "test-bot-token")

import airports
import events
from demand_forecasting import build_forecast

NOW = datetime(2026, 1, 7, 12, 0)

# Центр рядом с Шереметьево и центр в стороне
CENTERS = {
    "Молжаниновский": {"lat": 55.9300, "lon": 37.4000},
    "Арбат": {"lat": 55.7520, "lon": 37.5920},
}
LUZHNIKI = {"Лужники": {"lat": 55.7158, "lon": 37.5531}, "Арбат": CENTERS["Арбат"]}


class AirportPressureTests(unittest.TestCase):
    def test_arrivals_inside_window_raise_pressure(self):
        # 20 рейсов за час — заметно выше фона в 8
        arrivals = [
            {"airport": "SVO", "time": (NOW + timedelta(minutes=3 * i)).isoformat()}
            for i in range(20)
        ]
        pressure = airports.airport_pressure(arrivals, NOW)
        self.assertGreater(pressure["SVO"], 0.5)
        self.assertEqual(pressure["DME"], 0.0)

    def test_arrivals_below_baseline_produce_no_pressure(self):
        arrivals = [
            {"airport": "SVO", "time": (NOW + timedelta(minutes=5 * i)).isoformat()}
            for i in range(3)
        ]
        self.assertEqual(airports.airport_pressure(arrivals, NOW)["SVO"], 0.0)

    def test_arrivals_outside_window_and_unknown_airport_ignored(self):
        arrivals = [
            {"airport": "SVO", "time": (NOW + timedelta(hours=5)).isoformat()},
            {"airport": "XXX", "time": NOW.isoformat()},
            {"airport": "SVO", "time": "не дата"},
        ]
        self.assertEqual(airports.airport_pressure(arrivals, NOW)["SVO"], 0.0)

    def test_near_airport_district_gets_bonus_only(self):
        impacts = airports.district_impacts({"SVO": 1.0}, CENTERS)
        self.assertIn("Молжаниновский", impacts)
        self.assertNotIn("Арбат", impacts)
        self.assertLessEqual(impacts["Молжаниновский"], airports.MAX_BONUS)

    def test_far_districts_ignored_for_all_airports(self):
        self.assertEqual(airports.district_impacts({"SVO": 1.0, "DME": 1.0}, {"Арбат": CENTERS["Арбат"]}), {})

    def test_no_source_configured_means_no_impacts(self):
        self.assertEqual(airports.fetch_arrivals(), [])


class EventImpactTests(unittest.TestCase):
    def _event(self, start_offset_hours: float, hours: float = 3, attendance: int = 30000):
        start = NOW + timedelta(hours=start_offset_hours)
        return {
            "title": "Тест",
            "lat": LUZHNIKI["Лужники"]["lat"],
            "lon": LUZHNIKI["Лужники"]["lon"],
            "start": start.isoformat(),
            "end": (start + timedelta(hours=hours)).isoformat(),
            "attendance": attendance,
        }

    def test_event_in_progress_has_effect(self):
        impacts = events.event_impacts([self._event(0)], NOW, LUZHNIKI)
        self.assertIn("Лужники", impacts)
        self.assertNotIn("Арбат", impacts)
        self.assertLessEqual(impacts["Лужники"], events.MAX_BONUS)

    def test_event_far_in_future_is_ignored(self):
        self.assertEqual(events.event_impacts([self._event(20)], NOW, LUZHNIKI), {})

    def test_event_long_finished_is_ignored(self):
        self.assertEqual(events.event_impacts([self._event(-10)], NOW, LUZHNIKI), {})

    def test_bigger_attendance_gives_bigger_bonus(self):
        small = events.event_impacts([self._event(0, attendance=1000)], NOW, LUZHNIKI)
        big = events.event_impacts([self._event(0, attendance=40000)], NOW, LUZHNIKI)
        self.assertGreater(big["Лужники"], small["Лужники"])

    def test_missing_file_and_broken_json_are_safe(self):
        base = Path(__file__).resolve().parent
        self.assertEqual(events.load_events(base / "no-such-events.json"), [])
        broken = base / "broken-events.json"
        try:
            broken.write_text("{не json", encoding="utf-8")
            self.assertEqual(events.load_events(broken), [])
        finally:
            broken.unlink(missing_ok=True)

    def test_real_events_file_is_valid(self):
        self.assertIsInstance(events.load_events(), list)


class LocationFactorIntegrationTests(unittest.TestCase):
    def _taxi_inputs(self):
        return {
            "reports": [
                {
                    "district": "Арбат",
                    "coefficient": 2.0,
                    "timestamp": (NOW - timedelta(minutes=15)).isoformat(),
                    "user_id": 1,
                }
            ]
        }

    def test_airport_bonus_raises_district_score(self):
        plain = build_forecast("taxi", self._taxi_inputs(), now=NOW)
        boosted = build_forecast(
            "taxi", self._taxi_inputs(), now=NOW, factors={"airports": {"Арбат": 0.15}}
        )
        self.assertGreater(
            boosted["items"][0]["score"], plain["items"][0]["score"]
        )
        self.assertEqual(boosted["items"][0]["score_base"], plain["items"][0]["score"])
        self.assertTrue(boosted["factors"]["airports"])

    def test_event_bonus_is_capped_and_reported(self):
        forecast = build_forecast(
            "taxi",
            self._taxi_inputs(),
            now=NOW,
            factors={"events": {"Арбат": 5.0}},
        )
        self.assertLessEqual(forecast["items"][0]["score"], 100)
        self.assertTrue(forecast["factors"]["events"])

    def test_empty_maps_do_not_change_scores(self):
        plain = build_forecast("taxi", self._taxi_inputs(), now=NOW)
        empty = build_forecast(
            "taxi", self._taxi_inputs(), now=NOW, factors={"airports": {}, "events": {}}
        )
        self.assertEqual(plain["items"][0]["score"], empty["items"][0]["score"])


if __name__ == "__main__":
    unittest.main()
