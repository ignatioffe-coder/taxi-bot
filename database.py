"""Работа с базой данных"""

import sqlite3
import time
from contextlib import closing
from datetime import date, datetime, timedelta
from typing import List, Dict
import config
import subscriptions

DOCUMENT_FIELDS = {
    "medical_book_expiry": ("medical_book", "Медицинская книжка"),
    "license_expiry": ("license", "Водительское удостоверение"),
    "osago_expiry": ("osago", "ОСАГО"),
    "inspection_expiry": ("inspection", "Техосмотр"),
    "taxi_permit_expiry": ("taxi_permit", "Разрешение такси"),
}
REMINDER_OFFSETS = (7, 1, 0)
NOTIFICATION_BOOLEAN_FIELDS = (
    "demand_changes",
    "promising_areas",
    "weather_changes",
    "city_events",
    "traffic_events",
    "personal_recommendations",
    "financial_reminders",
)
REPORT_EXPIRY_MINUTES = {
    "airport_queue": 60,
    "low_demand": 60,
    "low_activity": 60,
    "high_demand": 120,
    "many_orders": 120,
    "long_wait": 120,
    "restaurant_busy": 120,
    "order_problem": 120,
    "delivery_problem": 120,
    "traffic": 180,
    "accident": 240,
    "road_closure": 360,
    "few_cars": 120,
}
FORECAST_SNAPSHOT_MIN_INTERVAL = timedelta(minutes=15)
TAXI_FORECAST_EVALUATION_DELAY = timedelta(hours=1)
TAXI_FORECAST_EVALUATION_WINDOW = timedelta(hours=1)
COURIER_FORECAST_EVALUATION_WINDOW = timedelta(days=7)


def get_moscow_time():
    """Возвращает текущее московское время (UTC+3)"""
    return config.get_moscow_time()


