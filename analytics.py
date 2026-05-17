"""Аналитика и прогнозы"""

import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
import statistics
import config


def get_current_recommendations() -> str:
    """Возвращает текущие рекомендации на основе исторических данных"""
    now = datetime.now()
    current_hour = now.hour
    current_dow = now.weekday()  # 0=понедельник

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Ищем похожие условия: тот же день недели, +-2 часа
    cursor.execute("""
        SELECT district, coefficient, weather, hour
        FROM coefficients 
        WHERE day_of_week = ? 
          AND hour BETWEEN ? AND ?
          AND timestamp > datetime('now', '-30 days')
        ORDER BY timestamp DESC
    """, (current_dow, max(0, current_hour - 2), min(23, current_hour + 2)))

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return "📊 Пока недостаточно данных для рекомендаций. Присылай коэффициенты — и прогнозы станут точнее!"

    # Агрегируем по районам
    district_data = defaultdict(list)
    for row in rows:
        district_data[row['district']].append(row['coefficient'])

    # Считаем средние и надёжность
    recommendations = []
    for district, coeffs in district_data.items():
        if len(coeffs) >= 3:  # Минимум 3 записи
            avg = statistics.mean(coeffs)
            # "Надёжность" — чем больше данных, тем лучше
            reliability = min(len(coeffs) / 10, 1.0)  # 10+ записей = максимальная надёжность
            recommendations.append({
                'district': district,
                'avg_coefficient': round(avg, 2),
                'data_points': len(coeffs),
                'reliability': round(reliability * 100),
                'max': round(max(coeffs), 2)
            })

    # Сортируем по среднему коэффициенту
    recommendations.sort(key=lambda x: x['avg_coefficient'], reverse=True)

    # Формируем текст
    text = f"📍 *Рекомендации на сейчас* ({now.strftime('%H:%M')}, {get_day_name(current_dow)})\n\n"

    for i, rec in enumerate(recommendations[:7], 1):
        stars = "⭐" * min(int(rec['avg_coefficient']), 3)
        text += (
            f"{i}. *{rec['district']}*\n"
            f"   Средний коэф: *{rec['avg_coefficient']}x* {stars}\n"
            f"   Максимум: {rec['max']}x | Записей: {rec['data_points']}\n"
            f"   Надёжность: {rec['reliability']}%\n\n"
        )

    if len(recommendations) > 7:
        text += f"_...и ещё {len(recommendations) - 7} районов_\n"

    text += "\n💡 *Совет*: Это средние значения за последние 30 дней в похожее время. Реальность может отличаться."

    return text


def get_district_forecast(district: str) -> str:
    """Прогноз по конкретному району на сегодня"""
    now = datetime.now()
    current_dow = now.weekday()

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Получаем данные по району за последние 30 дней
    cursor.execute("""
        SELECT hour, coefficient, day_of_week, timestamp
        FROM coefficients 
        WHERE district = ? 
          AND timestamp > datetime('now', '-30 days')
        ORDER BY hour
    """, (district,))

    rows = cursor.fetchall()
    conn.close()

    if len(rows) < config.MIN_DATA_POINTS:
        return f"📊 По району *{district}* пока мало данных ({len(rows)} записей). Нужно минимум {config.MIN_DATA_POINTS}."

    # Группируем по часам
    hourly_data = defaultdict(list)
    for row in rows:
        hourly_data[row['hour']].append(row['coefficient'])

    # Формируем прогноз по часам
    text = f"📈 *Прогноз по району: {district}*\n"
    text += f"_Сегодня ({get_day_name(current_dow)}), на основе {len(rows)} записей_\n\n"

    for hour in sorted(hourly_data.keys()):
        coeffs = hourly_data[hour]
        avg = statistics.mean(coeffs)
        count = len(coeffs)

        indicator = "🔴" if avg >= 1.5 else "🟡" if avg >= 1.2 else "🟢"
        time_str = f"{hour:02d}:00"

        text += f"{indicator} {time_str}: *{avg:.2f}x* (записей: {count})\n"

    # Лучшее время
    best_hour = max(hourly_data.keys(), key=lambda h: statistics.mean(hourly_data[h]))
    best_avg = statistics.mean(hourly_data[best_hour])
    text += f"\n🏆 *Лучшее время: {best_hour:02d}:00* (средний коэф: {best_avg:.2f}x)"

    return text


def get_weekly_report() -> str:
    """Отчёт за неделю для админа"""
    week_ago = datetime.now() - timedelta(days=7)

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*) as total_records,
               COUNT(DISTINCT user_id) as active_users,
               COUNT(DISTINCT district) as districts_covered,
               AVG(coefficient) as avg_coefficient
        FROM coefficients 
        WHERE timestamp > ?
    """, (week_ago,))

    row = cursor.fetchone()
    conn.close()

    text = "📊 *Отчёт за неделю*\n\n"
    text += f"📝 Новых записей: {row['total_records']}\n"
    text += f"👥 Активных водителей: {row['active_users']}\n"
    text += f"📍 Районов покрыто: {row['districts_covered']}\n"
    text += f"📈 Средний коэффициент: {round(row['avg_coefficient'], 2)}x\n"

    return text


def get_day_name(dow: int) -> str:
    days = ["понедельник", "вторник", "среда", "четверг", 
            "пятница", "суббота", "воскресенье"]
    return days[dow]


def get_top_moments() -> str:
    """Топ моментов с высокими коэффициентами"""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT district, coefficient, hour, day_of_week, timestamp
        FROM coefficients 
        WHERE coefficient >= 1.5
        ORDER BY coefficient DESC
        LIMIT 10
    """)

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return "Пока нет записей с коэффициентом ≥1.5"

    text = "🏆 *Топ моменты с высокими коэффициентами*\n\n"
    for i, row in enumerate(rows, 1):
        date_str = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00')).strftime('%d.%m %H:%M')
        text += f"{i}. {row['district']}: *{row['coefficient']}x* ({date_str})\n"

    return text
