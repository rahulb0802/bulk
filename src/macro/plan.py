from __future__ import annotations

import json
import re
from datetime import date

from google import genai
from google.genai import types

from macro.models import (
    DayMenu,
    DayPlan,
    MealBand,
    MealPlan,
    MenuItem,
    PlannedItem,
    Profile,
    default_meal_bands,
)
from macro.outages import Outage, apply_outages, describe_outages
from macro.settings import gemini_api_key, gemini_models, load_staples

MEALS = ("breakfast", "lunch", "dinner")


def meal_band(profile: Profile, meal: str) -> MealBand:
    bands = profile.meal_bands or default_meal_bands()
    if meal in bands:
        return bands[meal]
    return default_meal_bands()[meal]


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


def catalog_rows(items: list[MenuItem]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    ordered = sorted(
        items,
        key=lambda i: (MEALS.index(i.meal) if i.meal in MEALS else 99, -i.protein_g()),
    )
    for item in ordered:
        nutrition = item.nutrition
        assert nutrition is not None
        rows.append(
            {
                "id": item.id,
                "name": item.name,
                "station": item.station,
                "meal": item.meal,
                "serving": item.serving_size or nutrition.serving_size,
                "course": item.course,
                "kcal": round(nutrition.calories),
                "p": round(nutrition.protein_g, 1),
                "c": round(nutrition.carbs_g, 1),
                "f": round(nutrition.fat_g, 1),
            }
        )
    return rows


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
                    carbs_g=round(nutrition.carbs_g * servings, 1),
                    fat_g=round(nutrition.fat_g * servings, 1),
                    notes=raw.notes,
                )
            )
        rebuilt = rebuilt[: profile.max_items_per_meal]
        protein = sum(i.protein_g for i in rebuilt)
        calories = sum(i.calories for i in rebuilt)
        carbs = sum(i.carbs_g for i in rebuilt)
        fat = sum(i.fat_g for i in rebuilt)
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
                carbs_g=round(carbs, 1),
                fat_g=round(fat, 1),
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
    plan.carbs_g = round(sum(m.carbs_g for m in meals), 1)
    plan.fat_g = round(sum(m.fat_g for m in meals), 1)
    plan.warnings = warnings
    return plan


def plan_issues(plan: DayPlan, profile: Profile) -> list[str]:
    issues: list[str] = []
    if plan.protein_g < profile.protein_g:
        issues.append(
            f"Day protein {plan.protein_g:g}g is below the {profile.protein_g:g}g dining-hall floor"
        )
    if plan.calories < profile.calories_min - 50:
        issues.append(f"Day calories {plan.calories} below {profile.calories_min}")
    if plan.calories > profile.calories_max + 150:
        issues.append(f"Day calories {plan.calories} above {profile.calories_max}")
    for meal in plan.meals:
        band = meal_band(profile, meal.name)
        if meal.calories < band.calories_min:
            issues.append(
                f"{meal.name} is {meal.calories} kcal; raise it to {band.calories_min:g}-{band.calories_max:g}"
            )
        if meal.calories > band.calories_max:
            issues.append(
                f"{meal.name} is {meal.calories} kcal; cut it to {band.calories_min:g}-{band.calories_max:g} "
                "and move food to another meal"
            )
        if meal.protein_g < band.protein_min:
            issues.append(
                f"{meal.name} has {meal.protein_g:g}g protein; need at least {band.protein_min:g}g"
            )
    return issues


def plan_ok(plan: DayPlan, profile: Profile) -> bool:
    return not plan_issues(plan, profile)


