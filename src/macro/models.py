from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Nutrition(BaseModel):
    calories: float = 0
    protein_g: float = 0
    carbs_g: float = 0
    fat_g: float = 0
    serving_size: str = ""


class MenuItem(BaseModel):
    id: str
    name: str
    station: str
    meal: str
    serving_size: str = ""
    course: str = ""
    traits: list[str] = Field(default_factory=list)
    nutrition: Nutrition | None = None
    staple: bool = False

    def protein_g(self) -> float:
        return self.nutrition.protein_g if self.nutrition else 0.0

    def calories(self) -> float:
        return self.nutrition.calories if self.nutrition else 0.0


class DayMenu(BaseModel):
    date: str
    hall: str = "ISR"
    items: list[MenuItem] = Field(default_factory=list)


class PlannedItem(BaseModel):
    id: str
    name: str
    station: str
    servings: float = 1
    serving_size: str = ""
    protein_g: float = 0
    calories: float = 0
    notes: str = ""


class MealPlan(BaseModel):
    name: str
    items: list[PlannedItem] = Field(default_factory=list)
    station_order: list[str] = Field(default_factory=list)
    plate_tips: str = ""
    protein_g: float = 0
    calories: float = 0


class DayPlan(BaseModel):
    date: str
    hall: str = "ISR"
    meals: list[MealPlan] = Field(default_factory=list)
    protein_g: float = 0
    calories: float = 0
    carbs_g: float = 0
    fat_g: float = 0
    warnings: list[str] = Field(default_factory=list)
    protein_gap_plan: str | None = None

    def meal(self, name: str) -> MealPlan | None:
        for meal in self.meals:
            if meal.name == name:
                return meal
        return None


class Profile(BaseModel):
    diet: str = "ovo-lacto vegetarian"
    protein_g: float = 110
    calories_min: float = 2200
    calories_max: float = 2400
    max_items_per_meal: int = 6
    timezone: str = "America/Chicago"
    notify_lead_minutes: int = 45
    meals: dict[str, str] = Field(
        default_factory=lambda: {
            "breakfast": "07:00",
            "lunch": "11:15",
            "dinner": "17:15",
        }
    )
    dislikes: list[str] = Field(default_factory=list)
    notes: str = ""


def totals_dict(protein_g: float, calories: float, **extra: Any) -> dict[str, Any]:
    return {"protein_g": round(protein_g, 1), "calories": round(calories), **extra}
