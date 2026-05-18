"""
Анализ пробок в Москве
Бесплатные источники: Яндекс.Карты, Google Maps, OpenStreetMap
"""

import requests
import json
from datetime import datetime, timedelta
import random
from bs4 import BeautifulSoup
import re

class TrafficAnalyzer:
    def __init__(self):
        self.cache = {}
        self.cache_time = 0
        
    def get_traffic_data(self, force_update=False):
        """Получает данные о пробках"""
        now = datetime.now()
        
        # Используем кэш на 5 минут (чтобы не спамить запросы)
        if not force_update and self.cache and (now - self.cache_time).seconds < 300:
            return self.cache
        
        # Пробуем получить данные из разных источников
        data = self._get_traffic_from_yandex()
        
        if not data:
            data = self._get_traffic_by_time()
        
        self.cache = data
        self.cache_time = now
        return data
    
    def _get_traffic_from_yandex(self):
        """Парсинг пробок с Яндекс.Карт"""
        try:
            # Яндекс.Карты Москва
            url = "https://yandex.ru/maps/213/moscow/"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Ищем данные о пробках в JavaScript
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string and 'traffic' in script.string.lower():
                    # Ищем числа от 1 до 10 (баллы пробок)
                    numbers = re.findall(r'\b([1-9]|10)\b', script.string)
                    if numbers:
                        score = int(numbers[0])
                        return {
                            'score': score,
                            'level': self._get_traffic_level(score),
                            'source': 'yandex',
                            'update_time': datetime.now().isoformat()
                        }
            
            # Если не нашли, пробуем альтернативный метод
            return None
            
        except Exception as e:
            print(f"Ошибка получения пробок с Яндекс: {e}")
            return None
    
    def _get_traffic_by_time(self):
        """Расчёт пробок на основе времени и дня недели"""
        now = datetime.now()
        hour = now.hour
        weekday = now.weekday()
        is_weekend = weekday >= 5
        
        # Базовая логика пробок
        if is_weekend:
            # Выходные: пробок меньше
            if 11 <= hour <= 15:
                score = 4  # Дневной пик в выходные
            elif 17 <= hour <= 20:
                score = 5  # Вечерний пик
            else:
                score = 2
        else:
            # Будни
            if 8 <= hour <= 10:
                score = 8  # Утренний час пик
            elif 17 <= hour <= 20:
                score = 9  # Вечерний час пик
            elif 12 <= hour <= 15:
                score = 5  # Дневная загрузка
            elif 22 <= hour <= 6:
                score = 1  # Ночь
            else:
                score = 3
        
        return {
            'score': score,
            'level': self._get_traffic_level(score),
            'source': 'time_based',
            'update_time': datetime.now().isoformat()
        }
    
    def _get_traffic_level(self, score):
        """Преобразует баллы в текстовый уровень"""
        if score >= 8:
            return {'text': 'Очень сильные пробки', 'color': '🔴', 'multiplier': 1.3}
        elif score >= 6:
            return {'text': 'Сильные пробки', 'color': '🟠', 'multiplier': 1.2}
        elif score >= 4:
            return {'text': 'Средние пробки', 'color': '🟡', 'multiplier': 1.1}
        elif score >= 2:
            return {'text': 'Лёгкие пробки', 'color': '🟢', 'multiplier': 1.0}
        else:
            return {'text': 'Свободные дороги', 'color': '🔵', 'multiplier': 0.95}
    
    def get_traffic_forecast(self, hours_ahead=1):
        """Прогноз пробок на ближайшие часы"""
        now = datetime.now()
        forecast = []
        
        for i in range(hours_ahead):
            future_time = now + timedelta(hours=i)
            hour = future_time.hour
            weekday = future_time.weekday()
            is_weekend = weekday >= 5
            
            if is_weekend:
                if 11 <= hour <= 15:
                    score = 4
                    desc = "Дневное оживление"
                elif 17 <= hour <= 20:
                    score = 5
                    desc = "Вечерний рост"
                else:
                    score = 2
                    desc = "Спокойно"
            else:
                if 8 <= hour <= 10:
                    score = 8
                    desc = "Утренний час пик"
                elif 17 <= hour <= 20:
                    score = 9
                    desc = "Вечерний час пик"
                elif 12 <= hour <= 15:
                    score = 5
                    desc = "Дневная загрузка"
                else:
                    score = 2
                    desc = "Снижение"
            
            forecast.append({
                'hour': hour,
                'time': future_time.strftime('%H:%M'),
                'score': score,
                'description': desc,
                'level': self._get_traffic_level(score)
            })
        
        return forecast
    
    def get_district_traffic(self, district):
        """Влияние пробок на конкретный район"""
        traffic = self.get_traffic_data()
        
        # Коэффициенты для разных районов
        district_multipliers = {
            'Центр (Тверская, Арбат)': 1.3,
            'Хамовники': 1.2,
            'Пресненский': 1.2,
            'Таганский': 1.1,
            'Аэропорт': 1.0,
            'Курский вокзал': 1.4,
            'Павелецкий вокзал': 1.3,
            'Киевский вокзал': 1.3,
        }
        
        multiplier = district_multipliers.get(district, 1.0)
        final_score = min(10, traffic['score'] * multiplier)
        
        return {
            'district': district,
            'base_score': traffic['score'],
            'adjusted_score': round(final_score, 1),
            'multiplier': multiplier,
            'level': self._get_traffic_level(final_score)
        }


def get_traffic_report():
    """Получить полный отчёт о пробках для бота"""
    analyzer = TrafficAnalyzer()
    traffic = analyzer.get_traffic_data()
    forecast = analyzer.get_traffic_forecast(3)
    
    report = f"""
{traffic['level']['color']} **Пробки в Москве прямо сейчас:** {traffic['level']['text']} ({traffic['score']}/10 баллов)

**Влияние на такси:**
{_get_traffic_impact_text(traffic['score'])}

**Прогноз на ближайшие часы:**
"""
    
    for f in forecast:
        report += f"\n• {f['time']} - {f['level']['color']} {f['description']} ({f['score']}/10)"
    
    return report


def _get_traffic_impact_text(score):
    """Влияние пробок на работу такси"""
    if score >= 8:
        return """
⚠️ **Сильное влияние:**
• Время поездки +50-70%
• Спрос на поездки ВЫШЕ на 30-40%
• Рекомендуем: центр и магистрали
• Коэффициенты ожидаются 2.5-3.5x
"""
    elif score >= 6:
        return """
🟠 **Среднее влияние:**
• Время поездки +30-50%
• Спрос выше на 20-30%
• Рекомендуем: избегать центра в час пик
• Коэффициенты ожидаются 2.0-2.8x
"""
    elif score >= 4:
        return """
🟡 **Умеренное влияние:**
• Время поездки +15-30%
• Спрос немного выше
• Рекомендуем: стандартные маршруты
• Коэффициенты ожидаются 1.5-2.0x
"""
    else:
        return """
🟢 **Минимальное влияние:**
• Дороги свободны
• Спрос обычный
• Рекомендуем: можно ехать куда угодно
• Коэффициенты ожидаются 1.2-1.5x
"""


# Тест
if __name__ == "__main__":
    print(get_traffic_report())
    
    analyzer = TrafficAnalyzer()
    district_traffic = analyzer.get_district_traffic('Центр (Тверская, Арбат)')
    print(f"\n📊 Район: {district_traffic['district']}")
    print(f"Пробки с учётом района: {district_traffic['adjusted_score']}/10")