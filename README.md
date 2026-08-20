# ISR Daily Macro Planner

Personal tool that scrapes tomorrow's [ISR EatSmart](https://eatsmart.housing.illinois.edu/) menu, builds an ovo-lacto vegetarian breakfast/lunch/dinner plan targeting **≥110g protein** and **2200–2400 kcal**, then sends the plate to your phone via [ntfy.sh](https://ntfy.sh).

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
```

Plans are written to `data/plans/YYYY-MM-DD.md`.

Nightly GitHub Action (8:00pm CDT): add repository secrets `GEMINI_API_KEY` and `NTFY_TOPIC`, then enable Actions.

The planner uses Gemini 3.6 Flash, and falls back to 3.5 Flash if 3.6 is unavailable.

This is for personal meal planning only. Be polite to EatSmart (the scraper already rate-limits and caches labels).
