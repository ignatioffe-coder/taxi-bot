"""Build an anonymised Telegram community-signal snapshot for demand forecasting."""

import argparse
import glob
import json
import re
from collections import defaultdict
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "static" / "telegram-community-signals.json"

DISTRICT_ALIASES = {
    "арбат": "Арбат",
    "тверской": "Тверской",
    "большой т": "Тверской",
    "сокольник": "Сокольники",
    "новокосино": "Новокосино",
    "кузьмин": "Кузьминки",
    "внуково": "Внуково",
    "щербин": "Щербинка",
    "люберц": "Выхино-Жулебино",
    "митино": "Митино",
    "зеленоград": "Крюково",
    "бирюлёво": "Бирюлёво Восточное",
    "чертаново": "Чертаново Центральное",
    "царицын": "Царицыно",
    "бутово": "Северное Бутово",
    "химки": "Ховрино",
}

CATEGORIES = {
    "no_orders": (r"нет заказ|нету заказ|заказов нет|жду по пол часа|нету заказы", -1),
    "many_orders": (r"много заказ|заказов много|заказы идут|орава заказ|жир был", 1),
    "long_wait": (r"жду заказ|жду по пол часа|долгие перерывы|перерывы между заказами", -1),
    "traffic": (r"пробк|пробка|стоял|застрял|дорожн", -1),
    "weather": (r"дожд|ливень|снег|гололед|лед|мороз|холод|погода", 0),
    "support_issue": (r"поддержк.{0,50}(не отвечает|не помог|бот|шаблон|проблем)|не дают ответ", 0),
    "payment_issue": (r"недоплат|не приходит.*выплат|списал|штраф|выплат.*проблем", 0),
    "rental_issue": (r"аренд.{0,30}(списал|задолж|не заверш|дорог)|не заверша.*аренд", 0),
}


def canonical_district(text: str) -> str | None:
    lowered = text.lower()
    for alias, district in DISTRICT_ALIASES.items():
        if alias in lowered:
            return district
    return None


def parse_messages(folder: Path) -> list[tuple[str, str, str]]:
    rows = []
    for path in sorted(folder.glob("messages*.html")):
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        for message in soup.select(".message.default"):
            date = message.select_one(".date")
            text_node = message.select_one(".text")
            if not date or not text_node:
                continue
            title = date.get("title", "")
            text = " ".join(text_node.get_text(" ", strip=True).split())
            if text:
                rows.append((title, text, text.lower()))
    return rows


def build_snapshot(folder: Path) -> dict:
    hourly = defaultdict(lambda: {"messages": 0, "demand_score": 0.0, "categories": defaultdict(int)})
    districts = defaultdict(lambda: defaultdict(lambda: {"messages": 0, "demand_score": 0.0, "categories": defaultdict(int)}))
    dates = []

    for title, text, lowered in parse_messages(folder):
        match = re.search(r"(\d{2}\.\d{2}\.\d{4})\s+(\d{2}):(\d{2})", title)
        if not match:
            continue
        date_text, hour_text, _ = match.groups()
        hour = int(hour_text)
        dates.append(date_text)
        matched_categories = []
        demand_score = 0.0
        for category, (pattern, score) in CATEGORIES.items():
            if re.search(pattern, lowered):
                matched_categories.append(category)
                demand_score += score
                hourly[hour]["categories"][category] += 1

        if matched_categories:
            hourly[hour]["messages"] += 1
            hourly[hour]["demand_score"] += demand_score
            district = canonical_district(lowered)
            if district:
                districts[district][hour]["messages"] += 1
                districts[district][hour]["demand_score"] += demand_score
                for category in matched_categories:
                    districts[district][hour]["categories"][category] += 1

    def serialise_hour(row):
        messages = int(row["messages"])
        score = round(max(-1.0, min(1.0, row["demand_score"] / max(1, messages))), 3)
        return {"messages": messages, "demand_score": score, "categories": dict(row["categories"])}

    result = {
        "source": "Telegram community export",
        "date_from": min(dates) if dates else None,
        "date_to": max(dates, key=lambda value: tuple(map(int, value.split(".")[::-1]))) if dates else None,
        "privacy": "only aggregated counts and categories; no names or Telegram IDs",
        "hourly": {str(hour): serialise_hour(hourly[hour]) for hour in range(24)},
        "district_hourly": {},
    }
    for district, hours in sorted(districts.items()):
        result["district_hourly"][district] = {
            str(hour): serialise_hour(hours[hour]) for hour in range(24) if hours[hour]["messages"]
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", help="Папка ChatExport_...")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        parser.error(f"Папка не найдена: {folder}")
    snapshot = build_snapshot(folder)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Сигналов: {sum(v['messages'] for v in snapshot['hourly'].values())}; "
        f"районов: {len(snapshot['district_hourly'])}; файл: {output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
