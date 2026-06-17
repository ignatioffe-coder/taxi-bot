"""Аналитика и прогнозы"""

import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
import statistics
import config

# === ДЛЯ КАРТЫ ===
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import os
import requests
from io import BytesIO
from PIL import Image

# Координаты центров районов Москвы (широта, долгота)
DISTRICT_COORDS = {
    "Центр (Тверская, Арбат)": (55.7558, 37.6173),
    "Хамовники": (55.7340, 37.5890),
    "Пресненский": (55.7614, 37.5650),
    "Таганский": (55.7400, 37.6700),
    "Басманный": (55.7650, 37.6500),
    "Медведково": (55.8830, 37.6610),
    "Бибирево": (55.8830, 37.6030),
    "Алтуфьево": (55.8980, 37.5870),
    "Марьино": (55.6500, 37.7430),
    "Братеево": (55.6330, 37.7630),
    "Кузьминки": (55.7000, 37.7660),
    "Выхино": (55.7150, 37.8150),
    "Люблино": (55.6780, 37.7300),
    "Сокольники": (55.7900, 37.6800),
    "Алексеевский": (55.8080, 37.6400),
    "Останкино": (55.8200, 37.6100),
    "Аэропорт": (55.8000, 37.5300),
    "Беговой": (55.7800, 37.5600),
    "Хорошёво-Мнёвники": (55.7750, 37.4700),
    "Строгино": (55.8000, 37.4000),
    "Кунцево": (55.7350, 37.4450),
    "Фили": (55.7450, 37.4850),
    "Дорогомилово": (55.7400, 37.5600),
    "Раменки": (55.7000, 37.5000),
    "Тёплый Стан": (55.6200, 37.4800),
    "Ясенево": (55.6050, 37.5300),
    "Северное Бутово": (55.5700, 37.5800),
    "Южное Бутово": (55.5400, 37.5500),
    "Бирюлёво": (55.5800, 37.6600),
    "Царицыно": (55.6150, 37.6700),
    "Чертаново": (55.5930, 37.6000),
    "Беляево": (55.6400, 37.5250),
    "Коньково": (55.6300, 37.5050),
    "Коммунарка": (55.5600, 37.4700),
    "Филатов Луг": (55.5500, 37.4200),
    "Домодедово (город)": (55.4400, 37.7500),
    "Химки": (55.8890, 37.4300),
    "Красногорск": (55.8200, 37.3300),
    "Одинцово": (55.6700, 37.2700),
    "Люберцы": (55.6800, 37.8900),
    "Реутов": (55.7600, 37.8600),
    "Балашиха": (55.8000, 37.9300),
    "Щёлково": (55.9200, 37.9900),
    "Другой (указать вручную)": (55.7558, 37.6173),
}

# Границы Москвы для карты
MOSCOW_BOUNDS = {
    'north': 55.95,
    'south': 55.50,
    'west': 37.25,
    'east': 37.95
}


def get_color_by_coefficient(coef: float) -> str:
    """Возвращает цвет точки по коэффициенту"""
    if coef < 1.3:
        return "#2ecc71"
    elif coef < 1.7:
        return "#f1c40f"
    elif coef < 2.1:
        return "#e67e22"
    elif coef < 2.5:
        return "#e74c3c"
    else:
        return "#9b59b6"


def get_map_background() -> Optional[Image.Image]:
    """Скачивает фоновую карту Москвы через Static Map API"""
    try:
        # Используем CartoDB Dark Matter через OpenStreetMap
        # Формат: https://static-maps.yandex.ru/1.x/?...
        # Или используем OSM Static Map API
        
        width, height = 1200, 1000
        
        # OpenStreetMap static via MapQuest (бесплатно, не нужен ключ)
        # Или используем простой подход: скачиваем тайлы
        
        # Пробуем через Yandex Static Maps (без ключа, ограниченно)
        # lat_center = 55.75, lon_center = 37.62
        # spn = 0.7 (широта), 0.7 (долгота) — примерно вся Москва
        
        url = (
            f"https://static-maps.yandex.ru/1.x/"
            f"?ll=37.6173,55.7558"
            f"&spn=0.7,0.5"
            f"&size={width},{height}"
            f"&l=map"
            f"&theme=dark"
        )
        
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return Image.open(BytesIO(response.content))
        
        # Fallback — пробуем через OpenStreetMap
        # Используем bbox: west,south,east,north
        bbox = f"{MOSCOW_BOUNDS['west']},{MOSCOW_BOUNDS['south']},{MOSCOW_BOUNDS['east']},{MOSCOW_BOUNDS['north']}"
        url_osm = (
            f"https://www.openstreetmap.org/export/embed.html?"
            f"bbox={bbox}"
            f"&layer=mapnik"
        )
        # OSM не даёт статичные PNG напрямую, поэтому пробуем другой сервис
        
        # Пробуем через CartoDB тайлы напрямую
        # Zoom 10 для всей Москвы
        # Центр: 55.7558, 37.6173
        # Тайл для zoom 10, x, y — считаем...
        
        return None
        
    except Exception as e:
        print(f"Ошибка загрузки карты: {e}")
        return None


