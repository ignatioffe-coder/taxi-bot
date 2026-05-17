"""
Генератор карты спроса для Москвы (только за последние 30 минут)
"""

import matplotlib.pyplot as plt
import io
import sqlite3
from datetime import datetime, timedelta

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
    "Курский вокзал": (55.757, 37.658),
    "Павелецкий вокзал": (55.731, 37.635),
    "Киевский вокзал": (55.743, 37.566),
    "Белорусский вокзал": (55.777, 37.580),
    "Москва-Сити": (55.750, 37.540),
}

MAP_BOUNDS = {
    'min_lat': 55.70,
    'max_lat': 55.85,
    'min_lon': 37.45,
    'max_lon': 37.75,
}


def get_district_coefficients():
    """Получает средние коэффициенты по районам ТОЛЬКО ЗА ПОСЛЕДНИЕ 30 МИНУТ"""
    conn = sqlite3.connect('taxi_data.db')
    cursor = conn.cursor()
    
    # Вычисляем время 30 минут назад в правильном формате
    now = datetime.now()
    thirty_min_ago = (now - timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')
    
    print(f"DEBUG: Сейчас: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"DEBUG: Берём данные после: {thirty_min_ago}")
    
    try:
        # Используем строковое сравнение для дат
        cursor.execute("""
            SELECT district, AVG(coefficient) as avg_coef, COUNT(*) as count, MAX(timestamp) as last_time
            FROM coefficients
            WHERE timestamp >= ?
            GROUP BY district
            ORDER BY avg_coef DESC
        """, (thirty_min_ago,))
        
        results = cursor.fetchall()
        print(f"DEBUG: Найдено районов со свежими данными: {len(results)}")
        for row in results:
            print(f"  {row[0]}: {round(row[1], 1)}x ({row[2]} записей, последняя: {row[3]})")
            
    except Exception as e:
        print(f"Ошибка БД: {e}")
        results = []
    
    conn.close()
    
    coefs = {}
    for row in results:
        coefs[row[0]] = round(row[1], 1)
    
    if not coefs:
        print("DEBUG: Нет свежих данных за последние 30 минут!")
        
        # Показываем последние записи в БД для отладки
        debug_conn = sqlite3.connect('taxi_data.db')
        debug_cursor = debug_conn.cursor()
        debug_cursor.execute("SELECT timestamp, district, coefficient FROM coefficients ORDER BY timestamp DESC LIMIT 5")
        print("DEBUG: Последние 5 записей в БД:")
        for row in debug_cursor.fetchall():
            print(f"  {row[0]} | {row[1]} | {row[2]}x")
        debug_conn.close()
    
    return coefs


def create_demand_map():
    """Создаёт карту спроса на основе данных за последние 30 минут"""
    coefs = get_district_coefficients()
    
    if not coefs:
        fig, ax = plt.subplots(figsize=(10, 8))
        ax.text(0.5, 0.6, "Нет свежих данных за последние 30 минут", 
                ha='center', va='center', fontsize=14, transform=ax.transAxes)
        ax.text(0.5, 0.4, "📍 Нажмите «Прислать коэффициент»\nчтобы добавить свежие данные", 
                ha='center', va='center', fontsize=11, transform=ax.transAxes, color='gray')
        ax.set_title(f'Карта спроса\n{datetime.now().strftime("%d.%m.%Y %H:%M")}', fontsize=12)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis('off')
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
        buf.seek(0)
        plt.close()
        return buf
    
    # Создаём график
    fig, ax = plt.subplots(figsize=(14, 10))
    
    # Рисуем МКАД
    mкад = plt.Circle((37.62, 55.75), 0.35, fill=False, edgecolor='gray', linewidth=1.5, linestyle='--')
    ax.add_patch(mкад)
    
    # Отображаем районы
    for district, coef in coefs.items():
        if district not in DISTRICTS_COORDS:
            print(f"  ⚠️ Район '{district}' не найден в координатах")
            continue
            
        coords = DISTRICTS_COORDS[district]
        
        # Цвет от зелёного до фиолетового
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
        
        # Рисуем маркер
        ax.scatter(coords[1], coords[0], s=size, c=color, alpha=0.7, 
                  edgecolors='black', linewidth=1.5, marker=marker, zorder=5)
        
        # Подписываем
        ax.annotate(f"{district}\n{coef}x", 
                   (coords[1], coords[0]),
                   ha='center', va='center',
                   fontsize=8, fontweight='bold', zorder=6)
    
    # Настройки карты
    ax.set_xlim(MAP_BOUNDS['min_lon'], MAP_BOUNDS['max_lon'])
    ax.set_ylim(MAP_BOUNDS['min_lat'], MAP_BOUNDS['max_lat'])
    ax.set_xlabel('Долгота', fontsize=12)
    ax.set_ylabel('Широта', fontsize=12)
    ax.set_title(f'Актуальная карта спроса такси в Москве\n(за последние 30 минут)\n{datetime.now().strftime("%d.%m.%Y %H:%M")}', fontsize=12)
    
    # Легенда
    legend_elements = [
        plt.scatter([], [], s=100, c='#2ecc71', marker='o', label='Низкий (<1.3x)'),
        plt.scatter([], [], s=100, c='#f1c40f', marker='s', label='Средний (1.3-1.7x)'),
        plt.scatter([], [], s=100, c='#e67e22', marker='^', label='Высокий (1.7-2.1x)'),
        plt.scatter([], [], s=100, c='#e74c3c', marker='D', label='Очень высокий (2.1-2.5x)'),
        plt.scatter([], [], s=100, c='#8e44ad', marker='*', label='Максимальный (>2.5x)'),
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=9)
    
    ax.grid(True, alpha=0.2)
    
    # Сохраняем в байты
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white')
    buf.seek(0)
    plt.close()
    
    return buf


def generate_map():
    """Генерирует карту"""
    return create_demand_map()