def fill_gaps(plan: DayPlan, catalog: list[MenuItem], profile: Profile) -> DayPlan:
    """Greedy fill from the posted catalog if the LLM undershoots protein/calorie targets."""
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
                carbs_g=round(nutrition.carbs_g * servings, 1),
                fat_g=round(nutrition.fat_g * servings, 1),
                notes=note or "Added to hit targets",
            )
        )
        if item.station not in meal.station_order:
            meal.station_order.append(item.station)

    def room_in(meal: MealPlan) -> bool:
        band = meal_band(profile, meal.name)
        return meal.calories < band.calories_max - 40

    for _ in range(30):
        plan = recompute(plan, catalog, profile)
        short_meals = []
        for meal in plan.meals:
            band = meal_band(profile, meal.name)
            if meal.calories < band.calories_min or meal.protein_g < band.protein_min:
                short_meals.append(meal)
        need_protein = plan.protein_g < profile.protein_g
        need_cals = plan.calories < profile.calories_min
        if not short_meals and not need_protein and not need_cals:
            break
        used = {(m.name, i.id) for m in plan.meals for i in m.items}
        if short_meals:
            meal = min(short_meals, key=lambda m: m.calories)
            need_protein_here = meal.protein_g < meal_band(profile, meal.name).protein_min or need_protein
        else:
            candidates = [m for m in plan.meals if room_in(m)]
            if not candidates:
                break
            meal = min(
                candidates,
                key=lambda m: m.protein_g if need_protein else m.calories,
            )
            need_protein_here = need_protein
        if not room_in(meal) and len(meal.items) >= profile.max_items_per_meal:
            break
        if len(meal.items) < profile.max_items_per_meal:
            pool = protein_sorted[meal.name] if need_protein_here else calorie_sorted[meal.name]
            nxt = next((i for i in pool if (meal.name, i.id) not in used), None)
            if nxt is None:
                break
            add_to(
                meal,
                nxt,
                1,
                "Added to hit protein floor" if need_protein_here else "Added to balance meal calories",
            )
            continue
        extras = [item for item in meal.items if item.servings < 4]
        if not extras:
            break
        extras.sort(
            key=lambda i: i.protein_g / max(i.calories, 1) if need_protein_here else i.calories,
            reverse=True,
        )
        extras[0].servings += 1

    plan = recompute(plan, catalog, profile)
    leftover = plan_issues(plan, profile)
    if plan.protein_g < profile.protein_g:
        gap = round(profile.protein_g - plan.protein_g, 1)
        plan.protein_gap_plan = (
            f"Still {gap}g protein short of {profile.protein_g}g from the posted menu. "
            "Take extra servings of the highest-protein listed items."
        )
        plan.warnings.append(plan.protein_gap_plan)
    for issue in leftover:
        if issue not in plan.warnings:
            plan.warnings.append(issue)
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


def _band_lines(profile: Profile) -> str:
    lines = []
    for meal in MEALS:
        band = meal_band(profile, meal)
        lines.append(
            f"- {meal}: {band.calories_min:g}-{band.calories_max:g} kcal, "
            f"≥{band.protein_min:g}g protein, include carbs and fat"
        )
    return "\n".join(lines)


