"""
Генератор карты спроса для Москвы
"""

import matplotlib.pyplot as plt
import io
import sqlite3
from datetime import datetime, timedelta

import config


def get_moscow_time():
    """Возвращает московское время (UTC+3)"""
    return datetime.now() + timedelta(hours=3)


# Координаты районов Москвы
DISTRICTS_COORDS = {
    "Центр (Тверская, Арбат)": (55.760, 37.615),
    "Хамовники": (55.735, 37.580),
    "Пресненский": (55.762, 37.570),
    "Таганский": (55.740, 37.670),
    "Басманный": (55.770, 37.670),
    "Аэропорт": (55.800, 37.530),
    "Дорогомилово": (55.745, 37.555),
    "Сокольники": (55.790, 37.680),
}

MAP_BOUNDS = {
    'min_lat': 55.70,
    'max_lat': 55.85,
    'min_lon': 37.45,
    'max_lon': 37.75,
}


def get_district_coefficients():
    """Получает средние коэффициенты по районам из БД"""
    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT district, AVG(coefficient) as avg_coef, COUNT(*) as count
            FROM coefficients
            GROUP BY district
            ORDER BY avg_coef DESC
        """)
        results = cursor.fetchall()
    except Exception as e:
        print(f"Ошибка БД: {e}")
        results = []
    finally:
        conn.close()

    coefs = {}
    for row in results:
        coefs[row[0]] = round(row[1], 1)

    return coefs


def create_demand_map():
    """Создаёт карту спроса"""
    coefs = get_district_coefficients()

    if not coefs:
        fig, ax = plt.subplots(figsize=(10, 8))
        ax.text(0.5, 0.5, "Нет данных о коэффициентах\n\nНажмите «Прислать коэффициент»",
                ha='center', va='center', fontsize=14)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis('off')

        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        buf.seek(0)
        plt.close()
        return buf

    fig, ax = plt.subplots(figsize=(12, 10))

    # Рисуем МКАД
    mkad = plt.Circle((37.62, 55.75), 0.35, fill=False, edgecolor='gray', linewidth=1.5, linestyle='--')
    ax.add_patch(mkad)

    for district, coef in coefs.items():
        if district not in DISTRICTS_COORDS:
            continue

        coords = DISTRICTS_COORDS[district]

        if coef < 1.3:
            color = '#2ecc71'
            marker = 'o'
            size = 400
        elif coef < 1.7:
            color = '#f1c40f'
            marker = 's'
            size = 500
        elif coef < 2.1:
            color = '#e67e22'
            marker = '^'
            size = 600
        elif coef < 2.5:
            color = '#e74c3c'
            marker = 'D'
            size = 700
        else:
            color = '#8e44ad'
            marker = '*'
            size = 800

        ax.scatter(coords[1], coords[0], s=size, c=color, alpha=0.7,
                   edgecolors='black', linewidth=1.5, marker=marker, zorder=5)
        ax.annotate(f"{district}\n{coef}x",
                    (coords[1], coords[0]),
                    ha='center', va='center',
                    fontsize=8, fontweight='bold', zorder=6)

    ax.set_xlim(MAP_BOUNDS['min_lon'], MAP_BOUNDS['max_lon'])
    ax.set_ylim(MAP_BOUNDS['min_lat'], MAP_BOUNDS['max_lat'])
    ax.set_xlabel('Долгота', fontsize=12)
    ax.set_ylabel('Широта', fontsize=12)

    ax.set_title(f'Карта спроса такси в Москве\n{get_moscow_time().strftime("%d.%m.%Y %H:%M")}', fontsize=12)

    legend_elements = [
        plt.scatter([], [], s=100, c='#2ecc71', marker='o', label='Низкий (<1.3x)'),
        plt.scatter([], [], s=100, c='#f1c40f', marker='s', label='Средний (1.3-1.7x)'),
        plt.scatter([], [], s=100, c='#e67e22', marker='^', label='Высокий (1.7-2.1x)'),
        plt.scatter([], [], s=100, c='#e74c3c', marker='D', label='Очень высокий (2.1-2.5x)'),
        plt.scatter([], [], s=100, c='#8e44ad', marker='*', label='Максимальный (>2.5x)'),
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=9)
    ax.grid(True, alpha=0.2)

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white')
    buf.seek(0)
    plt.close()

    return buf


def generate_map():
    return create_demand_map()
