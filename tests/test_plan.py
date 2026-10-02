from datetime import date
from unittest import TestCase

from macro.models import DayPlan, MealPlan, MenuItem, Nutrition, PlannedItem, Profile
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
        self.assertIn("2–3 servings of a protein or a starch is normal", user)
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
    def test_caps_extreme_servings(self) -> None:
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
        self.assertEqual(breakfast.items[0].servings, 5)
        self.assertEqual(breakfast.items[0].calories, round(milk.calories() * 5))


def _grill(name: str, item_id: str, serving: str, **nutrients: float) -> MenuItem:
    return MenuItem(
        id=item_id,
        name=name,
        station="Grillworks",
        meal="breakfast",
        serving_size=serving,
        traits=["Vegetarian"],
        nutrition=Nutrition(serving_size=serving, **nutrients),
    )


class BreakfastMacroRecomputeTest(TestCase):
    def test_isr_breakfast_fat_is_eggs_and_peanut_butter_not_sausage(self) -> None:
        """EatSmart 2026-10-02 labels: sausage is 2.5g fat/patty, not the 51g meal total.

        2 scrambled eggs (11g F each) + 2 Tbsp peanut butter (16g) dominate fat.
        """
        eggs = _grill(
            "Scrambled Eggs",
            "eggs",
            "1/3 Cup",
            calories=150,
            protein_g=9,
            carbs_g=0,
            fat_g=11,
        )
        sausage = _grill(
            "Vegetarian Sausage Patties",
            "sausage",
            "Patty",
            calories=70,
            protein_g=9,
            carbs_g=4,
            fat_g=2.5,
        )
        zucchini = _grill(
            "Herb Roasted Zucchini & Tomato Bake",
            "zucchini",
            "Cup",
            calories=70,
            protein_g=2,
            carbs_g=11,
            fat_g=3,
        )
        staples = {
            item.name: item
            for item in load_staples()
            if item.meal == "breakfast"
            and item.name in {"Bagel", "Peanut Butter", "Soy Milk"}
        }
        catalog = [eggs, sausage, zucchini, *staples.values()]
        plan = DayPlan(
            date="2026-10-02",
            meals=[
                MealPlan(
                    name="breakfast",
                    items=[
                        PlannedItem(id=eggs.id, name=eggs.name, station=eggs.station, servings=2),
                        PlannedItem(
                            id=sausage.id, name=sausage.name, station=sausage.station, servings=2
                        ),
                        PlannedItem(
                            id=staples["Bagel"].id,
                            name="Bagel",
                            station="Deli & Bagel Bar",
                            servings=1,
                        ),
                        PlannedItem(
                            id=staples["Peanut Butter"].id,
                            name="Peanut Butter",
                            station="Condiments",
                            servings=1,
                        ),
                        PlannedItem(
                            id=staples["Soy Milk"].id,
                            name="Soy Milk",
                            station="Beverages",
                            servings=1,
                        ),
                        PlannedItem(
                            id=zucchini.id,
                            name=zucchini.name,
                            station=zucchini.station,
                            servings=1,
                        ),
                    ],
                )
            ],
        )
        rebuilt = recompute(plan, catalog, Profile())
        breakfast = rebuilt.meal("breakfast")
        assert breakfast is not None
        by_name = {item.name: item for item in breakfast.items}
        self.assertEqual(by_name["Vegetarian Sausage Patties"].fat_g, 5.0)
        self.assertEqual(by_name["Vegetarian Sausage Patties"].protein_g, 18.0)
        self.assertEqual(by_name["Scrambled Eggs"].fat_g, 22.0)
        self.assertEqual(by_name["Peanut Butter"].fat_g, 16.0)
        self.assertEqual(breakfast.protein_g, 61.0)
        self.assertEqual(breakfast.carbs_g, 91.0)
        self.assertEqual(breakfast.fat_g, 51.0)
        self.assertEqual(breakfast.calories, 1060)
