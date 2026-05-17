import os
import psycopg2
from datetime import datetime

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_connection():
    if DATABASE_URL:
        return psycopg2.connect(DATABASE_URL)
    else:
        import sqlite3
        return sqlite3.connect("taxi_data.db")

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    if DATABASE_URL:
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

def add_coefficient(user_id, username, district, coefficient, weather="", notes=""):
    try:
        now = datetime.now()
        conn = get_connection()
        cursor = conn.cursor()
        
        if DATABASE_URL:
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
        return True
    except Exception as e:
        print(f"Ошибка: {e}")
        return False

# Остальные функции (add_user, get_user_stats и т.д.) добавьте аналогично