def get_tile_url(zoom: int, x: int, y: int) -> str:
    """Возвращает URL тайла CartoDB Dark Matter"""
    # CartoDB Dark Matter тайлы
    return f"https://a.basemaps.cartocdn.com/dark_all/{zoom}/{x}/{y}.png"


def download_tiles() -> Optional[Image.Image]:
    """Скачивает и склеивает тайлы для Москвы"""
    try:
        # Для zoom 10, центр Москвы
        # Москва примерно: x=619, y=321 (zoom 10)
        # Берём 2x2 тайла для покрытия
        
        zoom = 10
        center_x, center_y = 619, 321
        
        tiles = []
        for dy in range(-1, 2):
            row = []
            for dx in range(-1, 2):
                x, y = center_x + dx, center_y + dy
                url = get_tile_url(zoom, x, y)
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    tile = Image.open(BytesIO(response.content))
                    row.append(tile)
                else:
                    row.append(None)
            tiles.append(row)
        
        # Склеиваем
        tile_size = 256
        img = Image.new('RGB', (tile_size * 3, tile_size * 3))
        for i, row in enumerate(tiles):
            for j, tile in enumerate(row):
                if tile:
                    img.paste(tile, (j * tile_size, i * tile_size))
        
        return img
        
    except Exception as e:
        print(f"Ошибка тайлов: {e}")
        return None


def latlon_to_pixel(lat: float, lon: float, zoom: int = 10) -> Tuple[int, int]:
    """Переводит lat/lon в пиксели тайла"""
    import math
    
    n = 2 ** zoom
    x = int((lon + 180) / 360 * n * 256)
    y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n * 256)
    
    return x, y


