from __future__ import annotations

import json
import re
from datetime import date

from groq import Groq

from macro.models import (
    DayMenu,
    DayPlan,
    MealPlan,
    MenuItem,
    PlannedItem,
    Profile,
)
from macro.settings import groq_api_key, groq_model, load_staples

MEALS = ("breakfast", "lunch", "dinner")


def catalog_for_day(menu: DayMenu, profile: Profile) -> list[MenuItem]:
    dislikes = [d.lower() for d in profile.dislikes if d]
    items = list(menu.items)
    staples = load_staples()
    existing = {(i.meal, i.name.lower()) for i in items}
    for staple in staples:
        if (staple.meal, staple.name.lower()) not in existing:
            items.append(staple)
    filtered: list[MenuItem] = []
    for item in items:
        name = item.name.lower()
        if any(d in name for d in dislikes):
            continue
        if item.nutrition is None:
            continue
        if item.nutrition.calories > 900 or item.nutrition.protein_g > 40:
            continue
        filtered.append(item)
    return filtered


LLM_ITEMS_PER_MEAL = 16


def _row(item: MenuItem) -> dict[str, object]:
    nutrition = item.nutrition
    assert nutrition is not None
    serving = item.serving_size or nutrition.serving_size
    return {
        "id": item.id,
        "n": item.name,
        "s": item.station,
        "m": item.meal,
        "sz": serving[:40],
        "kcal": round(nutrition.calories),
        "p": round(nutrition.protein_g, 1),
    }


