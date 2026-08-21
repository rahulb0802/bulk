# ISR Daily Macro Planner

Personal tool that scrapes tomorrow's [ISR EatSmart](https://eatsmart.housing.illinois.edu/) menu, builds an ovo-lacto vegetarian breakfast/lunch/dinner plan targeting **≥135g protein** from dining-hall food and **2200–2400 kcal**, then sends the plate to your phone via [ntfy.sh](https://ntfy.sh).

One protein shake is excluded. Macros are recomputed in Python from official nutrition labels so the model cannot invent numbers. Calories are also split per meal (roughly 550–850 breakfast, 650–950 lunch/dinner) so the day is not one huge lunch and a tiny dinner.

Protein from shakes is excluded. Macros are recomputed in Python from official nutrition labels.

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
```

Later `plan` / `notify` publishes reuse `isr-YYYY-MM-DD-{overview,breakfast,lunch,dinner}` so a re-plan replaces the queue instead of stacking.

Plans are written to `data/plans/YYYY-MM-DD.md`.

Nightly GitHub Action (8:00pm CDT): add repository secrets `GEMINI_API_KEY` and `NTFY_TOPIC`, then enable Actions. Each run uploads `data/plans/*.md` as the `isr-plan` artifact (Actions → run → Artifacts).

The planner uses Gemini 3.6 Flash, and falls back to 3.5 Flash if 3.6 is unavailable.

This is for personal meal planning only. Be polite to EatSmart (the scraper already rate-limits and caches labels).
