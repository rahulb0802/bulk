from datetime import date
from unittest import TestCase

from macro.models import DayPlan, MealPlan, PlannedItem, Profile
from macro.plan import build_meal_prompt, build_prompt, recompute
from macro.settings import load_staples, load_profile


def _empty_plan() -> DayPlan:
    return DayPlan(
        date="2026-09-26",
        meals=[
            MealPlan(name="breakfast", items=[PlannedItem(id="x", name="eggs", station="Grill")]),
            MealPlan(name="lunch"),
            MealPlan(name="dinner"),
        ],
    )


class PromptTest(TestCase):
    def test_day_prompt_includes_portion_and_staple_rules(self) -> None:
        profile = load_profile()
        _, user = build_prompt(date(2026, 9, 26), profile, [])
        self.assertIn("Prefer soy milk over dairy milk", user)
        self.assertIn("Peanut butter", user)
        self.assertIn("Default to 1 catalog serving", user)
        self.assertNotIn("Diner feedback", user)

    def test_meal_prompt_includes_feedback(self) -> None:
        profile = load_profile()
        _, user = build_meal_prompt(
            date(2026, 9, 26),
            profile,
            "lunch",
            [],
            _empty_plan(),
            missing="",
            message="too much rice, add tempeh",
        )
        self.assertIn("Diner feedback (must follow", user)
        self.assertIn("too much rice, add tempeh", user)
        self.assertIn("using the diner's feedback", user)
        self.assertIn("Prefer soy milk over dairy milk", user)

    def test_meal_prompt_keeps_outage_reason_when_food_is_missing(self) -> None:
        profile = load_profile()
        _, user = build_meal_prompt(
            date(2026, 9, 26),
            profile,
            "lunch",
            [],
            _empty_plan(),
            missing="cottage cheese",
            message="less sauce",
        )
        self.assertIn("A food just ran out", user)
        self.assertIn("less sauce", user)
        self.assertIn("cottage cheese", user)


class RecomputePortionCapTest(TestCase):
    def test_caps_servings_at_three(self) -> None:
        catalog = load_staples()
        milk = next(i for i in catalog if i.name == "Soy Milk" and i.meal == "breakfast")
        plan = DayPlan(
            date="2026-09-26",
            meals=[
                MealPlan(
                    name="breakfast",
                    items=[
                        PlannedItem(
                            id=milk.id,
                            name=milk.name,
                            station=milk.station,
                            servings=8,
                        )
                    ],
                )
            ],
        )
        rebuilt = recompute(plan, catalog, Profile())
        breakfast = rebuilt.meal("breakfast")
        assert breakfast is not None
        self.assertEqual(breakfast.items[0].servings, 3)
        self.assertEqual(breakfast.items[0].calories, round(milk.calories() * 3))
