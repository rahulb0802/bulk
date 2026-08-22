from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from macro.models import DayPlan, MenuItem, PlannedItem, Profile
from macro.settings import ntfy_base_url, ntfy_topic

MEALS = ("breakfast", "lunch", "dinner")


@dataclass(frozen=True)
class Outage:
    query: str
    meal: str | None = None


def parse_out_text(title: str, message: str) -> tuple[str | None, str]:
    """Parse an ntfy outage into (optional meal, food query)."""
    title = (title or "").strip()
    message = (message or "").strip()
    title_body = _strip_out_prefix(title)
    message_body = _strip_out_prefix(message)
    if title.lower() == "out":
        blob = message_body or message
    elif title_body and message_body and title.lower().startswith("out"):
        blob = message_body
    else:
        blob = message_body or title_body
    blob = blob.strip()
    meal, query = _split_meal_prefix(blob)
    return meal, query


def _strip_out_prefix(value: str) -> str:
    lowered = value.lower()
    if lowered == "out":
        return ""
    if lowered.startswith("out:") or lowered.startswith("out "):
        return value[4:].strip()
    return value


def _split_meal_prefix(blob: str) -> tuple[str | None, str]:
    lowered = blob.lower()
    for name in MEALS:
        prefix = f"{name}:"
        if lowered.startswith(prefix):
            return name, blob.split(":", 1)[1].strip()
    return None, blob


def infer_meal(profile: Profile, when: datetime | None = None) -> str:
    """Meal window starts notify_lead minutes before that meal's clock time."""
    tz = ZoneInfo(profile.timezone)
    now = when.astimezone(tz) if when is not None else datetime.now(tz)
    starts: list[tuple[str, datetime]] = []
    for name in MEALS:
        clock = profile.meals.get(name)
        if not clock:
            continue
        hour_s, minute_s = clock.split(":", 1)
        meal_at = now.replace(
            hour=int(hour_s),
            minute=int(minute_s),
            second=0,
            microsecond=0,
        )
        window_start = meal_at - timedelta(minutes=profile.notify_lead_minutes)
        starts.append((name, window_start))
    if not starts:
        return "lunch"
    current = starts[0][0]
    for name, window_start in starts:
        if now >= window_start:
            current = name
    return current


def load_outages(target: date, profile: Profile) -> list[Outage]:
    topic = ntfy_topic()
    if not topic:
        return []
    tz = ZoneInfo(profile.timezone)
    start = datetime.combine(target, time.min, tzinfo=tz)
    end = datetime.combine(target, time.max, tzinfo=tz)
    outages: list[Outage] = []
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.get(
                f"{ntfy_base_url()}/{topic}/json",
                params={"poll": "1", "since": str(int(start.timestamp()))},
            )
            response.raise_for_status()
    except Exception as exc:
        print(f"Could not poll ntfy outages ({exc}); continuing without them.")
        return []
    for line in response.text.splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        if raw.get("event") != "message":
            continue
        title = str(raw.get("title") or "")
        message = str(raw.get("message") or "")
        if not _looks_like_outage(title, message):
            continue
        stamp = int(raw.get("time") or 0)
        if stamp <= 0:
            continue
        when = datetime.fromtimestamp(stamp, tz=tz)
        if when < start or when > end:
            continue
        meal, query = parse_out_text(title, message)
        if not query:
            continue
        if meal is None:
            meal = infer_meal(profile, when)
        outages.append(Outage(query=query, meal=meal))
    return outages