def build_prompt(
    target: date,
    profile: Profile,
    catalog: list[dict[str, object]],
    retry_hint: str | None = None,
) -> tuple[str, str]:
    system = (
        "You are a dining-hall plate coach for an ovo-lacto vegetarian lifter. "
        "Reason about the menu: what belongs on a plate together, what tastes "
        "complete, what is actually healthy training food, and how carbs and fat "
        "support the protein target. Choose only catalog items. Do not invent foods. "
        "Return a single JSON object."
    )
    retry = f"\nFix these protein/calorie problems and rebuild the whole day:\n{retry_hint}\n" if retry_hint else ""
    user = f"""ISR dining hall plan for {target.isoformat()}. Ovo-lacto vegetarian lifter.

Dining-hall protein floor (one shake is outside this plan; do not include shakes): {profile.protein_g} g
Day calorie band: {profile.calories_min}-{profile.calories_max} kcal
Max items per meal: {profile.max_items_per_meal}

Meal bands (hard constraints — do not dump calories into one meal):
{_band_lines(profile)}

Reason about each plate before you pick items:
- Build a complete meal: a protein center, enough carbs for lifting, and some healthy fat. Do not serve protein-only plates or a pile of steamed vegetables with a random sauce.
- Taste and pairing: sauces and toppings only go with foods they belong on. Marinara belongs on pasta, not edamame and broccoli. Oatmeal should include a topping from the catalog (brown sugar, fruit, honey, nuts, yogurt) if one exists; if none exists, pick a different breakfast rather than serving it plain.
- Health: prefer whole, training-friendly foods (eggs, yogurt, tofu, beans, grains, fruit, vegetables, simple cooked entrees). Skip pizza, fries, dessert, and similar junk even if the calories look convenient. Pasta is a fine carb; pizza is not.
- Vegetables are a side, not the meal. Prefer fewer stations when it does not wreck the plate.
- Servings: never use fractions for whole/discrete food items. Eggs, muffins, bagels, bananas, apples, cookies, patties, pieces of fruit, and similar countables must be whole numbers (1, 2, 3…). Do not prescribe half an egg or 1.5 muffins. Fractional servings are only allowed for scoopable or pourable foods (oatmeal, rice, yogurt, sauce, beans by volume, etc.). If macros need a nudge, add or drop a whole item or another catalog food instead of splitting one.

Catalog fields: id, name, station, meal, serving, course, kcal, p, c, f. Use p/c/f to balance the plate; Python will only check protein and calories.
{profile.notes.strip()}
{retry}
Catalog (use these ids only):
{json.dumps(catalog, indent=2)}

Return JSON:
{{
  "meals": [
    {{
      "name": "breakfast" | "lunch" | "dinner",
      "station_order": ["walking order"],
      "plate_tips": "how to combine these so they taste like a real meal; what to skip or double",
      "items": [
        {{
          "id": "catalog id",
          "name": "exact catalog name",
          "station": "station",
          "servings": 1,
          "serving_size": "from catalog",
          "notes": "why this is on the plate / how it pairs"
        }}
      ]
    }}
  ],
  "warnings": [],
  "protein_gap_plan": null
}}

Cover all 3 meals. Hit the protein floor without starving carbs or fat. Do not invent foods.
""".strip()
    return system, user


def _should_fallback(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status in {404, 429, 500, 503}:
        return True
    text = str(exc).lower()
    markers = (
        "404",
        "429",
        "503",
        "not found",
        "not_found",
        "not supported",
        "unknown model",
        "invalid model",
        "high demand",
        "overloaded",
        "unavailable",
        "resource exhausted",
        "resource_exhausted",
        "capacity",
        "rate limit",
        "rate_limit",
        "quota",
        "try again",
        "temporarily",
    )
    return any(marker in text for marker in markers)


def ask_llm(system: str, user: str) -> dict[str, object]:
    client = genai.Client(api_key=gemini_api_key())
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        thinking_config=types.ThinkingConfig(thinking_level="high"),
    )
    last_error: BaseException | None = None
    for model in gemini_models():
        try:
            response = client.models.generate_content(
                model=model,
                contents=user,
                config=config,
            )
            print(f"Planned with {model}")
            return _extract_json(response.text or "{}")
        except Exception as exc:
            last_error = exc
            if _should_fallback(exc):
                print(f"{model} unavailable ({exc}); trying fallback")
                continue
            raise
    raise SystemExit(f"Gemini request failed: {last_error}")


def meal_plan_issues(plan: DayPlan, profile: Profile, meal_name: str) -> list[str]:
    """Issues the replacement plate can still fix: that meal's band plus day totals."""
    issues: list[str] = []
    if plan.protein_g < profile.protein_g:
        issues.append(
            f"Day protein {plan.protein_g:g}g is below the {profile.protein_g:g}g dining-hall floor"
        )
    if plan.calories < profile.calories_min - 50:
        issues.append(f"Day calories {plan.calories} below {profile.calories_min}")
    if plan.calories > profile.calories_max + 150:
        issues.append(f"Day calories {plan.calories} above {profile.calories_max}")
    meal = plan.meal(meal_name)
    if meal is None:
        issues.append(f"{meal_name} is missing from the plan")
        return issues
    band = meal_band(profile, meal_name)
    if meal.calories < band.calories_min:
        issues.append(
            f"{meal.name} is {meal.calories} kcal; raise it to {band.calories_min:g}-{band.calories_max:g}"
        )
    if meal.calories > band.calories_max:
        issues.append(
            f"{meal.name} is {meal.calories} kcal; cut it to {band.calories_min:g}-{band.calories_max:g}"
        )
    if meal.protein_g < band.protein_min:
        issues.append(
            f"{meal.name} has {meal.protein_g:g}g protein; need at least {band.protein_min:g}g"
        )
    return issues


