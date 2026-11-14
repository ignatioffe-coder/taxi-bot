import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("BOT_TOKEN", "test-bot-token")

import config
import tomtom_traffic
from demand_forecasting import build_forecast


class TomTomTrafficTests(unittest.TestCase):
    def setUp(self):
        tomtom_traffic._cache.update({"value": None, "at": 0.0})

    def tearDown(self):
        tomtom_traffic._cache.update({"value": None, "at": 0.0})

    def test_incidents_are_normalized_and_mapped_to_nearby_districts(self):
        incidents = tomtom_traffic.normalize_incidents({
            "incidents": [{
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[37.5922, 55.7509], [37.593, 55.751]],
                },
                "properties": {
                    "iconCategory": "jam",
                    "magnitudeOfDelay": "major",
                },
            }],
        })

        impacts = tomtom_traffic.district_impacts(
            incidents,
            {
                "Арбат": {"lat": 55.7509313, "lon": 37.5921517},
                "Куркино": {"lat": 55.8919009, "lon": 37.3963609},
            },
        )

        self.assertAlmostEqual(impacts["Арбат"], 0.08, places=3)
        self.assertNotIn("Куркино", impacts)

    @patch("tomtom_traffic.requests.get")
    def test_fetch_uses_auth_header_and_reuses_twenty_minute_cache(self, get):
        response = Mock()
        response.json.return_value = {"incidents": []}
        get.return_value = response

        with patch.object(config, "TOMTOM_TRAFFIC_API_KEY", "private-test-key"):
            self.assertEqual(tomtom_traffic.fetch_incidents(), [])
            self.assertEqual(tomtom_traffic.fetch_incidents(), [])

        get.assert_called_once()
        self.assertEqual(
            get.call_args.kwargs["headers"]["TomTom-Api-Key"], "private-test-key"
        )
        self.assertNotIn("key", get.call_args.kwargs["params"])
        self.assertEqual(get.call_args.kwargs["params"]["timeValidity"], "present")

    @patch("tomtom_traffic.requests.get")
    def test_fetch_is_disabled_without_api_key(self, get):
        with patch.object(config, "TOMTOM_TRAFFIC_API_KEY", ""):
            self.assertIsNone(tomtom_traffic.fetch_incidents())
        get.assert_not_called()

    def test_traffic_penalty_is_applied_and_reported_in_forecast(self):
        now = config.get_moscow_time()
        inputs = {
            "reports": [{
                "district": "Арбат",
                "coefficient": 2.0,
                "timestamp": now.isoformat(),
                "user_id": 1,
            }],
        }
        plain = build_forecast("taxi", inputs, now=now)
        traffic = build_forecast(
            "taxi", inputs, now=now, factors={"traffic": {"Арбат": 0.1}}
        )

        self.assertLess(traffic["items"][0]["score"], plain["items"][0]["score"])
        self.assertTrue(traffic["factors"]["traffic"])
        self.assertIn("TomTom", traffic["note"])


if __name__ == "__main__":
    unittest.main()
