"""Предполётная проверка MOVENTA перед тестовым запуском.

Проверяет конфигурацию, базу, данные и прогноз, ничего не меняя в продакшене.
Запуск:

    python tools/preflight.py

Код возврата 0 — всё готово, 1 — есть критичные проблемы (они помечены FAIL).
Предупреждения (WARN) не блокируют запуск, но о них стоит знать.
"""

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, str, str]] = []  # (статус, проверка, подробности)
TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{35}$")


def record(status: str, name: str, detail: str = "") -> None:
    RESULTS.append((status, name, detail))


def run(name: str, func) -> None:
    try:
        status, detail = func()
        record(status, name, detail)
    except Exception as exc:  # noqa: BLE001 - проверка не должна падать сама
        record("FAIL", name, f"{type(exc).__name__}: {exc}")


def check_token() -> tuple[str, str]:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        return "FAIL", "BOT_TOKEN не задан"
    if not TOKEN_RE.match(token):
        return "FAIL", "формат токена не похож на выданный @BotFather"
    return "OK", f"токен для бота {token.split(':')[0]}"


def check_webapp_url() -> tuple[str, str]:
    import config  # noqa: PLC0415

    url = (getattr(config, "WEBAPP_URL", "") or "").strip()
    if not url:
        return "FAIL", "WEBAPP_URL не задан: Mini App не откроется"
    if not url.startswith("https://"):
        return "FAIL", "WEBAPP_URL должен быть https"
    return "OK", url


def check_maps_key() -> tuple[str, str]:
    import config  # noqa: PLC0415

    key = (getattr(config, "YANDEX_MAPS_API_KEY", "") or "").strip()
    if not key:
        return "WARN", "YANDEX_MAPS_API_KEY не задан: карта коэффициентов не загрузится"
    return "OK", "ключ Яндекс.Карт задан"


def check_database() -> tuple[str, str]:
    import database  # noqa: PLC0415
    import config  # noqa: PLC0415

    database.init_db()
    path = Path(config.DB_PATH)
    if not path.exists():
        return "FAIL", f"файл базы не создан: {path}"
    import sqlite3  # noqa: PLC0415

    with sqlite3.connect(config.DB_PATH) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    required = {
        "users", "work_shifts", "coefficients", "driver_reports",
        "forecast_runs", "forecast_evaluations", "notifications",
        "user_subscriptions", "notification_preferences",
    }
    missing = required - tables
    if missing:
        return "FAIL", f"нет таблиц: {', '.join(sorted(missing))}"
    return "OK", f"{len(tables)} таблиц, файл {path.name}"


def check_static() -> tuple[str, str]:
    problems = []
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    if "?v=" not in index:
        problems.append("index.html без версии ассетов (кэш не сбросится)")
    for name in ("app.js", "styles.css"):
        if not (ROOT / "static" / name).exists():
            problems.append(f"нет static/{name}")
    if "MOVENTA" not in index:
        problems.append("в index.html нет бренда MOVENTA")
    if problems:
        return "FAIL", "; ".join(problems)
    return "OK", "index.html, app.js, styles.css на месте"


def _load_json(name: str) -> dict:
    return json.loads((ROOT / "static" / name).read_text(encoding="utf-8"))


def check_data() -> tuple[str, str]:
    centers = _load_json("district-centers.json")
    districts = centers.get("districts") or {}
    poi = _load_json("district-poi.json")
    poi_districts = poi.get("districts") or {}
    events = _load_json("events.json")
    if len(districts) < 100:
        return "FAIL", f"центров районов всего {len(districts)}"
    if len(poi_districts) < 100:
        return "WARN", "плотность заведений покрывает мало районов: курьерский прогноз будет слабым"
    return (
        "OK",
        f"{len(districts)} районов, плотность по {len(poi_districts)}, "
        f"событий: {len(events.get('events') or [])}",
    )


def check_forecast() -> tuple[str, str]:
    from datetime import timedelta  # noqa: PLC0415

    import config  # noqa: PLC0415
    import demand_forecasting  # noqa: PLC0415
    import external_factors  # noqa: PLC0415

    now = config.get_moscow_time()
    factors = external_factors.load_live_factors(now)
    taxi = demand_forecasting.build_forecast(
        "taxi",
        {"reports": [{
            "district": "Арбат",
            "coefficient": 2.5,
            "timestamp": (now - timedelta(minutes=10)).isoformat(),
            "user_id": 1,
        }]},
        now=now,
        factors=factors,
    )
    courier = demand_forecasting.build_forecast(
        "courier", {"shifts": []}, now=now, factors=factors
    )
    if "items" not in taxi or "items" not in courier:
        return "FAIL", "прогноз не вернул список районов"
    if not taxi["items"]:
        return "FAIL", "такси-прогноз не построился даже на тестовом коэффициенте"
    used = [name for name, value in (taxi.get("factors") or {}).items() if value]
    return (
        "OK",
        f"такси {len(taxi['items'])} района, курьер {len(courier['items'])}; "
        f"факторы: {', '.join(used) if used else 'нет внешних'}",
    )


def check_notifications() -> tuple[str, str]:
    import notifications  # noqa: PLC0415

    inside = notifications.in_quiet_hours(
        notifications.datetime(2026, 1, 1, 23, 0), "22:00", "08:00"
    )
    if not inside:
        return "FAIL", "тихие часы через полночь считаются неверно"
    return "OK", "тихие часы и частота настроены"


def check_admin() -> tuple[str, str]:
    import database  # noqa: PLC0415

    summary = database.get_admin_summary()
    for field in ("forecast_quality", "district_coverage", "forecast_quality_by_district"):
        if field not in summary:
            return "FAIL", f"в админке нет блока {field}"
    return "OK", "метрики, качество прогнозов и покрытие доступны"


def main() -> int:
    run("Токен бота", check_token)
    run("Адрес Mini App", check_webapp_url)
    run("Ключ Яндекс.Карт", check_maps_key)
    run("База данных", check_database)
    run("Статика", check_static)
    run("Данные по районам", check_data)
    run("Прогноз", check_forecast)
    run("Уведомления", check_notifications)
    run("Админка", check_admin)

    width = max(len(name) for _, name, _ in RESULTS)
    for status, name, detail in RESULTS:
        print(f"{status:5} {name:<{width}}  {detail}")

    failures = [r for r in RESULTS if r[0] == "FAIL"]
    warnings = [r for r in RESULTS if r[0] == "WARN"]
    print()
    if failures:
        print(f"Не готово: {len(failures)} проблем(ы). Исправь их перед запуском.")
        return 1
    if warnings:
        print(f"Готово к запуску, но есть {len(warnings)} предупреждение(й).")
        return 0
    print("Всё готово к тестовому запуску.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
