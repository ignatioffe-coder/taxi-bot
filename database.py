"""Работа с базой данных (SQLite для локально, PostgreSQL для Railway)"""

import os
import sqlite3
from datetime import datetime

# Определяем тип БД (PostgreSQL на Railway, SQLite локально)
DATABASE_URL = os.environ.get("DATABASE_URL")

# Если есть DATABASE_URL, используем PostgreSQL, иначе SQLite
IS_POSTGRES = DATABASE_URL is not None and DATABASE_URL.startswith("postgres")

if IS_POSTGRES:
    import psycopg2
    from psycopg2.extras import RealDictCursor


def get_connection():
    """Возвращает соединение с БД (автоматически выбирает PostgreSQL или SQLite)"""
    if IS_POSTGRES:
        return psycopg2.connect(DATABASE_URL)
    else:
        return sqlite3.connect("taxi_data.db")


def init_db():
    """Создаёт таблицы, если их нет"""
    conn = get_connection()
    cursor = conn.cursor()
    
    if IS_POSTGRES:
        # PostgreSQL
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS coefficients (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                username TEXT,
                district TEXT NOT NULL,
                coefficient REAL NOT NULL,
                weather TEXT DEFAULT '',
                day_of_week INTEGER,
                hour INTEGER,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes TEXT DEFAULT ''
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1
            )
        """)
    else:
        # SQLite
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
    
    conn.commit()
    conn.close()
    print("✅ База данных инициализирована")


def add_coefficient(user_id: int, username: str, district: str, 
                    coefficient: float, weather: str = "", notes: str = "") -> bool:
    """Добавляет запись о коэффициенте"""
    try:
        now = datetime.now()
        conn = get_connection()
        cursor = conn.cursor()
        
        if IS_POSTGRES:
            cursor.execute("""
                INSERT INTO coefficients 
                (user_id, username, district, coefficient, weather, day_of_week, hour, notes, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (user_id, username, district, coefficient, weather, 
                  now.weekday(), now.hour, notes, now))
        else:
            cursor.execute("""
                INSERT INTO coefficients 
                (user_id, username, district, coefficient, weather, day_of_week, hour, notes, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, username, district, coefficient, weather, 
                  now.weekday(), now.hour, notes, now.strftime('%Y-%m-%d %H:%M:%S')))
        
        conn.commit()
        conn.close()
        print(f"✅ Добавлен коэффициент: {district} = {coefficient}x")
        return True
    except Exception as e:
        print(f"❌ Ошибка добавления коэффициента: {e}")
        return False


def add_user(user_id: int, username: str, first_name: str, last_name: str):
    """Добавляет или обновляет пользователя"""
    conn = get_connection()
    cursor = conn.cursor()
    
    if IS_POSTGRES:
        cursor.execute("""
            INSERT INTO users (user_id, username, first_name, last_name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name
        """, (user_id, username, first_name, last_name))
    else:
        cursor.execute("""
            INSERT OR REPLACE INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
        """, (user_id, username, first_name, last_name))
    
    conn.commit()
    conn.close()


def get_user_stats(user_id: int) -> dict:
    """Статистика пользователя"""
    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()
    
    # Сначала проверим, есть ли вообще записи с таким user_id
    cursor.execute("SELECT COUNT(*) FROM coefficients WHERE user_id = ?", (user_id,))
    total = cursor.fetchone()[0]
    
    print(f"DEBUG: get_user_stats для user_id={user_id}, найдено записей={total}")
    
    if total == 0:
        conn.close()
        return {'total_records': 0, 'avg_coef': 0, 'last_record': 'нет'}
    
    cursor.execute("""
        SELECT 
            COUNT(*) as total_records, 
            AVG(coefficient) as avg_coef,
            MAX(timestamp) as last_record
        FROM coefficients 
        WHERE user_id = ?
    """, (user_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row and row[0] > 0:
        last_record = row[2]
        if last_record:
            if isinstance(last_record, str):
                last_record = last_record[:16]
            else:
                last_record = str(last_record)[:16]
        
        return {
            'total_records': row[0],
            'avg_coef': round(row[1], 2) if row[1] else 0,
            'last_record': last_record or 'недавно'
        }
    
    return {'total_records': 0, 'avg_coef': 0, 'last_record': 'нет'}


def get_district_forecast(district: str) -> str:
    """Получить прогноз по району (средний коэффициент и количество записей)"""
    conn = get_connection()
    cursor = conn.cursor()
    
    if IS_POSTGRES:
        cursor.execute("""
            SELECT 
                COALESCE(AVG(coefficient), 0) as avg_coef, 
                COUNT(*) as count
            FROM coefficients 
            WHERE district = %s
        """, (district,))
    else:
        cursor.execute("""
            SELECT 
                COALESCE(AVG(coefficient), 0) as avg_coef, 
                COUNT(*) as count
            FROM coefficients 
            WHERE district = ?
        """, (district,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row and row[1] > 0:
        avg_coef = round(row[0], 2)
        count = row[1]
        return f"📊 Район {district}: средний коэффициент {avg_coef}x (на основе {count} записей)"
    else:
        return f"📊 Район {district}: пока нет данных. Будьте первым!"


def get_top_districts(limit: int = 10) -> list:
    """Топ районов по среднему коэффициенту"""
    conn = get_connection()
    cursor = conn.cursor()
    
    if IS_POSTGRES:
        cursor.execute("""
            SELECT 
                district, 
                COALESCE(AVG(coefficient), 0) as avg_coef, 
                COUNT(*) as count
            FROM coefficients 
            GROUP BY district 
            ORDER BY avg_coef DESC 
            LIMIT %s
        """, (limit,))
    else:
        cursor.execute("""
            SELECT 
                district, 
                COALESCE(AVG(coefficient), 0) as avg_coef, 
                COUNT(*) as count
            FROM coefficients 
            GROUP BY district 
            ORDER BY avg_coef DESC 
            LIMIT ?
        """, (limit,))
    
    rows = cursor.fetchall()
    conn.close()
    
    result = []
    for row in rows:
        result.append({
            'district': row[0],
            'avg_coef': round(row[1], 2),
            'count': row[2]
        })
    return result


def get_all_coefficients(limit: int = 100) -> list:
    """Получить последние коэффициенты"""
    conn = get_connection()
    cursor = conn.cursor()
    
    if IS_POSTGRES:
        cursor.execute("""
            SELECT district, coefficient, timestamp, weather
            FROM coefficients 
            ORDER BY timestamp DESC 
            LIMIT %s
        """, (limit,))
    else:
        cursor.execute("""
            SELECT district, coefficient, timestamp, weather
            FROM coefficients 
            ORDER BY timestamp DESC 
            LIMIT ?
        """, (limit,))
    
    rows = cursor.fetchall()
    conn.close()
    
    result = []
    for row in rows:
        result.append({
            'district': row[0],
            'coefficient': row[1],
            'timestamp': str(row[2])[:16] if row[2] else '',
            'weather': row[3] if len(row) > 3 and row[3] else ''
        })
    return result