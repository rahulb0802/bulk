from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from macro.models import MenuItem, Nutrition, Profile
from macro.paths import config_dir, project_root


def load_env() -> None:
    load_dotenv(project_root() / ".env")


def load_profile() -> Profile:
    path = config_dir() / "profile.yaml"
    raw = yaml.safe_load(path.read_text()) or {}
    return Profile.model_validate(raw)


def load_staples() -> list[MenuItem]:
    path = config_dir() / "staples.yaml"
    raw = yaml.safe_load(path.read_text()) or {}
    items: list[MenuItem] = []
    for row in raw.get("items", []):
        meals = row.get("meals") or ["breakfast", "lunch", "dinner"]
        nutrition = Nutrition(
            calories=float(row.get("calories") or 0),
            protein_g=float(row.get("protein_g") or 0),
            carbs_g=float(row.get("carbs_g") or 0),
            fat_g=float(row.get("fat_g") or 0),
            serving_size=str(row.get("serving_size") or ""),
        )
        for meal in meals:
            items.append(
                MenuItem(
                    id=str(row["id"]),
                    name=str(row["name"]),
                    station=str(row.get("station") or "Staples"),
                    meal=meal,
                    serving_size=nutrition.serving_size,
                    traits=["Vegetarian", "Staple"],
                    nutrition=nutrition,
                    staple=True,
                )
            )
    return items


def gemini_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise SystemExit("GEMINI_API_KEY is missing. Copy .env.example to .env.")
    return key


def gemini_models() -> list[str]:
    primary = os.environ.get("GEMINI_MODEL", "gemini-3.7-flash").strip()
    fallback = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.6-flash").strip()
    models = [primary]
    if fallback and fallback != primary:
        models.append(fallback)
    return models


def ntfy_topic() -> str | None:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    return topic or None


def ntfy_base_url() -> str:
    return os.environ.get("NTFY_BASE_URL", "https://ntfy.sh").rstrip("/")


def ensure_data_subdir(name: str) -> Path:
    path = project_root() / "data" / name
    path.mkdir(parents=True, exist_ok=True)
    return path
