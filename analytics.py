"""Аналитика и прогнозы"""

import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
import statistics
import config

# === ДЛЯ КАРТЫ ===
import folium
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from PIL import Image
import io
import os

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


def get_color_by_coefficient(coef: float) -> str:
    """Возвращает цвет точки по коэффициенту"""
    if coef < 1.3:
        return "#2ecc71"  # Зелёный
    elif coef < 1.7:
        return "#f1c40f"  # Жёлтый
    elif coef < 2.1:
        return "#e67e22"  # Оранжевый
    elif coef < 2.5:
        return "#e74c3c"  # Красный
    else:
        return "#9b59b6"  # Фиолетовый


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


def generate_map() -> str:
    """
    Генерирует красивую карту Москвы с точками спроса.
    Возвращает путь к сохранённому PNG-файлу.
    """
    # Получаем данные за последние 30 минут
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

    # Если нет свежих данных — берём последние 50 записей
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

    # Агрегируем по районам (берём средний за период)
    district_stats = defaultdict(lambda: {"coefs": [], "last_time": None})
    for row in rows:
        d = row['district']
        district_stats[d]["coefs"].append(row['coefficient'])
        if district_stats[d]["last_time"] is None:
            district_stats[d]["last_time"] = row['timestamp']

    # Создаём карту — центр Москвы
    m = folium.Map(
        location=[55.7558, 37.6173],
        zoom_start=10,
        tiles="CartoDB dark_matter"  # Тёмная стильная подложка
    )

    # Добавляем точки
    points_added = 0
    for district, stats in district_stats.items():
        if district not in DISTRICT_COORDS:
            continue
        
        lat, lon = DISTRICT_COORDS[district]
        avg_coef = statistics.mean(stats["coefs"])
        color = get_color_by_coefficient(avg_coef)
        
        # Размер точки зависит от количества данных
        radius = 8 + min(len(stats["coefs"]) * 2, 15)
        
        folium.CircleMarker(
            location=[lat, lon],
            radius=radius,
            popup=f"{district}<br>Коэффициент: {avg_coef:.2f}x<br>Записей: {len(stats['coefs'])}",
            tooltip=f"{district}: {avg_coef:.2f}x",
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.7,
            weight=2
        ).add_to(m)
        points_added += 1

    # Если нет данных вообще — показываем демо-точку
    if points_added == 0:
        folium.CircleMarker(
            location=[55.7614, 37.5650],
            radius=12,
            popup="Пресненский<br>Демо: 1.5x",
            tooltip="Пресненский: 1.5x",
            color="#f1c40f",
            fill=True,
            fill_color="#f1c40f",
            fill_opacity=0.7,
            weight=2
        ).add_to(m)

    # Добавляем легенду
    legend_html = '''
    <div style="position: fixed; 
                bottom: 20px; right: 20px; 
                background-color: rgba(30,30,30,0.9);
                border: 1px solid #444;
                border-radius: 8px;
                padding: 12px;
                font-size: 13px;
                color: white;
                z-index: 9999;
                font-family: Arial, sans-serif;">
        <b style="font-size:14px;">Коэффициент</b><br>
        <span style="color:#2ecc71;">●</span> Низкий (&lt;1.3x)<br>
        <span style="color:#f1c40f;">●</span> Средний (1.3-1.7x)<br>
        <span style="color:#e67e22;">●</span> Высокий (1.7-2.1x)<br>
        <span style="color:#e74c3c;">●</span> Очень высокий (2.1-2.5x)<br>
        <span style="color:#9b59b6;">●</span> Максимальный (&gt;2.5x)<br>
        <hr style="border-color:#555;margin:6px 0;">
        <span style="color:#aaa;font-size:11px;">Обновлено: ''' + datetime.now().strftime('%H:%M') + '''</span>
    </div>
    '''
    m.get_root().html.add_child(folium.Element(legend_html))

    # Сохраняем HTML
    os.makedirs("maps", exist_ok=True)
    html_path = "maps/taxi_map.html"
    m.save(html_path)

    # Конвертируем HTML → PNG через selenium (или делаем скриншот через matplotlib)
    # Пока сохраняем HTML — Telegram может отправлять его как файл, 
    # но лучше сделать PNG. Для этого используем простой метод:
    
    # Метод: рендерим через matplotlib + contextily
    return _render_map_png(district_stats)


