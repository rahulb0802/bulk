from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from macro.models import DayPlan, MealPlan, Profile
from macro.settings import ntfy_base_url, ntfy_topic

MEAL_NAMES = ("breakfast", "lunch", "dinner")
SEQUENCE_KINDS = ("overview", *MEAL_NAMES)


def meal_markdown(meal: MealPlan) -> str:
    lines = [
        f"**ISR {meal.name} · {meal.protein_g:g}g P · {meal.carbs_g:g}g C · {meal.fat_g:g}g F · {meal.calories:g} kcal**",
    ]
    for i, item in enumerate(meal.items, start=1):
        amount = f"{item.servings:g} × {item.serving_size}" if item.serving_size else f"{item.servings:g} servings"
        extra = f" — {item.notes}" if item.notes else ""
        lines.append(
            f"{i}. **{item.name}** ({amount}) @ {item.station} · {item.protein_g:g}g P{extra}"
        )
    if meal.station_order:
        lines.append("Order: " + " → ".join(meal.station_order))
    if meal.plate_tips:
        lines.append(f"Tip: {meal.plate_tips}")
    return "\n".join(lines)


def overview_markdown(plan: DayPlan) -> str:
    lines = [
        f"**ISR {plan.date} · {plan.protein_g:g}g P · {plan.carbs_g:g}g C · {plan.fat_g:g}g F · {plan.calories:g} kcal**",
        "",
    ]
    for meal in plan.meals:
        names = ", ".join(item.name for item in meal.items[:4])
        lines.append(
            f"- **{meal.name.title()}**: {meal.protein_g:g}g P / {meal.carbs_g:g}g C / {meal.fat_g:g}g F / {meal.calories:g} kcal — {names}"
        )
    if plan.protein_gap_plan:
        lines.append("")
        lines.append(f"Protein gap: {plan.protein_gap_plan}")
    if plan.warnings:
        lines.append("")
        lines.append("Notes: " + "; ".join(plan.warnings[:3]))
    return "\n".join(lines)


def plan_to_markdown(plan: DayPlan) -> str:
    chunks = [overview_markdown(plan), ""]
    for meal in plan.meals:
        chunks.append(meal_markdown(meal))
        chunks.append("")
    return "\n".join(chunks).strip() + "\n"


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)


def ntfy_sequence_id(plan_date: date, name: str) -> str:
    return f"isr-{plan_date.isoformat()}-{name}"


def meal_notify_at(plan_date: date, meal_name: str, profile: Profile) -> datetime:
    tz = ZoneInfo(profile.timezone)
    hh, mm = _parse_hhmm(profile.meals[meal_name])
    when = datetime(plan_date.year, plan_date.month, plan_date.day, hh, mm, tzinfo=tz)
    return when - timedelta(minutes=profile.notify_lead_minutes)


def _ascii_header(value: str) -> str:
    replacements = {
        "·": "-",
        "—": "-",
        "–": "-",
        "×": "x",
        "→": "->",
    }
    for src, dst in replacements.items():
        value = value.replace(src, dst)
    return value.encode("ascii", "replace").decode("ascii")


def _publish(
    topic: str,
    title: str,
    message: str,
    sequence_id: str,
    at: datetime | None = None,
) -> None:
    headers = {
        "Title": _ascii_header(title),
        "Markdown": "yes",
        "Tags": "plate,tomato",
    }
    if at is not None:
        now = datetime.now(tz=at.tzinfo)
        if at > now + timedelta(minutes=2):
            headers["At"] = str(int(at.timestamp()))
    url = f"{ntfy_base_url()}/{topic}/{sequence_id}"
    with httpx.Client(timeout=20.0) as client:
        response = client.post(url, content=message.encode("utf-8"), headers=headers)
        response.raise_for_status()


def _delete_sequence(client: httpx.Client, topic: str, sequence_id: str) -> bool:
    response = client.delete(f"{ntfy_base_url()}/{topic}/{sequence_id}")
    if response.status_code in {400, 404}:
        return False
    response.raise_for_status()
    return True


def _scheduled_matches_date(raw: dict[str, object], target: date, profile: Profile) -> bool:
    if raw.get("event") != "message":
        return False
    title = str(raw.get("title") or "")
    if not title.startswith("ISR "):
        return False
    if target.isoformat() in title:
        return True
    meal = next((name for name in MEAL_NAMES if title.lower().startswith(f"isr {name}")), None)
    if meal is None:
        return False
    stamp = int(raw.get("time") or 0)
    if stamp <= 0:
        return False
    when = datetime.fromtimestamp(stamp, tz=ZoneInfo(profile.timezone))
    return when.date() == target


def cancel_plan_notifications(target: date, profile: Profile) -> int:
    topic = ntfy_topic()
    if not topic:
        print("NTFY_TOPIC not set; skipping cancel.")
        return 0
    cancelled = 0
    seen: set[str] = set()
    with httpx.Client(timeout=20.0) as client:
        for kind in SEQUENCE_KINDS:
            sid = ntfy_sequence_id(target, kind)
            if _delete_sequence(client, topic, sid):
                print(f"Cancelled {kind} ({sid})")
                cancelled += 1
            seen.add(sid)
        poll = client.get(
            f"{ntfy_base_url()}/{topic}/json",
            params={"poll": "1", "sched": "1", "since": "all"},
        )
        poll.raise_for_status()
        now = int(datetime.now(tz=ZoneInfo(profile.timezone)).timestamp())
        for line in poll.text.splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            if not _scheduled_matches_date(raw, target, profile):
                continue
            stamp = int(raw.get("time") or 0)
            if stamp <= now:
                continue
            sid = str(raw.get("sequence_id") or raw.get("id") or "")
            if not sid or sid in seen:
                continue
            if _delete_sequence(client, topic, sid):
                title = str(raw.get("title") or sid)
                print(f"Cancelled queued {title}")
                cancelled += 1
            seen.add(sid)
    return cancelled


def notify_plan(plan: DayPlan, profile: Profile) -> None:
    topic = ntfy_topic()
    if not topic:
        print("NTFY_TOPIC not set; skipping notifications.")
        return
    target = date.fromisoformat(plan.date)
    _publish(
        topic,
        title=f"ISR {plan.date} - {plan.protein_g:g}g P - {plan.calories:g} kcal",
        message=overview_markdown(plan),
        sequence_id=ntfy_sequence_id(target, "overview"),
    )
    for meal in plan.meals:
        when = meal_notify_at(target, meal.name, profile)
        _publish(
            topic,
            title=f"ISR {meal.name} - {meal.protein_g:g}g P - {meal.calories:g} kcal",
            message=meal_markdown(meal),
            sequence_id=ntfy_sequence_id(target, meal.name),
            at=when,
        )