def _locked_meal_lines(plan: DayPlan, skip: str) -> str:
    lines = []
    for meal in plan.meals:
        if meal.name == skip:
            continue
        names = ", ".join(
            f"{item.name} x{item.servings:g}" for item in meal.items
        ) or "(empty)"
        lines.append(
            f"- {meal.name} LOCKED ({meal.protein_g:g}g P / {meal.calories:g} kcal): {names}"
        )
    return "\n".join(lines) if lines else "- (no other meals)"


def build_meal_prompt(
    target: date,
    profile: Profile,
    meal_name: str,
    catalog: list[dict[str, object]],
    existing: DayPlan,
    missing: str,
    retry_hint: str | None = None,
) -> tuple[str, str]:
    system = (
        "You are a dining-hall plate coach for an ovo-lacto vegetarian lifter. "
        "Reason about the menu: what belongs on a plate together, what tastes "
        "complete, what is actually healthy training food, and how carbs and fat "
        "support the protein target. Choose only catalog items. Do not invent foods. "
        "Return a single JSON object."
    )
    band = meal_band(profile, meal_name)
    retry = (
        f"\nFix these protein/calorie problems and rebuild only {meal_name}:\n{retry_hint}\n"
        if retry_hint
        else ""
    )
    missing_line = f"These foods are OUT and must not appear: {missing}.\n" if missing else ""
    user = f"""ISR dining hall {meal_name} replan for {target.isoformat()}. Ovo-lacto vegetarian lifter.

A food just ran out at the hall. Rebuild ONLY {meal_name} from the remaining catalog.
Do not change the locked meals. Do not invent foods. Do not use items marked out.

Dining-hall protein floor (one shake is outside this plan; do not include shakes): {profile.protein_g} g
Day calorie band: {profile.calories_min}-{profile.calories_max} kcal
Max items per meal: {profile.max_items_per_meal}
{meal_name} band: {band.calories_min:g}-{band.calories_max:g} kcal, ≥{band.protein_min:g}g protein, include carbs and fat
{missing_line}
Locked meals (do not output these; they already happened or still stand):
{_locked_meal_lines(existing, meal_name)}

Reason about the replacement plate:
- Build a complete meal: a protein center, enough carbs for lifting, and some healthy fat. Do not serve protein-only plates or a pile of steamed vegetables with a random sauce.
- Taste and pairing: sauces and toppings only go with foods they belong on. Marinara belongs on pasta, not edamame and broccoli. Oatmeal should include a topping from the catalog (brown sugar, fruit, honey, nuts, yogurt) if one exists; if none exists, pick a different breakfast rather than serving it plain.
- Health: prefer whole, training-friendly foods (eggs, yogurt, tofu, beans, grains, fruit, vegetables, simple cooked entrees). Skip pizza, fries, dessert, and similar junk even if the calories look convenient. Pasta is a fine carb; pizza is not.
- Vegetables are a side, not the meal. Prefer fewer stations when it does not wreck the plate.
- Servings: never use fractions for whole/discrete food items. Eggs, muffins, bagels, bananas, apples, cookies, patties, pieces of fruit, and similar countables must be whole numbers (1, 2, 3…). Do not prescribe half an egg or 1.5 muffins. Fractional servings are only allowed for scoopable or pourable foods (oatmeal, rice, yogurt, sauce, beans by volume, etc.). If macros need a nudge, add or drop a whole item or another catalog food instead of splitting one.

Catalog fields: id, name, station, meal, serving, course, kcal, p, c, f. Use p/c/f to balance the plate; Python will only check protein and calories.
{profile.notes.strip()}
{retry}
Remaining {meal_name} catalog (use these ids only):
{json.dumps(catalog, indent=2)}

Return JSON:
{{
  "meals": [
    {{
      "name": "{meal_name}",
      "station_order": ["walking order"],
      "plate_tips": "how to combine these so they taste like a real meal; what to skip or double",
      "items": [
        {{
          "id": "catalog id",
          "name": "exact catalog name",
          "station": "station",
          "servings": 1,
          "serving_size": "from catalog",
          "notes": "why this is on the plate / how it pairs"
        }}
      ]
    }}
  ],
  "warnings": [],
  "protein_gap_plan": null
}}

Return only {meal_name}. Hit the protein floor with the locked meals plus this plate. Do not invent foods.
""".strip()
    return system, user


