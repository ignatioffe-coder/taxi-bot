import os
import sqlite3
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "test-bot-token")

import config
import database

NOW = datetime(2026, 1, 7, 18, 0)
STAMP = "2026-01-07 12:00:00"


class AdminQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_db = config.DB_PATH
        cls.db_path = Path(__file__).resolve().parent / "_quality_test.db"
        cls.db_path.unlink(missing_ok=True)
        config.DB_PATH = str(cls.db_path)
        database.init_db()

    @classmethod
    def tearDownClass(cls):
        config.DB_PATH = cls.original_db
        cls.db_path.unlink(missing_ok=True)

    def setUp(self):
        with closing(sqlite3.connect(config.DB_PATH)) as conn, conn:
            conn.execute("DELETE FROM forecast_evaluations")
            conn.execute("DELETE FROM forecast_runs")
            conn.execute("DELETE FROM coefficients")
            conn.execute("DELETE FROM work_shifts")

    def _seed_forecasts(self):
        with closing(sqlite3.connect(config.DB_PATH)) as conn, conn:
            conn.execute(
                """
                INSERT INTO forecast_runs
                    (user_id, work_type, generated_at, horizon_minutes, status)
                VALUES (1, 'taxi', ?, 60, 'evaluated')
                """,
                (STAMP,),
            )
            run_id = conn.execute("SELECT id FROM forecast_runs").fetchone()[0]
            for district, error in (("Арбат", 0.4), ("Тверской", 0.1)):
                conn.execute(
                    """
                    INSERT INTO forecast_evaluations
                        (run_id, district, predicted_score, confidence, metric_type,
                         predicted_metric, observed_metric, absolute_error,
                         status, evaluated_at)
                    VALUES (?, ?, 50, 40, 'coefficient', 2.0, ?, ?, 'evaluated', ?)
                    """,
                    (run_id, district, 2.0 + error, error, STAMP),
                )
            # Нерассчитанный прогноз не должен попадать в выборку
            conn.execute(
                """
                INSERT INTO forecast_evaluations
                    (run_id, district, predicted_score, confidence, metric_type,
                     predicted_metric, status)
                VALUES (?, 'Сокольники', 30, 20, 'coefficient', 1.5, 'pending')
                """,
                (run_id,),
            )

    def test_quality_by_district_sorts_worst_first(self):
        self._seed_forecasts()
        rows = database.get_forecast_quality_by_district("taxi")
        self.assertEqual([row["district"] for row in rows], ["Арбат", "Тверской"])
        self.assertEqual(rows[0]["mean_absolute_error"], 0.4)
        self.assertEqual(rows[0]["evaluated"], 1)

    def test_quality_by_district_respects_work_type(self):
        self._seed_forecasts()
        self.assertEqual(database.get_forecast_quality_by_district("courier"), [])

    def test_quality_rejects_unknown_work_type(self):
        with self.assertRaises(ValueError):
            database.get_forecast_quality_by_district("bike")

    def test_coverage_counts_only_districts_with_data(self):
        with closing(sqlite3.connect(config.DB_PATH)) as conn, conn:
            conn.executemany(
                """
                INSERT INTO coefficients (user_id, district, coefficient, timestamp)
                VALUES (1, ?, 2.0, ?)
                """,
                [("Арбат", STAMP), ("Тверской", STAMP), ("Арбат", STAMP)],
            )
        coverage = database.get_district_coverage("taxi", now=NOW)
        self.assertEqual(coverage["total"], len(config.MOSCOW_DISTRICTS))
        self.assertEqual(coverage["covered"], 2)
        self.assertGreater(coverage["share"], 0)

    def test_coverage_ignores_old_data(self):
        with closing(sqlite3.connect(config.DB_PATH)) as conn, conn:
            conn.execute(
                """
                INSERT INTO coefficients (user_id, district, coefficient, timestamp)
                VALUES (1, 'Арбат', 2.0, '2025-01-01 12:00:00')
                """
            )
        self.assertEqual(database.get_district_coverage("taxi", now=NOW)["covered"], 0)

    def test_coverage_for_courier_requires_hours(self):
        with closing(sqlite3.connect(config.DB_PATH)) as conn, conn:
            conn.executemany(
                """
                INSERT INTO work_shifts
                    (user_id, work_type, district, hours_worked, timestamp)
                VALUES (1, 'courier', ?, ?, ?)
                """,
                [("Арбат", 4, STAMP), ("Тверской", 0, STAMP)],
            )
        coverage = database.get_district_coverage("courier", now=NOW)
        self.assertEqual(coverage["covered"], 1)

    def test_admin_summary_includes_quality_blocks(self):
        self._seed_forecasts()
        summary = database.get_admin_summary(now=int(NOW.timestamp()))
        self.assertIn("district_coverage", summary)
        self.assertIn("forecast_quality_by_district", summary)
        self.assertEqual(
            summary["forecast_quality_by_district"]["taxi"][0]["district"], "Арбат"
        )


if __name__ == "__main__":
    unittest.main()