def init_db():
    """Создаёт таблицы, если их нет"""
    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS coefficients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            district TEXT NOT NULL,
            coefficient REAL NOT NULL,
            weather TEXT DEFAULT '',
            day_of_week INTEGER,
            hour INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            notes TEXT DEFAULT ''
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            preferred_name TEXT NOT NULL DEFAULT '',
            onboarding_complete INTEGER NOT NULL DEFAULT 1,
            joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen_at DATETIME,
            is_active INTEGER DEFAULT 1
        )
    """)
    user_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(users)").fetchall()
    }
    if "preferred_name" not in user_columns:
        cursor.execute(
            "ALTER TABLE users ADD COLUMN preferred_name TEXT NOT NULL DEFAULT ''"
        )
    if "onboarding_complete" not in user_columns:
        cursor.execute(
            "ALTER TABLE users ADD COLUMN onboarding_complete INTEGER NOT NULL DEFAULT 1"
        )
    if "last_seen_at" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_seen_at DATETIME")
    cursor.execute("""
        UPDATE users
        SET onboarding_complete = 0
        WHERE onboarding_complete = 1 AND trim(preferred_name) = ''
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            latitude REAL,
            longitude REAL,
            district TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS work_shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            work_type TEXT NOT NULL CHECK (work_type IN ('taxi', 'courier')),
            district TEXT,
            gross_income REAL NOT NULL DEFAULT 0,
            tips REAL NOT NULL DEFAULT 0,
            fuel_cost REAL NOT NULL DEFAULT 0,
            commission_cost REAL NOT NULL DEFAULT 0,
            maintenance_cost REAL NOT NULL DEFAULT 0,
            other_cost REAL NOT NULL DEFAULT 0,
            rental_cost REAL NOT NULL DEFAULT 0,
            insurance_cost REAL NOT NULL DEFAULT 0,
            tax_cost REAL NOT NULL DEFAULT 0,
            depreciation_cost REAL NOT NULL DEFAULT 0,
            charging_cost REAL NOT NULL DEFAULT 0,
            connectivity_cost REAL NOT NULL DEFAULT 0,
            hours_worked REAL NOT NULL DEFAULT 0,
            distance_km REAL NOT NULL DEFAULT 0,
            orders_count INTEGER NOT NULL DEFAULT 0,
            timestamp DATETIME NOT NULL
        )
    """)
    shift_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(work_shifts)").fetchall()
    }
    for column in (
        "rental_cost", "insurance_cost", "tax_cost", "depreciation_cost",
        "charging_cost", "connectivity_cost", "distance_km", "orders_count",
    ):
        if column not in shift_columns:
            column_type = "INTEGER" if column == "orders_count" else "REAL"
            cursor.execute(
                f"ALTER TABLE work_shifts ADD COLUMN {column} "
                f"{column_type} NOT NULL DEFAULT 0"
            )
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_work_shifts_user_time
        ON work_shifts (user_id, timestamp)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS driver_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            work_type TEXT NOT NULL CHECK (work_type IN ('taxi', 'courier')),
            category TEXT NOT NULL,
            district TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'hidden')),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_driver_reports_active
        ON driver_reports (work_type, status, expires_at, district)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_driver_reports_user_time
        ON driver_reports (user_id, created_at)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS forecast_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            work_type TEXT NOT NULL CHECK (work_type IN ('taxi', 'courier')),
            generated_at TEXT NOT NULL,
            horizon_minutes INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'evaluated'))
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_forecast_runs_status
        ON forecast_runs (status, work_type, generated_at)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS forecast_evaluations (
            run_id INTEGER NOT NULL,
            district TEXT NOT NULL,
            predicted_score INTEGER NOT NULL,
            confidence INTEGER NOT NULL,
            metric_type TEXT NOT NULL,
            predicted_metric REAL NOT NULL,
            observed_metric REAL,
            absolute_error REAL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'evaluated', 'insufficient_data')),
            evaluated_at TEXT,
            PRIMARY KEY (run_id, district)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS driver_profiles (
            user_id INTEGER NOT NULL,
            work_type TEXT NOT NULL DEFAULT 'taxi',
            vehicle_type TEXT NOT NULL DEFAULT '',
            vehicle_make TEXT NOT NULL DEFAULT '',
            vehicle_model TEXT NOT NULL DEFAULT '',
            vehicle_year INTEGER,
            fuel_type TEXT NOT NULL DEFAULT '',
            weekly_income_goal REAL NOT NULL DEFAULT 0,
            reminders_enabled INTEGER NOT NULL DEFAULT 1,
            medical_book_expiry TEXT,
            license_expiry TEXT,
            osago_expiry TEXT,
            inspection_expiry TEXT,
            taxi_permit_expiry TEXT,
            updated_at DATETIME NOT NULL,
            PRIMARY KEY (user_id, work_type)
        )
    """)
    profile_schema = cursor.execute(
        "PRAGMA table_info(driver_profiles)"
    ).fetchall()
    profile_primary_key = [
        row[1] for row in sorted(profile_schema, key=lambda item: item[5])
        if row[5]
    ]
    if profile_primary_key != ["user_id", "work_type"]:
        cursor.execute("ALTER TABLE driver_profiles RENAME TO driver_profiles_legacy")
        cursor.execute("""
            CREATE TABLE driver_profiles (
                user_id INTEGER NOT NULL,
                work_type TEXT NOT NULL DEFAULT 'taxi',
                vehicle_type TEXT NOT NULL DEFAULT '',
                vehicle_make TEXT NOT NULL DEFAULT '',
                vehicle_model TEXT NOT NULL DEFAULT '',
                vehicle_year INTEGER,
                fuel_type TEXT NOT NULL DEFAULT '',
                weekly_income_goal REAL NOT NULL DEFAULT 0,
                reminders_enabled INTEGER NOT NULL DEFAULT 1,
                medical_book_expiry TEXT,
                license_expiry TEXT,
                osago_expiry TEXT,
                inspection_expiry TEXT,
                taxi_permit_expiry TEXT,
                updated_at DATETIME NOT NULL,
                PRIMARY KEY (user_id, work_type)
            )
        """)
        cursor.execute("""
            INSERT INTO driver_profiles (
                user_id, work_type, vehicle_type, vehicle_make, vehicle_model,
                vehicle_year, fuel_type, weekly_income_goal, reminders_enabled,
                medical_book_expiry, license_expiry, osago_expiry,
                inspection_expiry, taxi_permit_expiry, updated_at
            )
            SELECT user_id, work_type, vehicle_type, vehicle_make, vehicle_model,
                   vehicle_year, fuel_type, weekly_income_goal, reminders_enabled,
                   medical_book_expiry, license_expiry, osago_expiry,
                   inspection_expiry, taxi_permit_expiry, updated_at
            FROM driver_profiles_legacy
        """)
        cursor.execute("DROP TABLE driver_profiles_legacy")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS document_reminder_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            document_key TEXT NOT NULL,
            expiry_date TEXT NOT NULL,
            days_before INTEGER NOT NULL,
            sent_at DATETIME NOT NULL,
            UNIQUE (user_id, document_key, expiry_date, days_before)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_subscriptions (
            user_id INTEGER PRIMARY KEY,
            trial_started_at INTEGER NOT NULL,
            trial_expires_at INTEGER NOT NULL,
            paid_until INTEGER NOT NULL DEFAULT 0,
            invoice_payload TEXT NOT NULL DEFAULT '',
            terms_accepted_at INTEGER,
            latest_charge_id TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS telegram_star_payments (
            telegram_payment_charge_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            invoice_payload TEXT NOT NULL,
            currency TEXT NOT NULL,
            total_amount INTEGER NOT NULL,
            subscription_expires_at INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payment_support_pending (
            user_id INTEGER PRIMARY KEY,
            requested_at INTEGER NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notification_preferences (
            user_id INTEGER PRIMARY KEY,
            demand_changes INTEGER NOT NULL DEFAULT 1,
            promising_areas INTEGER NOT NULL DEFAULT 1,
            weather_changes INTEGER NOT NULL DEFAULT 1,
            city_events INTEGER NOT NULL DEFAULT 1,
            traffic_events INTEGER NOT NULL DEFAULT 1,
            personal_recommendations INTEGER NOT NULL DEFAULT 1,
            financial_reminders INTEGER NOT NULL DEFAULT 1,
            frequency TEXT NOT NULL DEFAULT 'daily'
                CHECK (frequency IN ('immediate', 'hourly', 'daily')),
            quiet_start TEXT NOT NULL DEFAULT '',
            quiet_end TEXT NOT NULL DEFAULT ''
        )
    """)

    conn.commit()
    conn.close()


def get_subscription_access(user_id: int, now: int | None = None) -> Dict:
    """Create the one-time trial on first eligible use and return access state."""
    current_time = int(time.time()) if now is None else now
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO user_subscriptions (
                    user_id, trial_started_at, trial_expires_at, updated_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    user_id,
                    current_time,
                    current_time + subscriptions.TRIAL_SECONDS,
                    current_time,
                ),
            )
        row = conn.execute(
            """
            SELECT trial_started_at, trial_expires_at, paid_until
            FROM user_subscriptions
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

    trial_expires_at = row["trial_expires_at"]
    paid_until = row["paid_until"]
    if current_time < paid_until:
        status = "paid"
    elif current_time < trial_expires_at:
        status = "trial"
    else:
        status = "expired"
    return {
        "status": status,
        "active": status != "expired",
        "trial_started_at": row["trial_started_at"],
        "trial_expires_at": trial_expires_at,
        "paid_until": paid_until,
    }


def save_star_invoice(user_id: int, invoice_payload: str, now: int | None = None):
    current_time = int(time.time()) if now is None else now
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        with conn:
            updated = conn.execute(
                """
                UPDATE user_subscriptions
                SET invoice_payload = ?, terms_accepted_at = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (invoice_payload, current_time, current_time, user_id),
            )
            if updated.rowcount != 1:
                raise ValueError("Subscription trial has not been initialized")


def validate_star_invoice(
    user_id: int,
    invoice_payload: str,
    currency: str,
    total_amount: int,
) -> bool:
    if (
        currency != "XTR"
        or total_amount != subscriptions.MONTHLY_PRICE_STARS
        or not subscriptions.invoice_payload_belongs_to_user(invoice_payload, user_id)
    ):
        return False
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT invoice_payload, terms_accepted_at
            FROM user_subscriptions
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
    return bool(row and row[0] == invoice_payload and row[1])


def record_star_payment(
    user_id: int,
    invoice_payload: str,
    currency: str,
    total_amount: int,
    charge_id: str,
    subscription_expires_at: int,
    now: int | None = None,
) -> bool:
    current_time = int(time.time()) if now is None else now
    if (
        not charge_id
        or subscription_expires_at <= current_time
        or currency != "XTR"
        or total_amount != subscriptions.MONTHLY_PRICE_STARS
        or not subscriptions.invoice_payload_belongs_to_user(invoice_payload, user_id)
    ):
        return False

    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        with conn:
            existing = conn.execute(
                """
                SELECT user_id, invoice_payload, currency, total_amount,
                       subscription_expires_at
                FROM telegram_star_payments
                WHERE telegram_payment_charge_id = ?
                """,
                (charge_id,),
            ).fetchone()
            if existing:
                return existing == (
                    user_id,
                    invoice_payload,
                    currency,
                    total_amount,
                    subscription_expires_at,
                )
            subscription = conn.execute(
                """
                SELECT invoice_payload, terms_accepted_at
                FROM user_subscriptions
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if (
                not subscription
                or subscription[0] != invoice_payload
                or not subscription[1]
            ):
                return False
            inserted = conn.execute(
                """
                INSERT OR IGNORE INTO telegram_star_payments (
                    telegram_payment_charge_id, user_id, invoice_payload,
                    currency, total_amount, subscription_expires_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    charge_id,
                    user_id,
                    invoice_payload,
                    currency,
                    total_amount,
                    subscription_expires_at,
                    current_time,
                ),
            )
            if inserted.rowcount != 1:
                return False
            conn.execute(
                """
                UPDATE user_subscriptions
                SET paid_until = MAX(paid_until, ?),
                    latest_charge_id = ?,
                    updated_at = ?
                WHERE user_id = ?
                """,
                (subscription_expires_at, charge_id, current_time, user_id),
            )
    return True


def set_payment_support_pending(user_id: int, now: int | None = None):
    current_time = int(time.time()) if now is None else now
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO payment_support_pending (user_id, requested_at)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET requested_at = excluded.requested_at
                """,
                (user_id, current_time),
            )


def consume_payment_support_pending(user_id: int) -> bool:
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        with conn:
            deleted = conn.execute(
                "DELETE FROM payment_support_pending WHERE user_id = ?",
                (user_id,),
            )
    return deleted.rowcount == 1


def cancel_payment_support_pending(user_id: int) -> bool:
    return consume_payment_support_pending(user_id)


def add_coefficient(user_id: int, username: str, district: str, 
                    coefficient: float, weather: str = "", notes: str = "") -> bool:
    """Добавляет запись о коэффициенте"""
    conn = None
    try:
        now = get_moscow_time()  # ← ИСПРАВЛЕНО: московское время
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO coefficients 
            (user_id, username, district, coefficient, weather, day_of_week, hour, notes, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, username, district, coefficient, weather, 
              now.weekday(), now.hour, notes, now.strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        return True
    except Exception as e:
        print(f"Ошибка добавления коэффициента: {e}")
        return False
    finally:
        # ИСПРАВЛЕНО: раньше при ошибке соединение оставалось открытым
        if conn is not None:
            conn.close()


def add_user(user_id: int, username: str, first_name: str, last_name: str):
    """Добавляет или обновляет пользователя (не трогая joined_at и is_active)"""
    conn = sqlite3.connect(config.DB_PATH)
    try:
        cursor = conn.cursor()
        # ИСПРАВЛЕНО: INSERT OR REPLACE удалял строку и создавал новую,
        # из-за чего при каждом /start сбрасывались joined_at и is_active.
        cursor.execute("""
            INSERT INTO users (
                user_id, username, first_name, last_name, onboarding_complete,
                last_seen_at
            )
            VALUES (?, ?, ?, ?, 0, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                last_seen_at = excluded.last_seen_at
        """, (
            user_id,
            username,
            first_name,
            last_name,
            get_moscow_time().strftime("%Y-%m-%d %H:%M:%S"),
        ))
        conn.commit()
    finally:
        conn.close()


def is_user_onboarded(user_id: int) -> bool:
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT onboarding_complete
            FROM users
            WHERE user_id = ? AND trim(preferred_name) != ''
            """,
            (user_id,),
        ).fetchone()
    return bool(row and row[0])


def complete_onboarding(user_id: int, preferred_name: str, work_type: str) -> bool:
    if work_type not in {"taxi", "courier"}:
        raise ValueError("Unsupported work type")

    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        with conn:
            updated = conn.execute(
                """
                UPDATE users
                SET preferred_name = ?, onboarding_complete = 1
                WHERE user_id = ?
                  AND (onboarding_complete = 0 OR trim(preferred_name) = '')
                """,
                (preferred_name, user_id),
            )
            if updated.rowcount != 1:
                return False

            now = get_moscow_time().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """
                INSERT INTO driver_profiles (user_id, work_type, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, work_type) DO UPDATE SET
                    updated_at = excluded.updated_at
                """,
                (user_id, work_type, now),
            )
    return True


def get_stats_by_district(district: str, limit: int = 50) -> List[Dict]:
    """Возвращает статистику по району"""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM coefficients 
        WHERE district = ? 
        ORDER BY timestamp DESC 
        LIMIT ?
    """, (district, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_all_data() -> List[Dict]:
    """Все данные для аналитики"""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM coefficients ORDER BY timestamp DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_districts_stats() -> List[Dict]:
    """Статистика по районам: средний коэффициент, количество записей"""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            district,
            AVG(coefficient) as avg_coef,
            MAX(coefficient) as max_coef,
            COUNT(*) as count,
            MAX(timestamp) as last_update
        FROM coefficients 
        GROUP BY district
        HAVING count >= ?
        ORDER BY avg_coef DESC
    """, (config.MIN_DATA_POINTS,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_user_stats(user_id: int) -> Dict:
    """Статистика пользователя"""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) as total_records, 
               AVG(coefficient) as avg_coef,
               MAX(timestamp) as last_record
        FROM coefficients 
        WHERE user_id = ?
    """, (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else {}


def add_work_shift(
    user_id: int,
    work_type: str,
    district: str | None,
    gross_income: float,
    tips: float,
    fuel_cost: float,
    commission_cost: float,
    maintenance_cost: float,
    other_cost: float,
    hours_worked: float,
    rental_cost: float = 0,
    insurance_cost: float = 0,
    tax_cost: float = 0,
    depreciation_cost: float = 0,
    charging_cost: float = 0,
    connectivity_cost: float = 0,
    distance_km: float = 0,
    orders_count: int = 0,
) -> int:
    """Save a completed shift and return its row id."""
    now = get_moscow_time()
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO work_shifts (
                    user_id, work_type, district, gross_income, tips,
                    fuel_cost, commission_cost, maintenance_cost, other_cost,
                    rental_cost, insurance_cost, tax_cost, depreciation_cost,
                    charging_cost, connectivity_cost, hours_worked, distance_km,
                    orders_count, timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, work_type, district, gross_income, tips,
                    fuel_cost, commission_cost, maintenance_cost, other_cost,
                    rental_cost, insurance_cost, tax_cost, depreciation_cost,
                    charging_cost, connectivity_cost, hours_worked, distance_km,
                    orders_count, now.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            shift_id = cursor.lastrowid
    return shift_id


def get_work_summary(user_id: int, work_type: str, since: datetime) -> Dict:
    """Return period totals, a seven-day net-income chart, and recent shifts."""
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        totals = conn.execute(
            """
            SELECT COUNT(*) AS shifts,
                   COALESCE(SUM(gross_income), 0) AS gross_income,
                   COALESCE(SUM(tips), 0) AS tips,
                   COALESCE(SUM(fuel_cost), 0) AS fuel_cost,
                   COALESCE(SUM(commission_cost), 0) AS commission_cost,
                   COALESCE(SUM(maintenance_cost), 0) AS maintenance_cost,
                   COALESCE(SUM(other_cost), 0) AS other_cost,
                   COALESCE(SUM(rental_cost), 0) AS rental_cost,
                   COALESCE(SUM(insurance_cost), 0) AS insurance_cost,
                   COALESCE(SUM(tax_cost), 0) AS tax_cost,
                   COALESCE(SUM(depreciation_cost), 0) AS depreciation_cost,
                   COALESCE(SUM(charging_cost), 0) AS charging_cost,
                   COALESCE(SUM(connectivity_cost), 0) AS connectivity_cost,
                   COALESCE(SUM(hours_worked), 0) AS hours_worked,
                   COALESCE(SUM(distance_km), 0) AS distance_km,
                   COALESCE(SUM(orders_count), 0) AS orders_count
            FROM work_shifts
            WHERE user_id = ? AND work_type = ? AND timestamp >= ?
            """,
            (user_id, work_type, since.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchone()

        today = get_moscow_time().date()
        chart_start = today - timedelta(days=6)
        chart_rows = conn.execute(
            """
            SELECT substr(timestamp, 1, 10) AS day,
                   SUM(gross_income + tips - fuel_cost - commission_cost
                       - maintenance_cost - other_cost - rental_cost
                       - insurance_cost - tax_cost - charging_cost
                       - connectivity_cost) AS net_income
            FROM work_shifts
            WHERE user_id = ? AND work_type = ? AND timestamp >= ?
            GROUP BY substr(timestamp, 1, 10)
            """,
            (user_id, work_type, chart_start.strftime("%Y-%m-%d")),
        ).fetchall()
        recent = conn.execute(
            """
            SELECT id, district, gross_income, tips, fuel_cost, commission_cost,
                   maintenance_cost, other_cost, rental_cost, insurance_cost,
                   tax_cost, depreciation_cost, charging_cost, connectivity_cost,
                   hours_worked, distance_km, orders_count, timestamp
            FROM work_shifts
            WHERE user_id = ? AND work_type = ? AND timestamp >= ?
            ORDER BY timestamp DESC, id DESC
            LIMIT 8
            """,
            (user_id, work_type, since.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchall()
        current_time = get_moscow_time()
        period_duration = current_time - since
        comparison_since = since - period_duration
        previous_totals = conn.execute(
            """
            SELECT COALESCE(SUM(gross_income + tips - fuel_cost - commission_cost
                - maintenance_cost - other_cost - rental_cost - insurance_cost
                - tax_cost - charging_cost - connectivity_cost), 0),
                COUNT(*)
            FROM work_shifts
            WHERE user_id = ? AND work_type = ?
              AND timestamp >= ? AND timestamp < ?
            """,
            (
                user_id,
                work_type,
                comparison_since.strftime("%Y-%m-%d %H:%M:%S"),
                since.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        ).fetchone()
        weekday_rows = conn.execute(
            """
            SELECT CAST(strftime('%w', timestamp) AS INTEGER) AS weekday,
                   SUM(gross_income + tips - fuel_cost - commission_cost
                       - maintenance_cost - other_cost - rental_cost
                       - insurance_cost - tax_cost - charging_cost
                       - connectivity_cost) AS net_income,
                   SUM(hours_worked) AS hours_worked
            FROM work_shifts
            WHERE user_id = ? AND work_type = ? AND timestamp >= ?
            GROUP BY weekday
            """,
            (user_id, work_type, since.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchall()

    totals = dict(totals)
    expenses = {
        key: round(float(totals[key]), 2)
        for key in (
            "fuel_cost", "commission_cost", "maintenance_cost", "other_cost",
            "rental_cost", "insurance_cost", "tax_cost", "charging_cost",
            "connectivity_cost",
        )
    }
    gross_income = round(float(totals["gross_income"]), 2)
    tips = round(float(totals["tips"]), 2)
    total_expenses = round(sum(expenses.values()), 2)
    depreciation = round(float(totals["depreciation_cost"]), 2)
    economic_expenses = round(total_expenses + depreciation, 2)
    distance_km = round(float(totals["distance_km"]), 2)
    orders_count = int(totals["orders_count"])
    hours_worked = round(float(totals["hours_worked"]), 2)
    net_income = round(gross_income + tips - total_expenses, 2)
    weekday_efficiency = [
        {
            "weekday": int(row["weekday"]),
            "net_income_per_hour": round(
                float(row["net_income"]) / float(row["hours_worked"]), 2
            ),
            "hours_worked": round(float(row["hours_worked"]), 2),
        }
        for row in weekday_rows
        if row["hours_worked"] and float(row["hours_worked"]) > 0
    ]
    chart = {chart_start + timedelta(days=offset): 0.0 for offset in range(7)}
    for row in chart_rows:
        chart[datetime.strptime(row["day"], "%Y-%m-%d").date()] = round(
            float(row["net_income"]), 2
        )

    return {
        "shifts": int(totals["shifts"]),
        "gross_income": gross_income,
        "tips": tips,
        "expenses": expenses,
        "total_expenses": total_expenses,
        "economic_expenses": economic_expenses,
        "net_income": net_income,
        "economic_profit": round(net_income - depreciation, 2),
        "depreciation": depreciation,
        "hours_worked": hours_worked,
        "average_per_hour": round(net_income / hours_worked, 2) if hours_worked else 0,
        "distance_km": distance_km,
        "expenses_per_km": round(total_expenses / distance_km, 2) if distance_km else 0,
        "orders_count": orders_count,
        "average_check": round((gross_income + tips) / orders_count, 2)
            if orders_count else 0,
        "average_order_duration_minutes": round(hours_worked * 60 / orders_count, 2)
            if orders_count else 0,
        "previous_period_net_income": round(float(previous_totals[0] or 0), 2),
        "previous_period_shifts": int(previous_totals[1] or 0),
        "weekday_efficiency": weekday_efficiency,
        "chart": [
            {"date": day.isoformat(), "net_income": amount}
            for day, amount in chart.items()
        ],
        "recent_shifts": [dict(row) for row in recent],
    }


def get_market_district_stats() -> Dict[str, Dict]:
    """Return two-hour coefficient summaries for valid Moscow districts."""
    active_since = config.moscow_time_str(timedelta(hours=-2))
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT district, AVG(coefficient) AS avg_coefficient,
                   COUNT(*) AS records, MAX(timestamp) AS last_update
            FROM coefficients
            WHERE timestamp >= ?
            GROUP BY district
            """,
            (active_since,),
        ).fetchall()

    valid_districts = set(config.MOSCOW_DISTRICTS)
    return {
        row["district"]: {
            "avg_coefficient": round(float(row["avg_coefficient"]), 2),
            "records": int(row["records"]),
            "last_update": row["last_update"],
        }
        for row in rows
        if row["district"] in valid_districts
    }


def add_driver_report(
    user_id: int,
    work_type: str,
    category: str,
    district: str,
    details: str = "",
) -> Dict:
    """Create a short-lived report with duplicate and per-user rate checks."""
    if work_type not in {"taxi", "courier"}:
        raise ValueError("Unsupported work type")

    now = get_moscow_time()
    created_at = now.strftime("%Y-%m-%d %H:%M:%S")
    expires_at = (
        now + timedelta(minutes=REPORT_EXPIRY_MINUTES.get(category, 120))
    ).strftime("%Y-%m-%d %H:%M:%S")
    one_hour_ago = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    duplicate_since = (now - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")

    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        with conn:
            duplicate = conn.execute(
                """
                SELECT 1
                FROM driver_reports
                WHERE user_id = ? AND work_type = ? AND category = ?
                  AND district = ? AND created_at >= ?
                LIMIT 1
                """,
                (user_id, work_type, category, district, duplicate_since),
            ).fetchone()
            if duplicate:
                return {"created": False, "reason": "duplicate"}

            recent_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM driver_reports
                WHERE user_id = ? AND created_at >= ?
                """,
                (user_id, one_hour_ago),
            ).fetchone()[0]
            if recent_count >= 5:
                return {"created": False, "reason": "rate_limited"}

            cursor = conn.execute(
                """
                INSERT INTO driver_reports (
                    user_id, work_type, category, district, details,
                    created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, work_type, category, district, details,
                    created_at, expires_at,
                ),
            )
            return {"created": True, "report_id": cursor.lastrowid}


def get_active_driver_reports(
    work_type: str | None = None,
    limit: int = 100,
    now: datetime | None = None,
) -> List[Dict]:
    """Return current public reports without exposing reporter identities."""
    current_time = now or get_moscow_time()
    now_text = current_time.strftime("%Y-%m-%d %H:%M:%S")
    corroboration_since = (
        current_time - timedelta(minutes=30)
    ).strftime("%Y-%m-%d %H:%M:%S")
    work_filter = ""
    if work_type is not None:
        if work_type not in {"taxi", "courier"}:
            raise ValueError("Unsupported work type")
        work_filter = "AND r.work_type = ?"

    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT r.id, r.work_type, r.category, r.district, r.details,
                   r.created_at, r.expires_at,
                   (
                       SELECT COUNT(DISTINCT confirmed.user_id)
                       FROM driver_reports AS confirmed
                       WHERE confirmed.work_type = r.work_type
                         AND confirmed.category = r.category
                         AND confirmed.district = r.district
                         AND confirmed.status = 'active'
                         AND confirmed.created_at >= ?
                         AND confirmed.expires_at > ?
                   ) AS reporters
            FROM driver_reports AS r
            WHERE r.status = 'active'
              AND r.expires_at > ?
              {work_filter}
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT ?
            """,
            (
                corroboration_since,
                now_text,
                now_text,
                *([work_type] if work_type is not None else []),
                limit,
            ),
        ).fetchall()

    reports = []
    for row in rows:
        report = dict(row)
        reporter_count = report.pop("reporters")
        report["confidence"] = (
            "high" if reporter_count >= 3
            else "corroborated" if reporter_count == 2
            else "single"
        )
        report["corroborating_reporters"] = reporter_count
        reports.append(report)
    return reports


def get_driver_report_district_stats(
    work_type: str,
    now: datetime | None = None,
) -> Dict[str, Dict]:
    """Aggregate non-expired community reports by district."""
    if work_type not in {"taxi", "courier"}:
        raise ValueError("Unsupported work type")
    current_time = now or get_moscow_time()
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT district, COUNT(*) AS records,
                   COUNT(DISTINCT user_id) AS reporters,
                   MAX(created_at) AS last_update,
                   GROUP_CONCAT(DISTINCT category) AS categories
            FROM driver_reports
            WHERE work_type = ? AND status = 'active' AND expires_at > ?
            GROUP BY district
            """,
            (
                work_type,
                current_time.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        ).fetchall()

    valid_districts = set(config.MOSCOW_DISTRICTS)
    return {
        row["district"]: {
            "report_count": int(row["records"]),
            "reporters": int(row["reporters"]),
            "last_report_update": row["last_update"],
            "report_categories": sorted(
                category for category in (row["categories"] or "").split(",")
                if category
            ),
        }
        for row in rows
        if row["district"] in valid_districts
    }


def get_driver_reports_for_admin(limit: int = 200) -> List[Dict]:
    """Return active and hidden reports for the authenticated admin panel."""
    now_text = get_moscow_time().strftime("%Y-%m-%d %H:%M:%S")
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, user_id, work_type, category, district, details,
                   status, created_at, expires_at
            FROM driver_reports
            WHERE expires_at > ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (now_text, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def set_driver_report_status(report_id: int, status: str) -> bool:
    if status not in {"active", "hidden"}:
        raise ValueError("Unsupported report status")
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        with conn:
            cursor = conn.execute(
                "UPDATE driver_reports SET status = ? WHERE id = ?",
                (status, report_id),
            )
    return cursor.rowcount == 1


def get_admin_summary(now: int | None = None) -> Dict:
    """Return aggregate operational metrics without personal user details."""
    current_time = int(time.time()) if now is None else now
    month_ago = get_moscow_time() - timedelta(days=30)
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        users = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN last_seen_at >= ? THEN 1 ELSE 0 END) AS active_30d
            FROM users WHERE is_active = 1
            """,
            (month_ago.strftime("%Y-%m-%d %H:%M:%S"),),
        ).fetchone()
        shifts = conn.execute("SELECT COUNT(*) FROM work_shifts").fetchone()[0]
        reports = conn.execute(
            """
            SELECT COUNT(*)
            FROM driver_reports
            WHERE status = 'active' AND expires_at > ?
            """,
            (get_moscow_time().strftime("%Y-%m-%d %H:%M:%S"),),
        ).fetchone()[0]
        profile_directions = conn.execute(
            """
            SELECT work_type, COUNT(*)
            FROM driver_profiles
            GROUP BY work_type
            """
        ).fetchall()
        both_direction_users = conn.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT user_id
                FROM driver_profiles
                GROUP BY user_id
                HAVING COUNT(DISTINCT work_type) = 2
            )
            """
        ).fetchone()[0]
        subscriptions_count = conn.execute(
            """
            SELECT COUNT(*) FROM user_subscriptions
            WHERE paid_until > ? OR trial_expires_at > ?
            """,
            (current_time, current_time),
        ).fetchone()[0]
    profile_directions_by_type = {
        row[0]: int(row[1]) for row in profile_directions
    }
    forecast_quality = get_forecast_quality_summary()
    return {
        "total_users": int(users[0] or 0),
        "active_30d": int(users[1] or 0),
        "taxi_profiles": profile_directions_by_type.get("taxi", 0),
        "courier_profiles": profile_directions_by_type.get("courier", 0),
        "both_direction_users": int(both_direction_users),
        "work_shifts": int(shifts),
        "active_reports": int(reports),
        "active_subscriptions": int(subscriptions_count),
        "forecast_quality": forecast_quality,
    }


def record_forecast_snapshot(
    user_id: int,
    work_type: str,
    forecast: Dict,
    now: datetime | None = None,
) -> int | None:
    """Persist recent district signals for later comparison with observed outcomes."""
    if work_type not in {"taxi", "courier"}:
        raise ValueError("Unsupported work type")
    current_time = now or get_moscow_time()
    generated_at = current_time.strftime("%Y-%m-%d %H:%M:%S")
    recent_since = (
        current_time - FORECAST_SNAPSHOT_MIN_INTERVAL
    ).strftime("%Y-%m-%d %H:%M:%S")
    metric_type = "coefficient" if work_type == "taxi" else "net_income_per_hour"
    horizon_minutes = (
        int(TAXI_FORECAST_EVALUATION_DELAY.total_seconds() // 60)
        if work_type == "taxi"
        else 0
    )
    items = forecast.get("items") or []
    records = []
    for item in items:
        district = item.get("district")
        metric = (
            item.get("current_coefficient")
            if work_type == "taxi" and item.get("current_coefficient") is not None
            else item.get("historical_coefficient")
            if work_type == "taxi"
            else item.get("net_income_per_hour")
        )
        if (
            not isinstance(district, str)
            or not isinstance(metric, (int, float))
            or isinstance(metric, bool)
        ):
            continue
        records.append((
            district,
            int(item.get("score") or 0),
            int(item.get("confidence") or 0),
            metric_type,
            float(metric),
        ))
    if not records:
        return None

    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        with conn:
            retained_since = (
                current_time - timedelta(days=180)
            ).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """
                DELETE FROM forecast_evaluations
                WHERE run_id IN (
                    SELECT id FROM forecast_runs WHERE generated_at < ?
                )
                """,
                (retained_since,),
            )
            conn.execute(
                "DELETE FROM forecast_runs WHERE generated_at < ?",
                (retained_since,),
            )
            recent = conn.execute(
                """
                SELECT id FROM forecast_runs
                WHERE user_id = ? AND work_type = ? AND generated_at >= ?
                ORDER BY generated_at DESC LIMIT 1
                """,
                (user_id, work_type, recent_since),
            ).fetchone()
            if recent:
                return int(recent[0])

            cursor = conn.execute(
                """
                INSERT INTO forecast_runs (
                    user_id, work_type, generated_at, horizon_minutes
                )
                VALUES (?, ?, ?, ?)
                """,
                (user_id, work_type, generated_at, horizon_minutes),
            )
            run_id = int(cursor.lastrowid)
            conn.executemany(
                """
                INSERT INTO forecast_evaluations (
                    run_id, district, predicted_score, confidence,
                    metric_type, predicted_metric
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [(run_id, *record) for record in records],
            )
    return run_id


def evaluate_forecast_snapshots(now: datetime | None = None) -> Dict[str, int]:
    """Compare eligible saved signals with later observations from the app."""
    current_time = now or get_moscow_time()
    current_text = current_time.strftime("%Y-%m-%d %H:%M:%S")
    evaluated = {"taxi": 0, "courier": 0}
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            runs = conn.execute(
                """
                SELECT id, user_id, work_type, generated_at, horizon_minutes
                FROM forecast_runs
                WHERE status = 'pending'
                ORDER BY generated_at
                """
            ).fetchall()
            for run in runs:
                generated_at = datetime.strptime(
                    run["generated_at"], "%Y-%m-%d %H:%M:%S"
                )
                start = generated_at + timedelta(minutes=run["horizon_minutes"])
                window = (
                    TAXI_FORECAST_EVALUATION_WINDOW
                    if run["work_type"] == "taxi"
                    else COURIER_FORECAST_EVALUATION_WINDOW
                )
                end = start + window
                if current_time < end:
                    continue
                start_text = start.strftime("%Y-%m-%d %H:%M:%S")
                end_text = end.strftime("%Y-%m-%d %H:%M:%S")
                items = conn.execute(
                    """
                    SELECT district, predicted_metric
                    FROM forecast_evaluations
                    WHERE run_id = ? AND status = 'pending'
                    """,
                    (run["id"],),
                ).fetchall()
                for item in items:
                    if run["work_type"] == "taxi":
                        observation = conn.execute(
                            """
                            SELECT AVG(coefficient)
                            FROM coefficients
                            WHERE district = ? AND timestamp >= ? AND timestamp <= ?
                            """,
                            (item["district"], start_text, end_text),
                        ).fetchone()[0]
                    else:
                        observation = conn.execute(
                            """
                            SELECT
                                SUM(gross_income + tips - fuel_cost
                                    - commission_cost - maintenance_cost - other_cost
                                    - rental_cost - insurance_cost - tax_cost
                                    - charging_cost - connectivity_cost)
                                    / NULLIF(SUM(hours_worked), 0)
                            FROM work_shifts
                            WHERE user_id = ? AND work_type = 'courier'
                              AND district = ? AND timestamp >= ? AND timestamp <= ?
                              AND hours_worked > 0
                            """,
                            (
                                run["user_id"],
                                item["district"],
                                start_text,
                                end_text,
                            ),
                        ).fetchone()[0]
                    status = "evaluated" if observation is not None else "insufficient_data"
                    error = (
                        abs(float(observation) - float(item["predicted_metric"]))
                        if observation is not None else None
                    )
                    conn.execute(
                        """
                        UPDATE forecast_evaluations
                        SET observed_metric = ?, absolute_error = ?,
                            status = ?, evaluated_at = ?
                        WHERE run_id = ? AND district = ?
                        """,
                        (
                            observation,
                            error,
                            status,
                            current_text,
                            run["id"],
                            item["district"],
                        ),
                    )
                    if observation is not None:
                        evaluated[run["work_type"]] += 1
                conn.execute(
                    "UPDATE forecast_runs SET status = 'evaluated' WHERE id = ?",
                    (run["id"],),
                )
    return evaluated


def get_forecast_quality_summary() -> Dict[str, Dict]:
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT r.work_type, e.metric_type,
                   COUNT(*) AS predictions,
                   SUM(CASE WHEN e.status = 'evaluated' THEN 1 ELSE 0 END) AS evaluated,
                   SUM(CASE WHEN e.status = 'insufficient_data' THEN 1 ELSE 0 END)
                       AS insufficient,
                   AVG(CASE WHEN e.status = 'evaluated' THEN e.absolute_error END)
                       AS mean_absolute_error
            FROM forecast_runs AS r
            JOIN forecast_evaluations AS e ON e.run_id = r.id
            GROUP BY r.work_type, e.metric_type
            """
        ).fetchall()
    result = {
        "taxi": {
            "metric": "coefficient",
            "predictions": 0,
            "evaluated": 0,
            "insufficient": 0,
            "mean_absolute_error": None,
        },
        "courier": {
            "metric": "net_income_per_hour",
            "predictions": 0,
            "evaluated": 0,
            "insufficient": 0,
            "mean_absolute_error": None,
        },
    }
    for row in rows:
        item = result[row["work_type"]]
        item.update({
            "predictions": int(row["predictions"] or 0),
            "evaluated": int(row["evaluated"] or 0),
            "insufficient": int(row["insufficient"] or 0),
            "mean_absolute_error": (
                round(float(row["mean_absolute_error"]), 2)
                if row["mean_absolute_error"] is not None else None
            ),
        })
    return result


def get_demand_forecast_inputs(
    user_id: int,
    work_type: str,
    now: datetime | None = None,
) -> Dict[str, List[Dict]]:
    """Load bounded, privacy-safe inputs for the selected forecast model."""
    if work_type not in {"taxi", "courier"}:
        raise ValueError("Unsupported work type")

    current_time = now or get_moscow_time()
    lookback_days = 30 if work_type == "taxi" else 90
    since = (current_time - timedelta(days=lookback_days)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        if work_type == "taxi":
            rows = conn.execute(
                """
                SELECT user_id, district, coefficient, timestamp
                FROM coefficients
                WHERE timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp DESC
                """,
                (since, current_time.strftime("%Y-%m-%d %H:%M:%S")),
            ).fetchall()
            return {"reports": [dict(row) for row in rows], "shifts": []}

        rows = conn.execute(
            """
            SELECT district, gross_income, tips, fuel_cost, commission_cost,
                   maintenance_cost, other_cost, rental_cost, insurance_cost,
                   tax_cost, depreciation_cost, charging_cost, connectivity_cost,
                   hours_worked, timestamp
            FROM work_shifts
            WHERE user_id = ?
              AND work_type = ?
              AND district IS NOT NULL
              AND hours_worked > 0
              AND timestamp >= ?
              AND timestamp <= ?
            ORDER BY timestamp DESC
            """,
            (
                user_id,
                work_type,
                since,
                current_time.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        ).fetchall()
        return {"reports": [], "shifts": [dict(row) for row in rows]}


def get_driver_directions(user_id: int) -> List[str]:
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT work_type
            FROM driver_profiles
            WHERE user_id = ?
            ORDER BY rowid
            """,
            (user_id,),
        ).fetchall()
    return [row[0] for row in rows]


def add_driver_direction(user_id: int, work_type: str) -> bool:
    if work_type not in {"taxi", "courier"}:
        raise ValueError("Unsupported work type")
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        with conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO driver_profiles (user_id, work_type, updated_at)
                VALUES (?, ?, ?)
                """,
                (
                    user_id,
                    work_type,
                    get_moscow_time().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
    return cursor.rowcount == 1


def get_notification_preferences(user_id: int) -> Dict:
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO notification_preferences (user_id) VALUES (?)",
                (user_id,),
            )
        row = conn.execute(
            "SELECT * FROM notification_preferences WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    preferences = dict(row)
    for field in NOTIFICATION_BOOLEAN_FIELDS:
        preferences[field] = bool(preferences[field])
    return preferences


def save_notification_preferences(user_id: int, preferences: Dict) -> None:
    fields = (*NOTIFICATION_BOOLEAN_FIELDS, "frequency", "quiet_start", "quiet_end")
    values = [
        int(preferences[field]) if field in NOTIFICATION_BOOLEAN_FIELDS
        else preferences[field]
        for field in fields
    ]
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        with conn:
            conn.execute(
                f"""
                INSERT INTO notification_preferences (
                    user_id, {", ".join(fields)}
                )
                VALUES (?, {", ".join("?" for _ in fields)})
                ON CONFLICT(user_id) DO UPDATE SET
                    {", ".join(f"{field} = excluded.{field}" for field in fields)}
                """,
                (user_id, *values),
            )


def has_active_paid_subscription(user_id: int, now: int | None = None) -> bool:
    current_time = int(time.time()) if now is None else now
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        row = conn.execute(
            "SELECT 1 FROM user_subscriptions WHERE user_id = ? AND paid_until > ?",
            (user_id, current_time),
        ).fetchone()
    return row is not None


def export_user_data(user_id: int) -> Dict:
    """Return records owned by one Telegram user for a personal data export."""
    table_queries = {
        "profiles": ("driver_profiles", "user_id"),
        "notification_preferences": ("notification_preferences", "user_id"),
        "coefficients": ("coefficients", "user_id"),
        "shifts": ("work_shifts", "user_id"),
        "reports": ("driver_reports", "user_id"),
        "subscriptions": ("user_subscriptions", "user_id"),
        "payments": ("telegram_star_payments", "user_id"),
        "reminder_history": ("document_reminder_log", "user_id"),
    }
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        user = conn.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        result = {"user": dict(user) if user else None}
        for key, (table, owner_column) in table_queries.items():
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE {owner_column} = ?",
                (user_id,),
            ).fetchall()
            result[key] = [dict(row) for row in rows]
        runs = conn.execute(
            "SELECT * FROM forecast_runs WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        result["forecast_runs"] = [dict(row) for row in runs]
        result["forecast_evaluations"] = [
            dict(row)
            for row in conn.execute(
                """
                SELECT e.* FROM forecast_evaluations AS e
                JOIN forecast_runs AS r ON r.id = e.run_id
                WHERE r.user_id = ?
                """,
                (user_id,),
            ).fetchall()
        ]
    return result


def delete_user_data(user_id: int) -> None:
    """Erase application records owned by one user in a single transaction."""
    user_tables = (
        "coefficients", "locations", "work_shifts", "driver_reports",
        "driver_profiles", "user_subscriptions", "payment_support_pending",
        "document_reminder_log", "telegram_star_payments",
        "notification_preferences",
    )
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        with conn:
            conn.execute(
                """
                DELETE FROM forecast_evaluations
                WHERE run_id IN (SELECT id FROM forecast_runs WHERE user_id = ?)
                """,
                (user_id,),
            )
            conn.execute("DELETE FROM forecast_runs WHERE user_id = ?", (user_id,))
            for table in user_tables:
                conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))


def get_driver_profile(user_id: int, work_type: str | None = None) -> Dict:
    """Return one saved direction profile and document dates."""
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT u.first_name, u.last_name, u.preferred_name,
                   u.onboarding_complete, p.*
            FROM users AS u
            LEFT JOIN driver_profiles AS p
              ON p.user_id = u.user_id
             AND (? IS NULL OR p.work_type = ?)
            WHERE u.user_id = ?
            ORDER BY p.rowid
            LIMIT 1
            """,
            (work_type, work_type, user_id),
        ).fetchone()
    if row is None:
        return {}
    profile = dict(row)
    profile["onboarding_complete"] = bool(profile["onboarding_complete"])
    if profile["user_id"] is None:
        profile.update({
            "work_type": "taxi",
            "vehicle_type": "",
            "vehicle_make": "",
            "vehicle_model": "",
            "vehicle_year": None,
            "fuel_type": "",
            "weekly_income_goal": 0,
            "reminders_enabled": 1,
            **{field: None for field in DOCUMENT_FIELDS},
        })
    profile["reminders_enabled"] = bool(profile["reminders_enabled"])
    return profile


def save_driver_profile(user_id: int, profile: Dict) -> None:
    """Insert or update a validated driver profile."""
    fields = (
        "work_type", "vehicle_type", "vehicle_make", "vehicle_model",
        "vehicle_year", "fuel_type", "weekly_income_goal", "reminders_enabled",
        *DOCUMENT_FIELDS,
    )
    values = [profile[field] for field in fields]
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        with conn:
            conn.execute(
                f"""
                INSERT INTO driver_profiles (
                    user_id, {", ".join(fields)}, updated_at
                )
                VALUES (?, {", ".join("?" for _ in fields)}, ?)
                ON CONFLICT(user_id, work_type) DO UPDATE SET
                    {", ".join(f"{field} = excluded.{field}" for field in fields)},
                    updated_at = excluded.updated_at
                """,
                (user_id, *values, get_moscow_time().strftime("%Y-%m-%d %H:%M:%S")),
            )


def get_due_document_reminders(today: date) -> List[Dict]:
    """Return unsent document reminders due today for enabled driver profiles."""
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        profiles = conn.execute(
            """
            SELECT p.user_id, p.reminders_enabled,
                   p.medical_book_expiry, p.license_expiry, p.osago_expiry,
                   p.inspection_expiry, p.taxi_permit_expiry
            FROM driver_profiles AS p
            JOIN users AS u ON u.user_id = p.user_id
            LEFT JOIN notification_preferences AS n ON n.user_id = p.user_id
            WHERE p.reminders_enabled = 1 AND u.is_active = 1
              AND COALESCE(n.financial_reminders, 1) = 1
            """
        ).fetchall()
        sent = {
            (row["user_id"], row["document_key"], row["expiry_date"], row["days_before"])
            for row in conn.execute(
                """
                SELECT user_id, document_key, expiry_date, days_before
                FROM document_reminder_log
                """
            )
        }

    reminders = []
    pending = set(sent)
    for profile in profiles:
        for field, (document_key, label) in DOCUMENT_FIELDS.items():
            expiry_value = profile[field]
            if not expiry_value:
                continue
            expiry = date.fromisoformat(expiry_value)
            days_left = (expiry - today).days
            if days_left not in REMINDER_OFFSETS:
                continue
            key = (profile["user_id"], document_key, expiry_value, days_left)
            if key not in pending:
                reminders.append({
                    "user_id": profile["user_id"],
                    "document_key": document_key,
                    "document_label": label,
                    "expiry_date": expiry_value,
                    "days_left": days_left,
                })
                pending.add(key)
    return reminders


def log_document_reminder(reminder: Dict) -> bool:
    """Record a sent reminder once, keyed to its document expiry date."""
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        with conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO document_reminder_log (
                    user_id, document_key, expiry_date, days_before, sent_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    reminder["user_id"],
                    reminder["document_key"],
                    reminder["expiry_date"],
                    reminder["days_left"],
                    get_moscow_time().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            return cursor.rowcount == 1