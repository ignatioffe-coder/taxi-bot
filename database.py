"""Работа с базой данных"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Optional
import config


def init_db():
    """Создаёт таблицы, если их нет"""
    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()

    # Основная таблица: коэффициенты
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

    # Таблица пользователей
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

    # Таблица геолокаций (если пользователь шлёт гео)
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

    conn.commit()
    conn.close()


def add_coefficient(user_id: int, username: str, district: str, 
                    coefficient: float, weather: str = "", notes: str = "") -> bool:
    """Добавляет запись о коэффициенте"""
    try:
        now = datetime.now()
        print(f"DEBUG add_coefficient: user_id={user_id}, district={district}, coef={coefficient}, weather={weather}")
        
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO coefficients 
            (user_id, username, district, coefficient, weather, day_of_week, hour, notes, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, username, district, coefficient, weather, 
              now.weekday(), now.hour, notes, now.strftime('%Y-%m-%d %H:%M:%S')))
        
        conn.commit()
        print(f"DEBUG: Запись успешно добавлена! ID: {cursor.lastrowid}")
        conn.close()
        return True
    except Exception as e:
        print(f"Ошибка добавления коэффициента: {e}")
        return False

def add_user(user_id: int, username: str, first_name: str, last_name: str):
    """Добавляет или обновляет пользователя"""
    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO users (user_id, username, first_name, last_name)
        VALUES (?, ?, ?, ?)
    """, (user_id, username, first_name, last_name))
    conn.commit()
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
