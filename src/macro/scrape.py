from __future__ import annotations

import json
import re
import time
from datetime import date
from html import unescape
from typing import Any
import httpx
from bs4 import BeautifulSoup

from macro.models import DayMenu, MenuItem, Nutrition
from macro.settings import ensure_data_subdir

BASE_URL = "https://eatsmart.housing.illinois.edu/NetNutrition/46"
ISR_UNIT_OID = 11
USER_AGENT = (
    "Mozilla/5.0 (compatible; ISR-macro-planner/0.1; personal academic use)"
)
VEG_TRAITS = {"vegetarian", "vegan"}
MEAT_TRAITS = {
    "pork",
    "beef",
    "chicken",
    "turkey",
    "fish",
    "shellfish",
    "meat",
}
MEAL_ALIASES = {
    "breakfast": "breakfast",
    "continental breakfast": "breakfast",
    "lunch": "lunch",
    "light lunch": "lunch",
    "dinner": "dinner",
}


class EatSmartError(RuntimeError):
    pass


class EatSmartClient:
    def __init__(self, delay_s: float = 0.25) -> None:
        self.delay_s = delay_s
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "X-Requested-With": "XMLHttpRequest"},
            follow_redirects=True,
            timeout=30.0,
        )
        self._started = False

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> EatSmartClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _sleep(self) -> None:
        time.sleep(self.delay_s)

    def start(self) -> None:
        if self._started:
            return
        response = self._client.get(BASE_URL)
        response.raise_for_status()
        self._started = True
        self._sleep()

    def _post(self, controller: str, action: str, data: dict[str, Any]) -> httpx.Response:
        self.start()
        url = f"{BASE_URL}/{controller}/{action}"
        response = self._client.post(url, data=data, headers={"Referer": BASE_URL})
        response.raise_for_status()
        self._sleep()
        return response

    def _post_json(self, controller: str, action: str, data: dict[str, Any]) -> dict[str, Any]:
        response = self._post(controller, action, data)
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise EatSmartError(f"{controller}/{action} did not return JSON") from exc
        if not payload.get("success", True):
            raise EatSmartError(f"{controller}/{action} failed: {payload}")
        return payload

    def panel_html(self, payload: dict[str, Any], panel_id: str) -> str:
        for panel in payload.get("panels") or []:
            if panel.get("id") == panel_id:
                return unescape(panel.get("html") or "")
        return ""

    def select_isr(self) -> list[tuple[int, str]]:
        payload = self._post_json("Unit", "SelectUnitFromSideBar", {"unitOid": ISR_UNIT_OID})
        html = self.panel_html(payload, "childUnitsPanel")
        soup = BeautifulSoup(html, "lxml")
        stations: list[tuple[int, str]] = []
        for link in soup.select("a[onclick*='childUnitsSelectUnit']"):
            match = re.search(r"childUnitsSelectUnit\((\d+)\)", link.get("onclick") or "")
            name = link.get_text(strip=True)
            if match and name:
                stations.append((int(match.group(1)), name))
        if not stations:
            raise EatSmartError("No ISR child stations found")
        return stations

    def select_station(self, unit_oid: int) -> str:
        payload = self._post_json(
            "Unit", "SelectUnitFromChildUnitsList", {"unitOid": unit_oid}
        )
        return self.panel_html(payload, "menuPanel")

    def select_menu(self, menu_oid: int) -> str:
        payload = self._post_json("Menu", "SelectMenu", {"menuOid": menu_oid})
        return self.panel_html(payload, "itemPanel")

    def nutrition_label(self, detail_oid: str, menu_oid: int | None = None) -> str:
        data: dict[str, Any] = {"detailOid": detail_oid}
        if menu_oid is not None:
            data["menuOid"] = menu_oid
        response = self._post("NutritionDetail", "ShowItemNutritionLabel", data)
        ctype = response.headers.get("content-type", "")
        if "json" in ctype or response.text.lstrip().startswith("{"):
            payload = response.json()
            if isinstance(payload, dict) and payload.get("html"):
                return unescape(payload["html"])
            return unescape(self.panel_html(payload, "nutritionLabel") or response.text)
        return unescape(response.text)


def parse_menus_for_date(menu_list_html: str, target: date) -> dict[str, int]:
    """Return meal -> menuOid for the target calendar date."""
    soup = BeautifulSoup(menu_list_html, "lxml")
    wanted = _format_menu_date(target)
    found: dict[str, int] = {}
    for card in soup.select("section.card"):
        header = card.select_one("header.card-title")
        if not header or header.get_text(strip=True) != wanted:
            continue
        for link in card.select("a.cbo_nn_menuLink"):
            meal_raw = link.get_text(strip=True)
            meal = MEAL_ALIASES.get(meal_raw.lower())
            match = re.search(r"menuListSelectMenu\((\d+)\)", link.get("onclick") or "")
            if meal and match:
                found[meal] = int(match.group(1))
    return found


def _format_menu_date(target: date) -> str:
    return f"{target.strftime('%A, %B')} {target.day}, {target.year}"