def clear_outages(target: date, profile: Profile) -> int:
    """Delete ntfy messages titled out for this calendar day."""
    topic = ntfy_topic()
    if not topic:
        print("NTFY_TOPIC not set; skipping outage clear.")
        return 0
    tz = ZoneInfo(profile.timezone)
    start = datetime.combine(target, time.min, tzinfo=tz)
    end = datetime.combine(target, time.max, tzinfo=tz)
    deleted = 0
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.get(
                f"{ntfy_base_url()}/{topic}/json",
                params={"poll": "1", "since": str(int(start.timestamp()))},
            )
            response.raise_for_status()
            for line in response.text.splitlines():
                if not line.strip():
                    continue
                raw = json.loads(line)
                if raw.get("event") != "message":
                    continue
                title = str(raw.get("title") or "")
                message = str(raw.get("message") or "")
                if not _looks_like_outage(title, message):
                    continue
                stamp = int(raw.get("time") or 0)
                if stamp <= 0:
                    continue
                when = datetime.fromtimestamp(stamp, tz=tz)
                if when < start or when > end:
                    continue
                sid = str(raw.get("id") or "")
                if not sid:
                    continue
                drop = client.delete(f"{ntfy_base_url()}/{topic}/{sid}")
                if drop.status_code in {400, 404}:
                    continue
                drop.raise_for_status()
                print(f"Deleted outage ({message or title})")
                deleted += 1
    except Exception as exc:
        print(f"Could not clear ntfy outages ({exc}).")
        return deleted
    return deleted


def _looks_like_outage(title: str, message: str) -> bool:
    title_l = title.lower().strip()
    message_l = message.lower().strip()
    if title_l.startswith("isr "):
        return False
    if title_l == "out" or title_l.startswith("out ") or title_l.startswith("out:"):
        return True
    if message_l == "out" or message_l.startswith("out ") or message_l.startswith("out:"):
        return True
    return False


def publish_outage(query: str, meal: str | None, target: date) -> None:
    topic = ntfy_topic()
    if not topic:
        print("NTFY_TOPIC not set; not recording outage on ntfy.")
        return
    query = query.strip()
    if not query:
        return
    body = f"{meal}: {query}" if meal else query
    headers = {
        "Title": "out",
        "Tags": "x,warning",
        "Priority": "low",
    }
    url = f"{ntfy_base_url()}/{topic}"
    with httpx.Client(timeout=20.0) as client:
        response = client.post(url, content=body.encode("utf-8"), headers=headers)
        response.raise_for_status()
    print(f"Recorded outage for {target.isoformat()}: {body}")


def apply_outages(
    catalog: list[MenuItem],
    outages: list[Outage],
    plan: DayPlan | None = None,
) -> list[MenuItem]:
    """Drop catalog items matching outages, meal-scoped.

    Planned items for that meal are matched first; if none hit, the rest of
    that meal's catalog is searched. A lunch "eggs" outage does not drop
    dinner eggplant.
    """
    drop: set[tuple[str, str]] = set()
    by_meal: dict[str, list[MenuItem]] = {}
    for item in catalog:
        by_meal.setdefault(item.meal, []).append(item)

    for outage in outages:
        query = outage.query.lower().strip()
        if not query:
            continue
        meals = [outage.meal] if outage.meal in MEALS else list(MEALS)
        for meal in meals:
            planned = _planned_items(plan, meal)
            planned_hits = [item for item in planned if query in item.name.lower()]
            if planned_hits:
                for item in planned_hits:
                    drop.add((meal, item.id))
                continue
            for item in by_meal.get(meal, []):
                if query in item.name.lower():
                    drop.add((meal, item.id))
    if drop:
        dropped = ", ".join(sorted(f"{meal}:{item_id}" for meal, item_id in drop))
        print(f"Excluding outage items: {dropped}")
    return [item for item in catalog if (item.meal, item.id) not in drop]


def _planned_items(plan: DayPlan | None, meal: str) -> list[PlannedItem]:
    if plan is None:
        return []
    found = plan.meal(meal)
    if found is None:
        return []
    return found.items


def describe_outages(outages: list[Outage]) -> str:
    if not outages:
        return ""
    parts = []
    for outage in outages:
        if outage.meal:
            parts.append(f"{outage.meal}: {outage.query}")
        else:
            parts.append(outage.query)
    return "; ".join(parts)
