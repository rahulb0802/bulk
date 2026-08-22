from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from macro.models import DayPlan, Profile
from macro.notify import (
    cancel_plan_notifications,
    notify_meal,
    notify_plan,
    plan_to_markdown,
)
from macro.outages import Outage, infer_meal, load_outages, publish_outage
from macro.plan import MEALS, generate_meal_plan, generate_plan
from macro.scrape import load_menu, save_menu, scrape_isr
from macro.settings import ensure_data_subdir, load_env, load_profile


def resolve_date(value: str, timezone: str) -> date:
    tz = ZoneInfo(timezone)
    today = datetime.now(tz).date()
    if value in {"today", ""}:
        return today
    if value == "tomorrow":
        return today + timedelta(days=1)
    return date.fromisoformat(value)


def save_plan(plan: DayPlan) -> None:
    folder = ensure_data_subdir("plans")
    (folder / f"{plan.date}.json").write_text(plan.model_dump_json(indent=2))
    (folder / f"{plan.date}.md").write_text(plan_to_markdown(plan))


def load_saved_plan(target: date) -> DayPlan | None:
    path = ensure_data_subdir("plans") / f"{target.isoformat()}.json"
    if not path.exists():
        return None
    return DayPlan.model_validate_json(path.read_text())


def load_or_scrape_menu(target: date, do_scrape: bool):
    menu = None if do_scrape else load_menu(target)
    if menu is None:
        cmd_scrape(target)
        menu = load_menu(target)
    if menu is None:
        raise SystemExit("Scrape produced no menu file")
    return menu


def cmd_scrape(target: date) -> None:
    print(f"Scraping ISR EatSmart menu for {target.isoformat()}...")
    menu = scrape_isr(target)
    save_menu(menu)
    print(
        f"Saved {len(menu.items)} vegetarian items with nutrition "
        f"-> data/menus/{target.isoformat()}.json"
    )


def cmd_plan(target: date, profile: Profile, do_scrape: bool, do_notify: bool) -> None:
    menu = load_or_scrape_menu(target, do_scrape)
    outages = load_outages(target, profile)
    if outages:
        print(f"Honoring {len(outages)} ntfy outage(s) for {target.isoformat()}.")
    print(f"Planning {target.isoformat()} from {len(menu.items)} items...")
    plan = generate_plan(menu, profile, target, outages)
    save_plan(plan)
    print(plan_to_markdown(plan))
    print(f"Wrote data/plans/{plan.date}.md")
    if do_notify:
        notify_plan(plan, profile)
        print("Sent ntfy overview + per-meal pings.")


def cmd_notify(target: date, profile: Profile, cancel: bool) -> None:
    if cancel:
        count = cancel_plan_notifications(target, profile)
        print(f"Cancelled {count} ntfy message(s) for {target.isoformat()}.")
        return
    plan = load_saved_plan(target)
    if plan is None:
        raise SystemExit(f"No saved plan for {target.isoformat()}. Run: macro plan")
    notify_plan(plan, profile)
    print("Sent ntfy overview + per-meal pings.")


def _normalize_meal(value: str | None, profile: Profile) -> str:
    if value:
        meal = value.strip().lower()
        if meal not in MEALS:
            raise SystemExit(f"Unknown meal {value!r}; use breakfast, lunch, or dinner")
        return meal
    return infer_meal(profile)


def cmd_out(
    target: date,
    profile: Profile,
    *,
    food: str,
    meal_name: str | None,
    from_ntfy: bool,
    item: str,
    do_scrape: bool,
    do_notify: bool,
) -> None:
    meal = _normalize_meal(meal_name, profile)
    queries: list[str] = []
    if food:
        queries.append(food)
    if item.strip():
        queries.append(item.strip())
    if not queries and not from_ntfy:
        raise SystemExit(
            "macro out needs a food name, --item, or --from-ntfy "
            "(phone New plate polls ntfy outs)."
        )
    for query in queries:
        publish_outage(query, meal, target)
    outages = load_outages(target, profile)
    recorded = {outage.query.lower() for outage in outages}
    for query in queries:
        if query.lower() not in recorded:
            outages.append(Outage(query=query, meal=meal))
    if not outages:
        raise SystemExit(
            "No outages to apply. Publish an ntfy message titled out, or pass a food name."
        )
    menu = load_or_scrape_menu(target, do_scrape)
    existing = load_saved_plan(target)
    print(
        f"Replanning {meal} for {target.isoformat()} "
        f"({len(outages)} outage(s), {len(menu.items)} menu items)..."
    )
    if existing is None:
        print("No saved plan; generating a full day with outages excluded.")
        plan = generate_plan(menu, profile, target, outages)
    else:
        plan = generate_meal_plan(menu, profile, target, meal, existing, outages)
    save_plan(plan)
    print(plan_to_markdown(plan))
    print(f"Wrote data/plans/{plan.date}.md")
    if do_notify:
        notify_meal(plan, profile, meal, immediate=True)
        print(f"Sent replacement {meal} ping.")


def main() -> None:
    load_env()
    profile = load_profile()
    parser = argparse.ArgumentParser(prog="macro", description="ISR daily macro planner")
    parser.add_argument(
        "cmd",
        nargs="?",
        default="plan",
        choices=["scrape", "plan", "notify", "out"],
        help="scrape, plan (default), notify, or out",
    )
    parser.add_argument(
        "food",
        nargs="*",
        help="For out: food name (e.g. cottage cheese)",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="today, tomorrow, or YYYY-MM-DD (default: tomorrow; today for out)",
    )
    parser.add_argument(
        "--no-scrape",
        action="store_true",
        help="Reuse data/menus/<date>.json instead of hitting EatSmart",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Skip ntfy.sh",
    )
    parser.add_argument(
        "--cancel",
        action="store_true",
        help="With notify: drop queued ntfy pings for --date instead of sending",
    )
    parser.add_argument(
        "--meal",
        default=None,
        help="With out: breakfast, lunch, or dinner (default: infer from clock)",
    )
    parser.add_argument(
        "--item",
        default="",
        help="With out: food name from a GitHub Action payload",
    )
    parser.add_argument(
        "--from-ntfy",
        action="store_true",
        help="With out: include today's ntfy out messages (phone New plate path)",
    )
    args = parser.parse_args()
    date_value = args.date
    if date_value is None:
        date_value = "today" if args.cmd == "out" else "tomorrow"
    target = resolve_date(date_value, profile.timezone)

    if args.cmd == "scrape":
        cmd_scrape(target)
        return
    if args.cmd == "notify":
        cmd_notify(target, profile, cancel=args.cancel)
        return
    if args.cmd == "out":
        cmd_out(
            target,
            profile,
            food=" ".join(args.food).strip(),
            meal_name=args.meal,
            from_ntfy=args.from_ntfy,
            item=args.item,
            do_scrape=not args.no_scrape,
            do_notify=not args.no_notify,
        )
        return
    cmd_plan(
        target,
        profile,
        do_scrape=not args.no_scrape,
        do_notify=not args.no_notify,
    )


if __name__ == "__main__":
    main()
