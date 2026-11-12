"""Строит плотность заведений по районам Москвы из OpenStreetMap.

Плотность точек питания, магазинов и вузов — публичный фактор спроса,
который не требует ни краудсорсинга, ни ключей API (ТЗ, п. 8.2).

Данные OSM забираются одним запросом по Москве, затем каждая точка
привязывается к ближайшему центру района (приближение Вороного).
Точность достаточна для базовой оценки спроса, но границы районов
не учитываются — это указано в метаданных файла.

Запуск:
    python tools/build_poi_density.py
"""

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CENTERS_PATH = ROOT / "static" / "district-centers.json"
OUTPUT_PATH = ROOT / "static" / "district-poi.json"

MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# Москва целиком, включая Новую Москву
BBOX = "55.55,37.35,55.95,37.95"

CATEGORIES = {
    "food": 'node[amenity~"^(restaurant|cafe|fast_food|bar|pub|food_court)$"]',
    "shop": (
        'node[shop~"^(supermarket|convenience|mall|department_store|'
        'bakery|butcher|greengrocer)$"]'
    ),
    "education": 'node[amenity~"^(university|college)$"]',
    "service": 'node[amenity~"^(pharmacy|bank)$"]',
}

OVERPASS_QUERY = (
    "[out:json][timeout:300];("
    + "".join(f'{pattern}({BBOX});' for pattern in CATEGORIES.values())
    + ");out;"
)

USER_AGENT = "MOVENTA/1.0 (demand forecast baseline; OSM ODbL)"


def fetch_from_overpass(query: str) -> list[dict]:
    """Пробует зеркала по очереди с экспоненциальной паузой."""
    last_error = "unknown"
    for attempt in range(3):
        for mirror in MIRRORS:
            try:
                response = requests.post(
                    mirror,
                    data={"data": query},
                    headers={"User-Agent": USER_AGENT},
                    timeout=300,
                )
                if response.status_code == 200 and response.text.lstrip().startswith("{"):
                    payload = response.json()
                    return payload.get("elements", [])
                last_error = f"{mirror}: HTTP {response.status_code}"
            except Exception as exc:  # noqa: BLE001 - зеркала отвечают по-разному
                last_error = f"{mirror}: {type(exc).__name__}"
            print(f"  {last_error}", flush=True)
        pause = 15 * (2**attempt)
        print(f"  пауза {pause} с перед повтором", flush=True)
        time.sleep(pause)
    raise RuntimeError(f"Overpass недоступен: {last_error}")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))


def nearest_district(
    lat: float, lon: float, centers: dict[str, dict]
) -> str | None:
    best_name = None
    best_distance = float("inf")
    for name, point in centers.items():
        distance = haversine_km(lat, lon, point["lat"], point["lon"])
        if distance < best_distance:
            best_distance, best_name = distance, name
    return best_name


def category_of(tags: dict) -> str | None:
    amenity = tags.get("amenity", "")
    shop = tags.get("shop", "")
    if amenity in {"restaurant", "cafe", "fast_food", "bar", "pub", "food_court"}:
        return "food"
    if shop in {
        "supermarket", "convenience", "mall", "department_store",
        "bakery", "butcher", "greengrocer",
    }:
        return "shop"
    if amenity in {"university", "college"}:
        return "education"
    if amenity in {"pharmacy", "bank"}:
        return "service"
    return None


def main() -> int:
    centers_payload = json.loads(CENTERS_PATH.read_text(encoding="utf-8"))
    centers = centers_payload["districts"]
    print(f"районов: {len(centers)}")

    print("запрашиваю OpenStreetMap...", flush=True)
    elements = fetch_from_overpass(OVERPASS_QUERY)
    print(f"получено объектов: {len(elements):,}")

    counts: dict[str, dict[str, int]] = {
        name: {category: 0 for category in CATEGORIES} for name in centers
    }
    skipped = 0
    for element in elements:
        tags = element.get("tags") or {}
        category = category_of(tags)
        lat, lon = element.get("lat"), element.get("lon")
        if category is None or lat is None or lon is None:
            skipped += 1
            continue
        district = nearest_district(lat, lon, centers)
        if district:
            counts[district][category] += 1

    payload = {
        "source": "OpenStreetMap contributors",
        "license": "ODbL 1.0",
        "attribution": "© OpenStreetMap contributors",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": (
            "Каждая точка OSM отнесена к ближайшему центру района "
            "(приближение Вороного). Границы районов не учитываются."
        ),
        "bbox": BBOX,
        "categories": {
            "food": "кафе, рестораны, фастфуд, бары",
            "shop": "магазины продуктов и торговые центры",
            "education": "вузы и колледжи",
            "service": "аптеки и банки",
        },
        "districts": counts,
    }

    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    totals = {category: 0 for category in CATEGORIES}
    for district_counts in counts.values():
        for category, value in district_counts.items():
            totals[category] += value

    print(f"пропущено объектов: {skipped}")
    print("итого по категориям:", totals)
    top = sorted(
        counts.items(), key=lambda kv: kv[1]["food"] + kv[1]["shop"], reverse=True
    )[:5]
    print("топ районов:")
    for name, values in top:
        print(f"  {name}: еда {values['food']}, магазины {values['shop']}")
    print(f"сохранено: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
