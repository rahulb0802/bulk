from datetime import datetime
from unittest import TestCase
from zoneinfo import ZoneInfo

from macro.models import DayPlan, MealPlan, PlannedItem, Profile
from macro.notify import future_meal_names, meal_markdown, meal_notify_at


def _item(name: str = "eggs") -> PlannedItem:
    return PlannedItem(id="1", name=name, station="Grill")


def _plan() -> DayPlan:
    return DayPlan(
        date="2026-08-27",
        meals=[
            MealPlan(name="breakfast", items=[_item()]),
            MealPlan(name="lunch", items=[_item("naan")]),
            MealPlan(name="dinner", items=[_item("tempeh")]),
        ],
    )


def _profile() -> Profile:
    return Profile(
        timezone="America/Chicago",
        notify_lead_minutes=45,
        meals={"breakfast": "07:00", "lunch": "11:00", "dinner": "17:00"},
    )


class FutureMealNamesTest(TestCase):
    def test_lunch_replate_requeues_dinner_only(self) -> None:
        profile = _profile()
        tz = ZoneInfo(profile.timezone)
        now = datetime(2026, 8, 27, 11, 52, tzinfo=tz)
        self.assertEqual(
            future_meal_names(_plan(), profile, skip="lunch", now=now),
            ["dinner"],
        )

    def test_breakfast_replate_requeues_lunch_and_dinner(self) -> None:
        profile = _profile()
        tz = ZoneInfo(profile.timezone)
        now = datetime(2026, 8, 27, 6, 30, tzinfo=tz)
        self.assertEqual(
            future_meal_names(_plan(), profile, skip="breakfast", now=now),
            ["lunch", "dinner"],
        )

    def test_dinner_replate_requeues_nothing(self) -> None:
        profile = _profile()
        tz = ZoneInfo(profile.timezone)
        now = datetime(2026, 8, 27, 16, 30, tzinfo=tz)
        self.assertEqual(
            future_meal_names(_plan(), profile, skip="dinner", now=now),
            [],
        )

    def test_skips_empty_later_meals(self) -> None:
        profile = _profile()
        tz = ZoneInfo(profile.timezone)
        now = datetime(2026, 8, 27, 11, 52, tzinfo=tz)
        plan = DayPlan(
            date="2026-08-27",
            meals=[
                MealPlan(name="lunch", items=[_item("naan")]),
                MealPlan(name="dinner"),
            ],
        )
        self.assertEqual(future_meal_names(plan, profile, skip="lunch", now=now), [])

    def test_dinner_notify_is_still_in_the_future_at_lunch(self) -> None:
        profile = _profile()
        tz = ZoneInfo(profile.timezone)
        now = datetime(2026, 8, 27, 11, 52, tzinfo=tz)
        when = meal_notify_at(now.date(), "dinner", profile)
        self.assertGreater(when, now)
        self.assertEqual(when.hour, 16)
        self.assertEqual(when.minute, 15)


class MealMarkdownTest(TestCase):
    def test_lists_item_carbs_fat_and_calories(self) -> None:
        meal = MealPlan(
            name="breakfast",
            protein_g=61,
            carbs_g=91,
            fat_g=51,
            calories=1060,
            items=[
                PlannedItem(
                    id="sausage",
                    name="Vegetarian Sausage Patties",
                    station="Grillworks",
                    servings=2,
                    serving_size="Patty",
                    protein_g=18,
                    carbs_g=8,
                    fat_g=5,
                    calories=140,
                    notes="plant protein",
                )
            ],
        )
        text = meal_markdown(meal)
        self.assertIn("61g P · 91g C · 51g F · 1060 kcal", text)
        self.assertIn("2 × Patty", text)
        self.assertIn("18g P / 8g C / 5g F / 140 kcal", text)
        self.assertIn("plant protein", text)
