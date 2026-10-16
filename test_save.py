import sqlite3
import config


def test_save():
    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()

    # ИСПРАВЛЕНО: пишем московское время — так же, как это делает database.py
    now_dt = config.get_moscow_time()
    now = now_dt.strftime('%Y-%m-%d %H:%M:%S')

    try:
        cursor.execute("""
            INSERT INTO coefficients (user_id, username, district, coefficient, weather, day_of_week, hour, timestamp, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (7925732814, "test_user", "Тестовый район", 2.5, "ясно",
              now_dt.weekday(), now_dt.hour, now, ""))

        conn.commit()
        print("✅ Тестовая запись успешно добавлена!")

        cursor.execute("SELECT timestamp, district, coefficient FROM coefficients ORDER BY timestamp DESC LIMIT 3")
        print("\nПоследние 3 записи в БД:")
        for row in cursor.fetchall():
            print(f"  {row[0]} | {row[1]} | {row[2]}x")

    except Exception as e:
        print(f"❌ Ошибка: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    test_save()
