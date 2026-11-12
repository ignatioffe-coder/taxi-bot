"""Работа с базой данных"""

import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from typing import List, Dict
import config

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
            joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )
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

    conn.commit()
    conn.close()


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
            INSERT INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name
        """, (user_id, username, first_name, last_name))
        conn.commit()
    finally:
        conn.close()


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


def get_driver_profile(user_id: int) -> Dict:
    """Return a driver's saved profile and document dates."""
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT u.first_name, u.last_name, p.*
            FROM users AS u
            LEFT JOIN driver_profiles AS p ON p.user_id = u.user_id
            WHERE u.user_id = ?
            """,
            (user_id,),
        ).fetchone()
    if row is None:
        return {}
    profile = dict(row)
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