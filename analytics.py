"""Аналитика и прогнозы"""

import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
import statistics
import config
import traffic_analyzer  # Добавляем импорт

# Создаём экземпляр анализатора пробок
traffic = traffic_analyzer.TrafficAnalyzer()


def get_current_recommendations() -> str:
    """Возвращает текущие рекомендации на основе исторических данных + пробок"""
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
    
    # Получаем данные о пробках
    traffic_data = traffic.get_traffic_data()
    traffic_score = traffic_data.get('score', 4)
    traffic_mult = traffic_data.get('level', {}).get('multiplier', 1.0)
    
    conn.close()

    if not rows:
        # Если нет исторических данных, даём рекомендацию на основе пробок и времени
        return get_fallback_recommendations(traffic_data)

    # Агрегируем по районам
    district_data = defaultdict(list)
    for row in rows:
        district_data[row['district']].append(row['coefficient'])

    # Считаем средние и надёжность с учётом пробок
    recommendations = []
    for district, coeffs in district_data.items():
        if len(coeffs) >= 2:
            avg = statistics.mean(coeffs)
            # Корректируем с учётом пробок
            if traffic_score >= 7:
                avg = avg * 1.2  # Сильные пробки +20% к спросу
            elif traffic_score >= 5:
                avg = avg * 1.1  # Средние пробки +10%
            
            reliability = min(len(coeffs) / 10, 1.0)
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
    
    # Добавляем информацию о пробках
    text += f"{traffic_data['level']['color']} *Пробки:* {traffic_data['level']['text']} ({traffic_score}/10)\n\n"

    for i, rec in enumerate(recommendations[:7], 1):
        stars = "⭐" * min(int(rec['avg_coefficient']), 3)
        text += (
            f"{i}. *{rec['district']}*\n"
            f"   Средний коэф: *{rec['avg_coefficient']}x* {stars}\n"
            f"   Максимум: {rec['max']}x | Записей: {rec['data_points']}\n"
            f"   Надёжность: {rec['reliability']}%\n\n"
        )

    # Добавляем совет по пробкам
    if traffic_score >= 7:
        text += "\n💡 *Совет:* Из-за сильных пробок лучше работать в центре — там выше спрос!\n"
    elif traffic_score >= 5:
        text += "\n💡 *Совет:* Пробки средние, рекомендую вокзалы и центр Москвы\n"
    else:
        text += "\n💡 *Совет:* Дороги свободны, можно работать в любом районе\n"

    return text


def get_fallback_recommendations(traffic_data) -> str:
    """Рекомендации, когда нет исторических данных"""
    now = datetime.now()
    hour = now.hour
    weekday = now.weekday()
    is_weekend = weekday >= 5
    traffic_score = traffic_data.get('score', 4)
    
    text = f"📍 *Рекомендации на сейчас* ({now.strftime('%H:%M')}, {get_day_name(weekday)})\n\n"
    text += f"{traffic_data['level']['color']} *Пробки:* {traffic_data['level']['text']} ({traffic_score}/10)\n\n"
    
    text += "📊 *На основе общих правил:*\n\n"
    
    # Рекомендации по времени
    if 8 <= hour <= 10:
        text += "• 🌅 *Утренний час пик* → езжайте к метро и вокзалам\n"
        text += "   Ожидаемый коэффициент: 1.8-2.2x\n"
    elif 17 <= hour <= 20:
        text += "• 🌆 *Вечерний час пик* → центр Москвы, ТЦ\n"
        text += "   Ожидаемый коэффициент: 1.8-2.5x\n"
    elif hour >= 23 or hour <= 5:
        text += "• 🌙 *Ночное время* → аэропорты, бары, клубы\n"
        text += "   Ожидаемый коэффициент: 2.0-3.0x\n"
    else:
        text += "• ☀️ *Дневное время* → торговые центры, деловые центры\n"
        text += "   Ожидаемый коэффициент: 1.3-1.7x\n"
    
    # Рекомендации по пробкам
    if traffic_score >= 7:
        text += "\n🚗 *Из-за сильных пробок* рекомендую:\n"
        text += "   • Курский вокзал\n"
        text += "   • Павелецкий вокзал\n"
        text += "   • Центр Москвы\n"
    
    text += "\n💡 *Совет:* Присылайте коэффициенты — прогнозы станут точнее!"
    
    return text


def get_traffic_forecast() -> str:
    """Прогноз пробок на сегодня"""
    now = datetime.now()
    traffic_forecast = traffic.get_traffic_forecast(6)  # на 6 часов вперёд
    
    text = "🚗 *Прогноз пробок на сегодня*\n\n"
    
    current_traffic = traffic.get_traffic_data()
    text += f"*Сейчас:* {current_traffic['level']['color']} {current_traffic['level']['text']} ({current_traffic['score']}/10)\n\n"
    
    text += "*Прогноз по часам:*\n"
    
    for f in traffic_forecast:
        text += f"   • {f['time']} - {f['level']['color']} {f['description']} ({f['score']}/10)\n"
    
    # Пиковые часы
    text += "\n⚠️ *Пиковые часы:*\n"
    if now.weekday() < 5:  # будни
        text += "   • Утро: 08:00-10:00\n"
        text += "   • Вечер: 17:00-19:00\n"
    else:
        text += "   • День: 12:00-15:00\n"
        text += "   • Вечер: 17:00-19:00\n"
    
    text += "\n💡 *Совет:* В пиковые часы лучше работать в центре — там выше коэффициенты!"
    
    return text


def get_district_traffic(district: str) -> str:
    """Анализ пробок для конкретного района"""
    district_traffic = traffic.get_district_traffic(district)
    
    text = f"🚗 *Анализ пробок: {district}*\n\n"
    text += f"Текущий уровень пробок: {district_traffic['level']['color']} {district_traffic['level']['text']}\n"
    text += f"Баллы: {district_traffic['adjusted_score']}/10\n\n"
    
    if district_traffic['adjusted_score'] >= 7:
        text += "⚠️ *Внимание:* В этом районе очень сильные пробки!\n"
        text += "   • Время поездки +50-70%\n"
        text += "   • Но спрос будет выше на 30-40%\n"
    elif district_traffic['adjusted_score'] >= 5:
        text += "🟠 *Средние пробки*\n"
        text += "   • Время поездки +20-30%\n"
        text += "   • Спрос немного выше\n"
    else:
        text += "🟢 *Дороги свободны*\n"
        text += "   • Время поездки обычное\n"
        text += "   • Спрос стандартный\n"
    
    return text


def get_current_recommendations() -> str:
    # ... (оставьте как есть, я уже обновил выше)
    pass


def get_district_forecast(district: str) -> str:
    # ... (оставьте как есть)
    pass


def get_weekly_report() -> str:
    # ... (оставьте как есть)
    pass


def get_day_name(dow: int) -> str:
    days = ["понедельник", "вторник", "среда", "четверг", 
            "пятница", "суббота", "воскресенье"]
    return days[dow]


def get_top_moments() -> str:
    # ... (оставьте как есть)
    pass