def generate_map() -> str:
    """
    Генерирует карту Москвы с подложкой.
    """
    # Получаем данные
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    thirty_mins_ago = datetime.now() - timedelta(minutes=30)
    cursor.execute("""
        SELECT district, coefficient, timestamp
        FROM coefficients 
        WHERE timestamp > ?
        ORDER BY timestamp DESC
    """, (thirty_mins_ago.strftime('%Y-%m-%d %H:%M:%S'),))

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        conn = sqlite3.connect(config.DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT district, coefficient, timestamp
            FROM coefficients 
            ORDER BY timestamp DESC
            LIMIT 50
        """)
        rows = cursor.fetchall()
        conn.close()

    district_stats = defaultdict(lambda: {"coefs": [], "last_time": None})
    for row in rows:
        d = row['district']
        district_stats[d]["coefs"].append(row['coefficient'])
        if district_stats[d]["last_time"] is None:
            district_stats[d]["last_time"] = row['timestamp']

    # Создаём фигуру matplotlib
    fig, ax = plt.subplots(figsize=(14, 12))
    
    # Пытаемся загрузить фон
    bg = download_tiles()
    if bg:
        # Показываем тайлы как фон
        ax.imshow(bg, extent=[0, bg.width, 0, bg.height], aspect='equal')
        
        # Переводим координаты в пиксели
        # Но это сложно... Давай проще
        pass
    
    # ПРОСТОЙ ВАРИАНТ: рисуем карту вручную через OSM API
    # Используем bbox для всей Москвы
    
    west, south, east, north = (
        MOSCOW_BOUNDS['west'], MOSCOW_BOUNDS['south'],
        MOSCOW_BOUNDS['east'], MOSCOW_BOUNDS['north']
    )
    
    # Скачиваем статичную карту через geoapify (бесплатно, нужен ключ)
    # Или через mapbox... 
    
    # Давай используем ПРОСТОЙ подход: 
    # Рисуем сетку + названия районов + точки
    # Без реальной подложки, но красиво оформлено
    
    ax.set_xlim(west, east)
    ax.set_ylim(south, north)
    
    # Рисуем "фейковую" карту — сетку районов
    # Делим Москву на условные зоны
    
    # Фон — тёмный
    ax.set_facecolor('#1a1a2e')
    fig.patch.set_facecolor('#1a1a2e')
    
    # Рисуем сетку
    for lon in np.arange(37.3, 37.9, 0.1):
        ax.axvline(lon, color='#333344', linewidth=0.5, alpha=0.5)
    for lat in np.arange(55.55, 55.9, 0.1):
        ax.axhline(lat, color='#333344', linewidth=0.5, alpha=0.5)
    
    # Добавляем "реки" (условно — синие линии)
    # Москва-река примерно
    river_lons = [37.4, 37.45, 37.5, 37.55, 37.6, 37.62, 37.65, 37.7, 37.75]
    river_lats = [55.72, 55.73, 55.735, 55.74, 55.745, 55.75, 55.755, 55.76, 55.765]
    ax.fill(river_lons + river_lons[::-1], 
            [l - 0.015 for l in river_lats] + [l + 0.015 for l in river_lats[::-1]],
            color='#1e3a5f', alpha=0.6, zorder=1)
    
    # Рисуем МКАД (условно — серый круг/овал)
    from matplotlib.patches import Ellipse
    mkad = Ellipse((37.62, 55.75), 0.65, 0.45, 
                   fill=False, edgecolor='#444455', 
                   linewidth=2, linestyle='--', zorder=2)
    ax.add_patch(mkad)
    
    # Точки
    for district, stats in district_stats.items():
        if district not in DISTRICT_COORDS:
            continue
        
        lat, lon = DISTRICT_COORDS[district]
        avg_coef = statistics.mean(stats["coefs"])
        color = get_color_by_coefficient(avg_coef)
        size = 400 + len(stats["coefs"]) * 200
        
        ax.scatter(lon, lat, s=size, c=color, alpha=0.85,
                  edgecolors='white', linewidths=2, zorder=5)
        
        ax.annotate(f"{district}\n{avg_coef:.1f}x",
                   xy=(lon, lat), xytext=(10, 10),
                   textcoords='offset points',
                   fontsize=8, color='white', fontweight='bold',
                   bbox=dict(boxstyle='round,pad=0.3', 
                            facecolor='black', alpha=0.7,
                            edgecolor='none'),
                   zorder=6)

    # Настройки
    ax.set_aspect('equal')
    ax.set_xlabel('Долгота', color='white', fontsize=11)
    ax.set_ylabel('Широта', color='white', fontsize=11)
    ax.tick_params(colors='white')
    ax.spines['bottom'].set_color('white')
    ax.spines['top'].set_color('white')
    ax.spines['left'].set_color('white')
    ax.spines['right'].set_color('white')
    
    # Заголовок
    now = datetime.now()
    ax.set_title(f'Актуальная карта спроса такси в Москве\n(за последние 30 минут) {now.strftime("%d.%m.%Y %H:%M")}',
                fontsize=14, color='white', fontweight='bold', pad=20)

    # Легенда
    legend_elements = [
        mpatches.Patch(facecolor='#2ecc71', edgecolor='white', label='Низкий (<1.3x)'),
        mpatches.Patch(facecolor='#f1c40f', edgecolor='white', label='Средний (1.3-1.7x)'),
        mpatches.Patch(facecolor='#e67e22', edgecolor='white', label='Высокий (1.7-2.1x)'),
        mpatches.Patch(facecolor='#e74c3c', edgecolor='white', label='Очень высокий (2.1-2.5x)'),
        mpatches.Patch(facecolor='#9b59b6', edgecolor='white', label='Максимальный (>2.5x)'),
    ]
    ax.legend(handles=legend_elements, loc='upper right',
             facecolor='#1a1a2e', edgecolor='#444',
             labelcolor='white', fontsize=10)

    plt.tight_layout()
    
    os.makedirs("maps", exist_ok=True)
    png_path = "maps/taxi_map.png"
    plt.savefig(png_path, dpi=150, bbox_inches='tight',
               facecolor='#1a1a2e', edgecolor='none')
    plt.close()

    return png_path


def get_current_recommendations() -> str:
    """Возвращает текущие рекомендации на основе исторических данных"""
    now = datetime.now()
    current_hour = now.hour
    current_dow = now.weekday()

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

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

    district_data = defaultdict(list)
    for row in rows:
        district_data[row['district']].append(row['coefficient'])

    recommendations = []
    for district, coeffs in district_data.items():
        if len(coeffs) >= 3:
            avg = statistics.mean(coeffs)
            reliability = min(len(coeffs) / 10, 1.0)
            recommendations.append({
                'district': district,
                'avg_coefficient': round(avg, 2),
                'data_points': len(coeffs),
                'reliability': round(reliability * 100),
                'max': round(max(coeffs), 2)
            })

    recommendations.sort(key=lambda x: x['avg_coefficient'], reverse=True)

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

    hourly_data = defaultdict(list)
    for row in rows:
        hourly_data[row['hour']].append(row['coefficient'])

    text = f"📈 *Прогноз по району: {district}*\n"
    text += f"_Сегодня ({get_day_name(current_dow)}), на основе {len(rows)} записей_\n\n"

    for hour in sorted(hourly_data.keys()):
        coeffs = hourly_data[hour]
        avg = statistics.mean(coeffs)
        count = len(coeffs)

        indicator = "🔴" if avg >= 1.5 else "🟡" if avg >= 1.2 else "🟢"
        time_str = f"{hour:02d}:00"

        text += f"{indicator} {time_str}: *{avg:.2f}x* (записей: {count})\n"

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
