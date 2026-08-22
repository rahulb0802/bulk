# ISR Daily Macro Planner

Personal tool that scrapes tomorrow's [ISR EatSmart](https://eatsmart.housing.illinois.edu/) menu, builds an ovo-lacto vegetarian breakfast/lunch/dinner plan targeting **≥135g protein** from dining-hall food and **2200–2400 kcal**, then sends the plate to your phone via [ntfy.sh](https://ntfy.sh).

One protein shake is excluded. Macros are recomputed in Python from official nutrition labels so the model cannot invent numbers. Calories are also split per meal (roughly 550–850 breakfast, 650–950 lunch/dinner) so the day is not one huge lunch and a tiny dinner.

## Setup

```bash
cp .env.example .env
# put your Gemini API key and a secret ntfy topic in .env
uv sync
```

1. Get a Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey).
2. Install the [ntfy app](https://ntfy.sh/), subscribe to the same topic you put in `.env` (`NTFY_TOPIC` should be long and unguessable).
3. Edit `config/profile.yaml`. Only add `config/staples.yaml` items after you
   have seen them at ISR.

## Usage

```bash
# scrape + plan + ntfy for tomorrow (America/Chicago)
uv run macro plan

uv run macro scrape --date today
uv run macro plan --date 2026-08-20 --no-notify
uv run macro notify --date 2026-08-20
# drop queued meal pings for that date (including ones sent without sequence ids)
uv run macro notify --cancel --date tomorrow

# laptop: food is gone at the hall (default date is today)
uv run macro out "cottage cheese"
uv run macro out eggs --meal lunch
```

Later `plan` / `notify` publishes reuse `isr-YYYY-MM-DD-{overview,breakfast,lunch,dinner}` so a re-plan replaces the queue instead of stacking.

Plans are written to `data/plans/YYYY-MM-DD.md`.

## Food is out (phone)

The hall interface is ntfy. You do not need a laptop at ISR.

1. **Planned item is gone:** on the meal ping, tap **Out …** for that food. That records the outage and starts a Gemini replan of **that meal**. A high-priority replacement plate arrives in about 1–2 minutes.
2. **Something else is gone:** in the ntfy app, publish a message with title `out` and the food name (`tofu`, or `lunch: eggs`). Then tap **New plate** on the meal ping.

ntfy only shows three action buttons. The two highest-protein items on the plate get **Out**; the third button is always **New plate**. Remaining items use compose + **New plate**.

Optional iOS/Android Shortcut: dictate a food name, then `POST` `https://api.github.com/repos/<you>/bulk/dispatches` with header `Authorization: Bearer <FOOD_OUT_DISPATCH_TOKEN>` and JSON `{"event_type":"food-out","client_payload":{"item":"<dictated>","meal":"","date":"today"}}`.

Nightly GitHub Action (8:00pm CDT): add repository secrets `GEMINI_API_KEY`, `NTFY_TOPIC`, and `FOOD_OUT_DISPATCH_TOKEN` (a fine-grained PAT that can **only** dispatch workflows on this repo), then enable Actions. Each nightly run uploads `data/plans/*.md` as the `isr-plan` artifact (Actions → run → Artifacts).

`FOOD_OUT_DISPATCH_TOKEN` is embedded in meal-ping action buttons so one tap can start a replan. Use an unguessable `NTFY_TOPIC` and rotate the token if the topic leaks. The hall workflow is `Hall food out` (`repository_dispatch` event `food-out`).

The planner uses Gemini 3.6 Flash, and falls back to 3.5 Flash if 3.6 is unavailable.

This is for personal meal planning only. Be polite to EatSmart (the scraper already rate-limits and caches labels).