def _splice_meal(existing: DayPlan, rebuilt: DayPlan, meal_name: str) -> DayPlan:
    replacement = rebuilt.meal(meal_name)
    if replacement is None:
        meals_from_llm = [m for m in rebuilt.meals if m.name == meal_name]
        replacement = meals_from_llm[0] if meals_from_llm else MealPlan(name=meal_name)
    meals = []
    seen = False
    for meal in existing.meals:
        if meal.name == meal_name:
            meals.append(replacement)
            seen = True
        else:
            meals.append(meal)
    if not seen:
        meals.append(replacement)
    warnings = list(existing.warnings)
    for warning in rebuilt.warnings:
        if warning not in warnings:
            warnings.append(warning)
    return DayPlan(
        date=existing.date,
        hall=existing.hall,
        meals=meals,
        warnings=warnings,
        protein_gap_plan=rebuilt.protein_gap_plan or existing.protein_gap_plan,
    )


def generate_plan(
    menu: DayMenu,
    profile: Profile,
    target: date,
    outages: list[Outage] | None = None,
) -> DayPlan:
    items = apply_outages(catalog_for_day(menu, profile), outages or [])
    if not items:
        raise SystemExit(f"No vegetarian items with nutrition for {target.isoformat()}")
    rows = catalog_rows(items)
    retry_hint = None
    plan: DayPlan | None = None
    for attempt in range(2):
        system, user = build_prompt(target, profile, rows, retry_hint)
        raw = ask_llm(system, user)
        plan = recompute(dayplan_from_llm(raw, target), items, profile)
        issues = plan_issues(plan, profile)
        if not issues:
            return plan
        retry_hint = "; ".join(issues)
        print(f"Retrying plan ({attempt + 1}): {retry_hint}")
    assert plan is not None
    return fill_gaps(plan, items, profile)


def generate_meal_plan(
    menu: DayMenu,
    profile: Profile,
    target: date,
    meal_name: str,
    existing: DayPlan,
    outages: list[Outage],
) -> DayPlan:
    if meal_name not in MEALS:
        raise SystemExit(f"Unknown meal {meal_name!r}; use breakfast, lunch, or dinner")
    catalog = apply_outages(catalog_for_day(menu, profile), outages, plan=existing)
    meal_catalog = [item for item in catalog if item.meal == meal_name]
    if not meal_catalog:
        raise SystemExit(
            f"No remaining {meal_name} catalog items after outages for {target.isoformat()}"
        )
    rows = catalog_rows(meal_catalog)
    missing = describe_outages(
        [outage for outage in outages if not outage.meal or outage.meal == meal_name]
    )
    retry_hint = None
    plan: DayPlan | None = None
    for attempt in range(2):
        system, user = build_meal_prompt(
            target, profile, meal_name, rows, existing, missing, retry_hint
        )
        raw = ask_llm(system, user)
        rebuilt = dayplan_from_llm(raw, target)
        plan = recompute(_splice_meal(existing, rebuilt, meal_name), catalog, profile)
        note = f"Replanned {meal_name} without: {missing}" if missing else f"Replanned {meal_name}"
        if note not in plan.warnings:
            plan.warnings.append(note)
        issues = meal_plan_issues(plan, profile, meal_name)
        if not issues:
            return plan
        retry_hint = "; ".join(issues)
        print(f"Retrying {meal_name} ({attempt + 1}): {retry_hint}")
    assert plan is not None
    return fill_gaps(plan, catalog, profile)