def compact_catalog(items: list[MenuItem], per_meal: int = LLM_ITEMS_PER_MEAL) -> list[dict[str, object]]:
    """Keep a small high-protein + calorie mix so Groq free-tier TPM is not blown."""
    selected: list[MenuItem] = []
    for meal in MEALS:
        group = [item for item in items if item.meal == meal]
        by_protein = sorted(group, key=lambda i: i.protein_g(), reverse=True)
        by_calories = sorted(group, key=lambda i: i.calories(), reverse=True)
        picked: list[MenuItem] = []
        seen: set[str] = set()
        protein_slots = max(per_meal * 2 // 3, 8)
        for item in by_protein[:protein_slots]:
            if item.id not in seen:
                seen.add(item.id)
                picked.append(item)
        for item in by_calories:
            if len(picked) >= per_meal:
                break
            if item.id not in seen:
                seen.add(item.id)
                picked.append(item)
        selected.extend(picked)
    return [_row(item) for item in selected]


def lookup_item(catalog: list[MenuItem], item_id: str, meal: str) -> MenuItem | None:
    for item in catalog:
        if item.id == item_id and item.meal == meal:
            return item
    for item in catalog:
        if item.id == item_id:
            return item
    return None


def recompute(plan: DayPlan, catalog: list[MenuItem], profile: Profile) -> DayPlan:
    warnings: list[str] = list(plan.warnings)
    meals: list[MealPlan] = []
    for meal in plan.meals:
        rebuilt: list[PlannedItem] = []
        seen: set[str] = set()
        for raw in meal.items:
            item = lookup_item(catalog, raw.id, meal.name)
            if item is None:
                warnings.append(f"Dropped unknown item {raw.name!r}")
                continue
            if item.meal != meal.name and not item.staple:
                warnings.append(
                    f"{item.name} is listed for {item.meal}, not {meal.name}; kept anyway"
                )
            servings = raw.servings if raw.servings and raw.servings > 0 else 1.0
            if servings > 6:
                servings = 6
            key = item.id
            if key in seen:
                continue
            seen.add(key)
            nutrition = item.nutrition
            assert nutrition is not None
            rebuilt.append(
                PlannedItem(
                    id=item.id,
                    name=item.name,
                    station=item.station,
                    servings=servings,
                    serving_size=item.serving_size or nutrition.serving_size,
                    protein_g=round(nutrition.protein_g * servings, 1),
                    calories=round(nutrition.calories * servings),
                    notes=raw.notes,
                )
            )
        rebuilt = rebuilt[: profile.max_items_per_meal]
        protein = sum(i.protein_g for i in rebuilt)
        calories = sum(i.calories for i in rebuilt)
        stations = []
        for item in rebuilt:
            if item.station not in stations:
                stations.append(item.station)
        meals.append(
            MealPlan(
                name=meal.name,
                items=rebuilt,
                station_order=meal.station_order or stations,
                plate_tips=meal.plate_tips,
                protein_g=round(protein, 1),
                calories=round(calories),
            )
        )
    names = {m.name for m in meals}
    for required in MEALS:
        if required not in names:
            meals.append(MealPlan(name=required))
    meals.sort(key=lambda m: MEALS.index(m.name) if m.name in MEALS else 99)
    plan.meals = meals
    plan.protein_g = round(sum(m.protein_g for m in meals), 1)
    plan.calories = round(sum(m.calories for m in meals))
    plan.warnings = warnings
    return plan


def plan_ok(plan: DayPlan, profile: Profile) -> bool:
    if plan.protein_g < profile.protein_g:
        return False
    if plan.calories < profile.calories_min - 50:
        return False
    if plan.calories > profile.calories_max + 150:
        return False
    return True


def fill_gaps(plan: DayPlan, catalog: list[MenuItem], profile: Profile) -> DayPlan:
    """Greedy fill from the posted catalog if the LLM undershoots targets."""
    by_meal: dict[str, list[MenuItem]] = {meal: [] for meal in MEALS}
    for item in catalog:
        if item.meal in by_meal:
            by_meal[item.meal].append(item)
    protein_sorted = {
        meal: sorted(
            items, key=lambda i: i.protein_g() / max(i.calories(), 1), reverse=True
        )
        for meal, items in by_meal.items()
    }
    calorie_sorted = {
        meal: sorted(items, key=lambda i: i.calories(), reverse=True)
        for meal, items in by_meal.items()
    }

    def add_to(meal: MealPlan, item: MenuItem, servings: float = 1.0, note: str = "") -> None:
        nutrition = item.nutrition
        assert nutrition is not None
        meal.items.append(
            PlannedItem(
                id=item.id,
                name=item.name,
                station=item.station,
                servings=servings,
                serving_size=item.serving_size or nutrition.serving_size,
                protein_g=round(nutrition.protein_g * servings, 1),
                calories=round(nutrition.calories * servings),
                notes=note or "Added to hit targets",
            )
        )
        if item.station not in meal.station_order:
            meal.station_order.append(item.station)

    for _ in range(30):
        plan = recompute(plan, catalog, profile)
        need_protein = plan.protein_g < profile.protein_g
        need_cals = plan.calories < profile.calories_min
        if not need_protein and not need_cals:
            break
        used = {(m.name, i.id) for m in plan.meals for i in m.items}
        candidates = [m for m in plan.meals if len(m.items) < profile.max_items_per_meal]
        if not candidates:
            best: PlannedItem | None = None
            best_meal: MealPlan | None = None
            for meal in plan.meals:
                for item in meal.items:
                    if item.servings >= 4:
                        continue
                    score = item.protein_g if need_protein else item.calories
                    best_score = 0.0
                    if best is not None:
                        best_score = best.protein_g if need_protein else best.calories
                    if best is None or score > best_score:
                        best = item
                        best_meal = meal
            if best and best_meal:
                best.servings += 1
                continue
            break
        meal = min(
            candidates,
            key=lambda m: m.protein_g if need_protein else m.calories,
        )
        pool = protein_sorted[meal.name] if need_protein else calorie_sorted[meal.name]
        nxt = next((i for i in pool if (meal.name, i.id) not in used), None)
        if nxt is None:
            break
        add_to(
            meal,
            nxt,
            1,
            "Added to hit protein floor" if need_protein else "Added to fill calorie band",
        )

    plan = recompute(plan, catalog, profile)
    if plan.protein_g < profile.protein_g:
        gap = round(profile.protein_g - plan.protein_g, 1)
        plan.protein_gap_plan = (
            f"Still {gap}g protein short of {profile.protein_g}g from the posted menu. "
            "Take extra servings of the highest-protein listed items."
        )
        plan.warnings.append(plan.protein_gap_plan)
    if plan.calories < profile.calories_min:
        plan.warnings.append(
            f"Calories {plan.calories} below {profile.calories_min}; "
            "take extra servings of listed items if you need more."
        )
    if plan.calories > profile.calories_max + 150:
        plan.warnings.append(
            f"Calories {plan.calories} above {profile.calories_max}; skip a fried side or dessert."
        )
    return plan


def _extract_json(text: str) -> dict[str, object]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def dayplan_from_llm(raw: dict[str, object], target: date) -> DayPlan:
    meals: list[MealPlan] = []
    for meal_raw in raw.get("meals") or []:
        if not isinstance(meal_raw, dict):
            continue
        items = []
        for item_raw in meal_raw.get("items") or []:
            if not isinstance(item_raw, dict):
                continue
            items.append(
                PlannedItem(
                    id=str(item_raw.get("id") or ""),
                    name=str(item_raw.get("name") or ""),
                    station=str(item_raw.get("station") or ""),
                    servings=float(item_raw.get("servings") or 1),
                    serving_size=str(item_raw.get("serving_size") or ""),
                    notes=str(item_raw.get("notes") or ""),
                )
            )
        meals.append(
            MealPlan(
                name=str(meal_raw.get("name") or "").lower(),
                items=items,
                station_order=[str(s) for s in (meal_raw.get("station_order") or [])],
                plate_tips=str(meal_raw.get("plate_tips") or ""),
            )
        )
    return DayPlan(
        date=target.isoformat(),
        meals=meals,
        warnings=[str(w) for w in (raw.get("warnings") or [])],
        protein_gap_plan=(
            str(raw["protein_gap_plan"]) if raw.get("protein_gap_plan") else None
        ),
    )


def build_prompt(
    target: date,
    profile: Profile,
    catalog: list[dict[str, object]],
    retry_hint: str | None = None,
) -> tuple[str, str]:
    system = (
        "You are a dining-hall plate coach for an ovo-lacto vegetarian UIUC student. "
        "Choose only items from the provided catalog. Do not invent foods or macros. "
        "Return a single JSON object."
    )
    retry = f"\nRetry: {retry_hint}\n" if retry_hint else ""
    catalog_json = json.dumps(catalog, separators=(",", ":"))
    user = f"""ISR {target.isoformat()}. Ovo-lacto vegetarian.
Protein floor {profile.protein_g}g (no shakes). Calories {profile.calories_min}-{profile.calories_max}.
Max {profile.max_items_per_meal} items/meal.
{profile.notes.strip()}
{retry}
Catalog keys: id,n=name,s=station,m=meal,sz=serving,kcal,p=protein_g
{catalog_json}

Return JSON: {{"meals":[{{"name":"breakfast|lunch|dinner","station_order":["..."],"plate_tips":"...","items":[{{"id":"...","name":"...","station":"...","servings":1,"serving_size":"...","notes":"..."}}]}}],"warnings":[],"protein_gap_plan":null}}
Use catalog ids only. Cover all 3 meals. Prefer high protein/kcal. Do not invent foods.
""".strip()
    return system, user


def ask_llm(system: str, user: str) -> dict[str, object]:
    client = Groq(api_key=groq_api_key())
    completion = client.chat.completions.create(
        model=groq_model(),
        temperature=0.2,
        reasoning_effort="medium",
        max_tokens=2500,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    content = completion.choices[0].message.content or "{}"
    return _extract_json(content)


def generate_plan(menu: DayMenu, profile: Profile, target: date) -> DayPlan:
    items = catalog_for_day(menu, profile)
    if not items:
        raise SystemExit(f"No vegetarian items with nutrition for {target.isoformat()}")
    compact = compact_catalog(items)
    system, user = build_prompt(target, profile, compact)
    raw = ask_llm(system, user)
    plan = recompute(dayplan_from_llm(raw, target), items, profile)
    if not plan_ok(plan, profile):
        plan = fill_gaps(plan, items, profile)
    return plan
