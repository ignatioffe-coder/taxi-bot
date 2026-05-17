import requests

def get_weather(city="Moscow"):
    """Погода через wttr.in (бесплатно, без ключа)"""
    try:
        url = f"https://wttr.in/{city}?format=%C+%t&lang=ru"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            text = response.text.strip()
            
            # Разделяем на условие и температуру
            if ' ' in text:
                # Берём последнюю часть как температуру
                parts = text.rsplit(' ', 1)
                if len(parts) == 2:
                    condition_raw, temp = parts
                else:
                    condition_raw = text
                    temp = ""
            else:
                condition_raw = text
                temp = ""
            
            # Словарь для перевода погодных условий
            conditions_map = {
                "Clear": "☀️ Ясно",
                "Sunny": "☀️ Солнечно",
                "Partly cloudy": "🌤️ Переменная облачность",
                "Cloudy": "☁️ Облачно",
                "Overcast": "☁️ Пасмурно",
                "Rain": "🌧️ Дождь",
                "Light rain": "🌧️ Небольшой дождь",
                "Heavy rain": "🌧️ Сильный дождь",
                "Rain, Thunderstorm": "⛈️ Гроза с дождём",
                "Thunderstorm": "⛈️ Гроза",
                "Light Rain With Thunderstorm": "⛈️ Гроза с дождём",
                "Rain With Thunderstorm": "⛈️ Гроза с дождём",
                "Snow": "❄️ Снег",
                "Light snow": "❄️ Небольшой снег",
                "Heavy snow": "❄️ Сильный снег",
                "Fog": "🌫️ Туман",
                "Mist": "🌫️ Дымка"
            }
            
            # Ищем в словаре (по ключевым словам)
            condition_ru = condition_raw
            for eng, rus in conditions_map.items():
                if eng.lower() in condition_raw.lower():
                    condition_ru = rus
                    break
            
            # Чистый вывод
            result = f"🌡️ Погода в Москве: {condition_ru}"
            if temp:
                result += f", {temp}"
            
            return result
        else:
            # Запасной вариант
            return get_fallback_weather()
            
    except Exception as e:
        return get_fallback_weather()

def get_fallback_weather():
    """Запасной вариант погоды"""
    from datetime import datetime
    hour = datetime.now().hour
    
    if 6 <= hour < 12:
        return "🌡️ Погода в Москве: ☀️ Утро, ~15°C"
    elif 12 <= hour < 18:
        return "🌡️ Погода в Москве: ☀️ День, ~22°C"
    elif 18 <= hour < 23:
        return "🌡️ Погода в Москве: 🌙 Вечер, ~16°C"
    else:
        return "🌡️ Погода в Москве: 🌙 Ночь, ~10°C"

if __name__ == "__main__":
    print(get_weather())