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
    parser.add_argument("--category", default="event")
    parser.add_argument("--venue", default="")
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--start", required=True, help="ISO 8601, например 2026-11-14T19:00:00")
    parser.add_argument("--end", default="", help="ISO 8601; если пусто — +3 часа")
    parser.add_argument("--attendance", type=int, default=0)
    parser.add_argument("--attendance-min", type=int)
    parser.add_argument("--attendance-max", type=int)
    parser.add_argument("--attendance-confidence", default="")
    parser.add_argument("--attendance-basis", default="")
    parser.add_argument("--source-url", default="")
    parser.add_argument("--venue-source-url", default="")
    parser.add_argument("--location-source-url", default="")
    parser.add_argument("--source-checked-at", default="")
    parser.add_argument("--path", default=str(EVENTS_PATH))
    args = parser.parse_args()

    if (args.attendance_min is None) != (args.attendance_max is None):
        parser.error("--attendance-min и --attendance-max нужно задавать вместе")
    if args.attendance_min is not None and args.attendance_min > args.attendance_max:
        parser.error("--attendance-min не может быть больше --attendance-max")
    if args.attendance_min is not None and not (
        args.attendance_min <= args.attendance <= args.attendance_max
    ):
        parser.error("--attendance должно быть внутри заданного диапазона")

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
    event = {
        "title": args.title,
        "category": args.category,
        "venue": args.venue,
        "lat": args.lat,
        "lon": args.lon,
        "start": args.start,
        "end": args.end,
        "attendance": args.attendance,
    }
    optional_fields = {
        "attendance_min": args.attendance_min,
        "attendance_max": args.attendance_max,
        "attendance_confidence": args.attendance_confidence,
        "attendance_basis": args.attendance_basis,
        "source_url": args.source_url,
        "venue_source_url": args.venue_source_url,
        "location_source_url": args.location_source_url,
        "source_checked_at": args.source_checked_at,
    }
    event.update(
        {
            key: value
            for key, value in optional_fields.items()
            if value is not None and value != ""
        }
    )
    payload["events"].append(event)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Добавлено. Всего событий: {len(payload['events'])}. Файл: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
