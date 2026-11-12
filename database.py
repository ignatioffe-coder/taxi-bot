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
            hours_worked REAL NOT NULL DEFAULT 0,
            timestamp DATETIME NOT NULL
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_work_shifts_user_time
        ON work_shifts (user_id, timestamp)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS driver_profiles (
            user_id INTEGER PRIMARY KEY,
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
            updated_at DATETIME NOT NULL
        )
    """)
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
                user_id, username, first_name, last_name, onboarding_complete
            )
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name
        """, (user_id, username, first_name, last_name))
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
                ON CONFLICT(user_id) DO UPDATE SET
                    work_type = excluded.work_type,
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
                    hours_worked, timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, work_type, district, gross_income, tips,
                    fuel_cost, commission_cost, maintenance_cost, other_cost,
                    hours_worked, now.strftime("%Y-%m-%d %H:%M:%S"),
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
                   COALESCE(SUM(hours_worked), 0) AS hours_worked
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
                       - maintenance_cost - other_cost) AS net_income
            FROM work_shifts
            WHERE user_id = ? AND work_type = ? AND timestamp >= ?
            GROUP BY substr(timestamp, 1, 10)
            """,
            (user_id, work_type, chart_start.strftime("%Y-%m-%d")),
        ).fetchall()
        recent = conn.execute(
            """
            SELECT id, district, gross_income, tips, fuel_cost, commission_cost,
                   maintenance_cost, other_cost, hours_worked, timestamp
            FROM work_shifts
            WHERE user_id = ? AND work_type = ? AND timestamp >= ?
            ORDER BY timestamp DESC, id DESC
            LIMIT 8
            """,
            (user_id, work_type, since.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchall()

    totals = dict(totals)
    expenses = {
        key: round(float(totals[key]), 2)
        for key in ("fuel_cost", "commission_cost", "maintenance_cost", "other_cost")
    }
    gross_income = round(float(totals["gross_income"]), 2)
    tips = round(float(totals["tips"]), 2)
    total_expenses = round(sum(expenses.values()), 2)
    hours_worked = round(float(totals["hours_worked"]), 2)
    net_income = round(gross_income + tips - total_expenses, 2)
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
        "net_income": net_income,
        "hours_worked": hours_worked,
        "average_per_hour": round(net_income / hours_worked, 2) if hours_worked else 0,
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
                   maintenance_cost, other_cost, hours_worked, timestamp
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


def get_driver_profile(user_id: int) -> Dict:
    """Return a driver's saved profile and document dates."""
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT u.first_name, u.last_name, u.preferred_name,
                   u.onboarding_complete, p.*
            FROM users AS u
            LEFT JOIN driver_profiles AS p ON p.user_id = u.user_id
            WHERE u.user_id = ?
            """,
            (user_id,),
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
                ON CONFLICT(user_id) DO UPDATE SET
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
            WHERE p.reminders_enabled = 1 AND u.is_active = 1
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
            if key not in sent:
                reminders.append({
                    "user_id": profile["user_id"],
                    "document_key": document_key,
                    "document_label": label,
                    "expiry_date": expiry_value,
                    "days_left": days_left,
                })
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