import sqlite3
from datetime import datetime

# Проверяем, работает ли сохранение
def test_save():
    conn = sqlite3.connect('taxi_data.db')
    cursor = conn.cursor()
    
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        cursor.execute("""
            INSERT INTO coefficients (user_id, username, district, coefficient, weather, day_of_week, hour, timestamp, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (7925732814, "test_user", "Тестовый район", 2.5, "ясно", 
              datetime.now().weekday(), datetime.now().hour, now, ""))
        
        conn.commit()
        print("✅ Тестовая запись успешно добавлена!")
        
        # Проверяем
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