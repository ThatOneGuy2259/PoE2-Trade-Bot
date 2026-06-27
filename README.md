# PoE2 Trade Bot — Phase 1

Polls poe2scout, alerts on price jumps/crashes/demand collapse.

## Run

1. `cp .env.example .env` and fill DISCORD_TOKEN + ALERT_CHANNEL_ID.
2. `pip install -e ".[dev]" && python -m poe2bot.main`
3. In Discord: `/setleague` (autocompletes live), then wait for polls to accrue.

## Deploy

`docker build -t poe2bot . && docker run -v $PWD/data:/data --env-file .env poe2bot`

or install `deploy/poe2bot.service` with `/etc/poe2bot.env`.
