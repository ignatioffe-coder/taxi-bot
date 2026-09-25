import sqlite3
import os
import config


def clear_database():
    if not os.path.exists(config.DB_PATH):
        print(f"❌ Файл базы данных {config.DB_PATH} не найден!")
        return

    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()

        if not tables:
            print("⚠️ В базе данных нет таблиц")
            conn.close()
            return

        print(f"Найдено таблиц: {len(tables)}")

        cleared_count = 0
        for table in tables:
            if table[0] != 'sqlite_sequence':
                cursor.execute(f"DELETE FROM {table[0]};")
                print(f"✅ Очищена таблица: {table[0]}")
                cleared_count += 1

        cursor.execute("DELETE FROM sqlite_sequence;")
        print("✅ Сброшены автоинкременты")

        conn.commit()
        conn.close()

        print(f"\n🎉 База данных полностью очищена!")
        print(f"Очищено таблиц: {cleared_count}")
        print(f"Размер файла: {os.path.getsize(config.DB_PATH)} байт")

    except Exception as e:
        print(f"❌ Ошибка при очистке БД: {e}")


def preview_before_clear():
    if not os.path.exists(config.DB_PATH):
        print("База данных не существует")
        return

    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()

    print("\n📊 Текущее состояние БД:")
    for table in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table[0]};")
        count = cursor.fetchone()[0]
        print(f"  • {table[0]}: {count} записей")

    conn.close()


if __name__ == "__main__":
    print("=== ОЧИСТКА БАЗЫ ДАННЫХ ===\n")
    preview_before_clear()
    print("\n⚠️ ВНИМАНИЕ! Это действие удалит ВСЕ данные из базы!")
    confirm = input("Вы уверены? (да/нет): ")

    if confirm.lower() == 'да':
        clear_database()
    else:
        print("❌ Очистка отменена")