def _render_map_png(district_stats) -> str:
    """Рендерит карту в PNG через matplotlib с подложкой"""
    try:
        import contextily as ctx
        import geopandas as gpd
        from shapely.geometry import Point
        
        # Создаём GeoDataFrame
        data = []
        for district, stats in district_stats.items():
            if district not in DISTRICT_COORDS:
                continue
            lat, lon = DISTRICT_COORDS[district]
            data.append({
                'district': district,
                'lat': lat,
                'lon': lon,
                'coef': statistics.mean(stats["coefs"]),
                'count': len(stats["coefs"]),
                'geometry': Point(lon, lat)  # GeoPandas: x=lon, y=lat
            })
        
        if not data:
            # Демо-данные
            data = [{
                'district': 'Пресненский',
                'lat': 55.7614,
                'lon': 37.5650,
                'coef': 1.5,
                'count': 1,
                'geometry': Point(37.5650, 55.7614)
            }]
        
        gdf = gpd.GeoDataFrame(data, crs="EPSG:4326")
        # Переводим в метры для contextily
        gdf_mercator = gdf.to_crs(epsg=3857)
        
        fig, ax = plt.subplots(figsize=(12, 10))
        
        # Рисуем подложку
        try:
            ctx.add_basemap(ax, crs=gdf_mercator.crs.to_string(), 
                          source=ctx.providers.CartoDB.DarkMatter,
                          zoom=10)
        except Exception:
            # Fallback — просто фон
            ax.set_facecolor('#1a1a2e')
        
        # Рисуем точки
        for idx, row in gdf_mercator.iterrows():
            color = get_color_by_coefficient(row['coef'])
            size = 200 + row['count'] * 80
            
            ax.scatter(row.geometry.x, row.geometry.y, 
                      s=size, c=color, alpha=0.7, 
                      edgecolors='white', linewidths=1.5, zorder=5)
            
            # Подпись района
            ax.annotate(f"{row['district']}\n{row['coef']:.1f}x",
                       xy=(row.geometry.x, row.geometry.y),
                       xytext=(8, 8), textcoords='offset points',
                       fontsize=9, color='white', fontweight='bold',
                       bbox=dict(boxstyle='round,pad=0.3', 
                                facecolor='black', alpha=0.6, edgecolor='none'))
        
        # Настройки
        ax.set_xlim(gdf_mercator.geometry.x.min() - 5000, gdf_mercator.geometry.x.max() + 5000)
        ax.set_ylim(gdf_mercator.geometry.y.min() - 5000, gdf_mercator.geometry.y.max() + 5000)
        ax.set_aspect('equal')
        ax.axis('off')
        
        # Заголовок
        now = datetime.now()
        fig.suptitle(f'Актуальная карта спроса такси в Москве\n(за последние 30 минут) {now.strftime("%d.%m.%Y %H:%M")}',
                    fontsize=14, color='white', fontweight='bold', y=0.98)
        
        # Легенда
        legend_elements = [
            mpatches.Patch(facecolor='#2ecc71', edgecolor='white', label='Низкий (<1.3x)'),
            mpatches.Patch(facecolor='#f1c40f', edgecolor='white', label='Средний (1.3-1.7x)'),
            mpatches.Patch(facecolor='#e67e22', edgecolor='white', label='Высокий (1.7-2.1x)'),
            mpatches.Patch(facecolor='#e74c3c', edgecolor='white', label='Очень высокий (2.1-2.5x)'),
            mpatches.Patch(facecolor='#9b59b6', edgecolor='white', label='Максимальный (>2.5x)'),
        ]
        ax.legend(handles=legend_elements, loc='upper right', 
                 facecolor='#1a1a2e', edgecolor='#444', labelcolor='white',
                 fontsize=10)
        
        plt.tight_layout()
        
        os.makedirs("maps", exist_ok=True)
        png_path = "maps/taxi_map.png"
        plt.savefig(png_path, dpi=150, bbox_inches='tight', 
                   facecolor='#1a1a2e', edgecolor='none')
        plt.close()
        
        return png_path
        
    except Exception as e:
        print(f"Ошибка рендера карты: {e}")
        # Fallback — возвращаем путь к HTML
        return "maps/taxi_map.html"


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
