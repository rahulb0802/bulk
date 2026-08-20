from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from macro.models import DayPlan, MealPlan, Profile
from macro.settings import ntfy_base_url, ntfy_topic


def meal_markdown(meal: MealPlan) -> str:
    lines = [
        f"**ISR {meal.name} · {meal.protein_g:g}g P · {meal.calories:g} kcal**",
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
        f"**ISR {plan.date} · {plan.protein_g:g}g P · {plan.calories:g} kcal**",
        "",
    ]
    for meal in plan.meals:
        names = ", ".join(item.name for item in meal.items[:4])
        lines.append(
            f"- **{meal.name.title()}**: {meal.protein_g:g}g P / {meal.calories:g} kcal — {names}"
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


def _publish(topic: str, title: str, message: str, at: datetime | None = None) -> None:
    headers = {
        "Title": _ascii_header(title),
        "Markdown": "yes",
        "Tags": "plate,tomato",
    }
    if at is not None:
        now = datetime.now(tz=at.tzinfo)
        if at > now + timedelta(minutes=2):
            headers["At"] = str(int(at.timestamp()))
    url = f"{ntfy_base_url()}/{topic}"
    with httpx.Client(timeout=20.0) as client:
        response = client.post(url, content=message.encode("utf-8"), headers=headers)
        response.raise_for_status()


def notify_plan(plan: DayPlan, profile: Profile) -> None:
    topic = ntfy_topic()
    if not topic:
        print("NTFY_TOPIC not set; skipping notifications.")
        return
    _publish(
        topic,
        title=f"ISR {plan.date} - {plan.protein_g:g}g P - {plan.calories:g} kcal",
        message=overview_markdown(plan),
    )
    target = date.fromisoformat(plan.date)
    for meal in plan.meals:
        when = meal_notify_at(target, meal.name, profile)
        _publish(
            topic,
            title=f"ISR {meal.name} - {meal.protein_g:g}g P - {meal.calories:g} kcal",
            message=meal_markdown(meal),
            at=when,
        )
