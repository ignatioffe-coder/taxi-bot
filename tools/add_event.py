"""Добавляет событие в static/events.json (ТЗ, п. 13).

Пример:
    python tools/add_event.py --title "Концерт" --venue "Лужники" \
        --lat 55.7158 --lon 37.5531 \
        --start 2026-11-14T19:00:00 --end 2026-11-14T23:00:00 --attendance 40000
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

EVENTS_PATH = Path(__file__).resolve().parent.parent / "static" / "events.json"


def load_payload(path: Path) -> dict:
    if not path.exists():
        return {"events": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"events": []}
    if not isinstance(payload, dict):
        return {"events": []}
    payload.setdefault("events", [])
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Добавить событие в events.json")
    parser.add_argument("--title", required=True)
    parser.add_argument("--venue", default="")
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--start", required=True, help="ISO 8601, например 2026-11-14T19:00:00")
    parser.add_argument("--end", default="", help="ISO 8601; если пусто — +3 часа")
    parser.add_argument("--attendance", type=int, default=0)
    parser.add_argument("--path", default=str(EVENTS_PATH))
    args = parser.parse_args()

    for value, label in ((args.start, "start"), (args.end, "end")):
        if not value:
            continue
        try:
            datetime.fromisoformat(value)
        except ValueError:
            print(f"Неверный формат {label}: {value}")
            return 1

    path = Path(args.path)
    payload = load_payload(path)
    payload["events"].append({
        "title": args.title,
        "venue": args.venue,
        "lat": args.lat,
        "lon": args.lon,
        "start": args.start,
        "end": args.end,
        "attendance": args.attendance,
    })
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Добавлено. Всего событий: {len(payload['events'])}. Файл: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
