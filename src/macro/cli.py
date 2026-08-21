from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from macro.models import DayPlan, Profile
from macro.notify import cancel_plan_notifications, notify_plan, plan_to_markdown
from macro.plan import generate_plan
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


def cmd_scrape(target: date) -> None:
    print(f"Scraping ISR EatSmart menu for {target.isoformat()}...")
    menu = scrape_isr(target)
    save_menu(menu)
    print(
        f"Saved {len(menu.items)} vegetarian items with nutrition "
        f"-> data/menus/{target.isoformat()}.json"
    )


def cmd_plan(target: date, profile: Profile, do_scrape: bool, do_notify: bool) -> None:
    menu = None if do_scrape else load_menu(target)
    if menu is None:
        cmd_scrape(target)
        menu = load_menu(target)
    if menu is None:
        raise SystemExit("Scrape produced no menu file")
    print(f"Planning {target.isoformat()} from {len(menu.items)} items...")
    plan = generate_plan(menu, profile, target)
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


def main() -> None:
    load_env()
    profile = load_profile()
    parser = argparse.ArgumentParser(prog="macro", description="ISR daily macro planner")
    parser.add_argument(
        "cmd",
        nargs="?",
        default="plan",
        choices=["scrape", "plan", "notify"],
        help="scrape, plan (default), or notify",
    )
    parser.add_argument(
        "--date",
        default="tomorrow",
        help="today, tomorrow, or YYYY-MM-DD (default: tomorrow)",
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
    args = parser.parse_args()
    target = resolve_date(args.date, profile.timezone)

    if args.cmd == "scrape":
        cmd_scrape(target)
        return
    if args.cmd == "notify":
        cmd_notify(target, profile, cancel=args.cancel)
        return
    cmd_plan(
        target,
        profile,
        do_scrape=not args.no_scrape,
        do_notify=not args.no_notify,
    )


if __name__ == "__main__":
    main()