def parse_menu_items(item_html: str, station: str, meal: str) -> list[MenuItem]:
    soup = BeautifulSoup(item_html, "lxml")
    items: list[MenuItem] = []
    current_course = ""
    table = soup.select_one("table.table")
    if table is None:
        return items
    for row in table.select("tr"):
        if "cbo_nn_itemGroupRow" in (row.get("class") or []):
            current_course = row.get_text(" ", strip=True)
            continue
        link = row.select_one("a.cbo_nn_itemHover")
        if link is None:
            continue
        onclick = link.get("onclick") or ""
        match = re.search(r"getItemNutritionLabelOnClick\(event,(\d+)\)", onclick)
        if not match:
            match = re.search(r"getItemNutritionLabel\((\d+)\)", onclick)
        if not match:
            continue
        detail_id = match.group(1)
        name = "".join(
            child for child in link.find_all(string=True, recursive=False)
        ).strip()
        if not name:
            name = link.get_text(" ", strip=True)
        traits = [
            img.get("title", "").strip()
            for img in link.select("img[title]")
            if img.get("title")
        ]
        serving = ""
        cells = row.find_all("td")
        if len(cells) >= 3:
            serving = cells[2].get_text(" ", strip=True)
        items.append(
            MenuItem(
                id=detail_id,
                name=_clean_name(name),
                station=station,
                meal=meal,
                serving_size=serving,
                course=current_course,
                traits=traits,
            )
        )
    return items


def _clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip()
    return name


def is_vegetarian_item(item: MenuItem) -> bool:
    lowered = {t.lower() for t in item.traits}
    if lowered & MEAT_TRAITS:
        return False
    return bool(lowered & VEG_TRAITS)


def _plausible_serving(nutrition: Nutrition) -> bool:
    """Drop pan/tray labels that would wreck a personal plate plan."""
    if nutrition.calories <= 0:
        return False
    if nutrition.calories > 900:
        return False
    if nutrition.protein_g > 40:
        return False
    return True


def parse_nutrition_label(html: str) -> Nutrition:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    serving = ""
    serve_el = soup.select_one(".cbo_nn_LabelBottomBorderLabel")
    if serve_el:
        serving = serve_el.get_text(" ", strip=True)
        serving = re.sub(r"^Serving Size:\s*", "", serving, flags=re.I)
        serving = serving.replace("\xa0", " ").strip()
    nutrients: dict[str, float] = {}
    for label, pattern in (
        ("calories", r"Calories\s+(\d+(?:\.\d+)?)"),
        ("protein_g", r"Protein\s+([\d.]+)\s*g"),
        ("carbs_g", r"Total Carbohydrate\s+([\d.]+)\s*g"),
        ("fat_g", r"Total Fat\s+([\d.]+)\s*g"),
    ):
        match = re.search(pattern, text, flags=re.I)
        if match:
            nutrients[label] = float(match.group(1))
    return Nutrition(
        calories=nutrients.get("calories", 0),
        protein_g=nutrients.get("protein_g", 0),
        carbs_g=nutrients.get("carbs_g", 0),
        fat_g=nutrients.get("fat_g", 0),
        serving_size=serving,
    )


def _cache_path() -> Any:
    return ensure_data_subdir("cache") / "nutrition.json"


def load_nutrition_cache() -> dict[str, dict[str, Any]]:
    path = _cache_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_nutrition_cache(cache: dict[str, dict[str, Any]]) -> None:
    _cache_path().write_text(json.dumps(cache, indent=2, sort_keys=True))


def scrape_isr(target: date) -> DayMenu:
    cache = load_nutrition_cache()
    day = DayMenu(date=target.isoformat(), hall="ISR")
    seen: set[tuple[str, str, str]] = set()

    with EatSmartClient() as client:
        stations = client.select_isr()
        for unit_oid, station_name in stations:
            client.select_isr()
            menu_html = client.select_station(unit_oid)
            menus = parse_menus_for_date(menu_html, target)
            if not menus:
                continue
            for meal, menu_oid in menus.items():
                item_html = client.select_menu(menu_oid)
                raw_items = parse_menu_items(item_html, station_name, meal)
                veg_items = [item for item in raw_items if is_vegetarian_item(item)]
                for item in veg_items:
                    key = (item.meal, item.station, item.name)
                    if key in seen:
                        continue
                    seen.add(key)
                    cached = cache.get(item.id)
                    if cached:
                        item.nutrition = Nutrition.model_validate(cached)
                    else:
                        try:
                            label = client.nutrition_label(item.id, menu_oid)
                            item.nutrition = parse_nutrition_label(label)
                            cache[item.id] = item.nutrition.model_dump()
                        except Exception:
                            item.nutrition = None
                    if item.nutrition and item.nutrition.calories == 0 and item.nutrition.protein_g == 0:
                        continue
                    if item.nutrition is None:
                        continue
                    if not _plausible_serving(item.nutrition):
                        continue
                    if not item.serving_size and item.nutrition.serving_size:
                        item.serving_size = item.nutrition.serving_size
                    day.items.append(item)

    save_nutrition_cache(cache)
    return day


def save_menu(day: DayMenu) -> None:
    path = ensure_data_subdir("menus") / f"{day.date}.json"
    path.write_text(day.model_dump_json(indent=2))


def load_menu(target: date) -> DayMenu | None:
    path = ensure_data_subdir("menus") / f"{target.isoformat()}.json"
    if not path.exists():
        return None
    return DayMenu.model_validate_json(path.read_text())
