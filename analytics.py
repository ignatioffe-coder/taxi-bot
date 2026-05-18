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
    current_dow = now.weekday()

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

    # Если нет данных, даём общие рекомендации
    if not rows:
        return get_fallback_recommendations()

    # Агрегируем по районам
    district_data = defaultdict(list)
    for row in rows:
        district_data[row['district']].append(row['coefficient'])

    # Считаем средние и надёжность
    recommendations = []
    for district, coeffs in district_data.items():
        if len(coeffs) >= 2:
            avg = statistics.mean(coeffs)
            recommendations.append({
                'district': district,
                'avg_coefficient': round(avg, 2),
                'data_points': len(coeffs),
                'max': round(max(coeffs), 2)
            })

    if not recommendations:
        return get_fallback_recommendations()

    # Сортируем по среднему коэффициенту
    recommendations.sort(key=lambda x: x['avg_coefficient'], reverse=True)

    # Формируем текст
    text = f"📍 *Рекомендации на сейчас* ({now.strftime('%H:%M')}, {get_day_name(current_dow)})\n\n"

    for i, rec in enumerate(recommendations[:5], 1):
        stars = "⭐" * min(int(rec['avg_coefficient']), 3)
        text += (
            f"{i}. *{rec['district']}*\n"
            f"   Средний коэф: *{rec['avg_coefficient']}x* {stars}\n"
            f"   Максимум: {rec['max']}x | Записей: {rec['data_points']}\n\n"
        )

    text += "💡 *Совет*: Присылайте коэффициенты — прогнозы станут точнее!"

    return text


def get_fallback_recommendations() -> str:
    """Рекомендации, когда нет исторических данных"""
    now = datetime.now()
    hour = now.hour
    weekday = now.weekday()
    is_weekend = weekday >= 5
    
    text = f"📍 *Рекомендации на сейчас* ({now.strftime('%H:%M')}, {get_day_name(weekday)})\n\n"
    text += "📊 *На основе общих правил:*\n\n"
    
    # Рекомендации по времени
    if 8 <= hour <= 10:
        text += "🌅 *Утренний час пик* → езжайте к метро и вокзалам\n"
        text += "   Ожидаемый коэффициент: 1.8-2.2x\n"
    elif 17 <= hour <= 20:
        text += "🌆 *Вечерний час пик* → центр Москвы, ТЦ\n"
        text += "   Ожидаемый коэффициент: 1.8-2.5x\n"
    elif hour >= 23 or hour <= 5:
        text += "🌙 *Ночное время* → аэропорты, бары, клубы\n"
        text += "   Ожидаемый коэффициент: 2.0-3.0x\n"
    else:
        text += "☀️ *Дневное время* → торговые центры, деловые центры\n"
        text += "   Ожидаемый коэффициент: 1.3-1.7x\n"
    
    # Рекомендации по выходным
    if is_weekend:
        text += "\n🎉 *Выходной день* → центр, ТЦ, парки развлечений\n"
    
    text += "\n💡 *Совет:* Присылайте коэффициенты — прогнозы станут точнее!"
    
    return text


def get_district_forecast(district: str) -> str:
    """Прогноз по конкретному району"""
    
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Получаем данные по району
    cursor.execute("""
        SELECT COUNT(*) as count, AVG(coefficient) as avg_coef, MAX(coefficient) as max_coef
        FROM coefficients 
        WHERE district = ?
    """, (district,))

    row = cursor.fetchone()
    conn.close()

    if row and row['count'] > 0:
        return f"📊 *Район {district}*\n\n✅ Записей: {row['count']}\n📈 Средний коэффициент: *{round(row['avg_coef'], 2)}x*\n🏆 Максимальный: {round(row['max_coef'], 2)}x"
    else:
        return f"📊 *Район {district}*\n\nПока нет данных. Спасибо за первый коэффициент! 🌟"


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
    text += f"📈 Средний коэффициент: {round(row['avg_coefficient'] or 0, 2)}x\n"

    return text


def get_day_name(dow: int) -> str:
    days = ["понедельник", "вторник", "среда", "четверг", 
            "пятница", "суббота", "воскресенье"]
    return days[dow]


def get_top_moments() -> str:
    """Топ моменты с высокими коэффициентами"""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT district, coefficient, hour, day_of_week, timestamp
        FROM coefficients 
        WHERE coefficient >= 1.3
        ORDER BY coefficient DESC
        LIMIT 10
    """)

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return "Пока нет записей с высокими коэффициентами. Отправляйте данные!"

    text = "🏆 *Топ моменты с высокими коэффициентами*\n\n"
    for i, row in enumerate(rows, 1):
        try:
            date_str = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00')).strftime('%d.%m %H:%M')
        except:
            date_str = str(row['timestamp'])[:16]
        text += f"{i}. {row['district']}: *{row['coefficient']}x* ({date_str})\n"

    return text