# PoE2 Trade Bot — Phase 1

Polls poe2scout, alerts on price jumps/crashes/demand collapse.

## Run

1. `cp .env.example .env` and fill DISCORD_TOKEN + ALERT_CHANNEL_ID.
2. `pip install -e ".[dev]" && python -m poe2bot.main`
3. In Discord: `/setleague` (autocompletes live), then wait for polls to accrue.

## Try it without a Discord token

`python -m poe2bot.main --once` does a single live poll against poe2scout and prints the
result (and any alerts) to stdout — no credentials needed. It auto-selects the current
league and proves the fetch → normalize → store → detect → alert pipeline end to end. On
a cold ledger it fires nothing (the detector needs a stored baseline first).

> macOS note: a python.org interpreter may lack CA certificates. If `--once` fails TLS
> verification, run `pip install certifi` and `export SSL_CERT_FILE="$(python -m certifi)"`
> (Docker/Linux already have system certs).

## Deploy

`docker build -t poe2bot . && docker run -v $PWD/data:/data --env-file .env poe2bot`

or install `deploy/poe2bot.service` with `/etc/poe2bot.env`.
