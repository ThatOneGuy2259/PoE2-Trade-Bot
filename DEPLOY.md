# Deploying PoE2 Trade Bot (Docker, Debian/Ubuntu x86_64)

You run these on the **server** (SSH in over Tailscale). The bot makes only outbound
connections (Discord + poe2scout), so it needs no inbound ports and does not depend on
Tailscale to run — Tailscale is just how you reach the box.

> **Never paste your bot token into a chat or commit it.** It lives only in the env file
> created in step 4, with `chmod 600`.

---

## 0. One-time Discord setup (in a browser)

1. **Invite the bot to your server.** In the Developer Portal, copy your application's
   **Client ID** (General Information → Application ID — this is *not* secret), then open:
   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&scope=bot+applications.commands&permissions=19456
   ```
   `permissions=19456` = View Channel + Send Messages + Embed Links. Pick your server,
   authorize. (No privileged intents are required — the bot never reads message content.)
2. **Get the channel IDs.** Discord → User Settings → Advanced → enable **Developer Mode**.
   Right-click your alerts channel → **Copy Channel ID**. Do the same for a health channel
   if you want one (optional). Channel IDs are not secret.

---

## 1. Install Docker (skip if already installed)

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"   # then log out/in so 'docker' works without sudo
```

## 2. Get the code

```bash
git clone https://github.com/ThatOneGuy2259/PoE2-Trade-Bot.git
cd PoE2-Trade-Bot
```

## 3. Create the env file (this is where your details go)

```bash
cp .env.example poe2bot.env
chmod 600 poe2bot.env          # readable only by you
nano poe2bot.env               # fill in the values below
```
Set at minimum:
```ini
DISCORD_TOKEN=<paste your bot token here, on the server only>
ALERT_CHANNEL_ID=<your alerts channel ID>
HEALTH_CHANNEL_ID=<optional health channel ID, or leave blank>
POLL_INTERVAL_MIN=30
POE2SCOUT_UA=poe2bot/0.1 (contact: you@example.com)
DB_PATH=/data/poe2bot.db
```
(Leave `DB_PATH=/data/poe2bot.db` — that's the path inside the container, backed by the
volume in step 5.)

## 4. Verify the data path before going live (no token needed)

```bash
docker build -t poe2bot .
docker run --rm --env-file poe2bot.env poe2bot python -m poe2bot.main --once
```
You should see ~38 currency items for the current league with Exalted/Divine/Chaos prices
and `alerts fired: 0` (expected on a cold ledger). If that prints, fetch → normalize →
detect all work against the live API.

## 5. Run it for real (persistent + auto-restart)

```bash
docker volume create poe2bot-data
docker run -d --name poe2bot \
  --env-file poe2bot.env \
  -v poe2bot-data:/data \
  --restart unless-stopped \
  poe2bot
docker logs -f poe2bot        # watch startup; Ctrl-C to stop watching (bot keeps running)
```
The named volume `poe2bot-data` keeps your SQLite ledger across restarts/updates.

## 6. Verify in Discord

1. Run `/setleague` — it autocompletes from the live league list; pick the current league.
2. `/status` — shows league, last poll, alert cap.
3. `/price divine` — shows the price in Exalted / Divine / Chaos.

> First-time note: **global slash commands can take up to ~1 hour to appear** after the
> bot first starts (a Discord propagation limit, not a bug). If you want them instantly,
> ask me to add a `DISCORD_GUILD_ID` option that syncs commands to your server immediately.

Alerts begin once the ledger has built a baseline (a few hours of polling).

---

## Operations

```bash
docker logs -f poe2bot                       # follow logs
docker restart poe2bot                        # restart
docker stop poe2bot && docker rm poe2bot      # stop + remove (volume/data preserved)

# Update to the latest code:
cd PoE2-Trade-Bot && git pull
docker build -t poe2bot .
docker stop poe2bot && docker rm poe2bot
docker run -d --name poe2bot --env-file poe2bot.env -v poe2bot-data:/data \
  --restart unless-stopped poe2bot
```

## Notes

- **Secrets:** `poe2bot.env` is git-ignored and `chmod 600`. Never commit it; never paste
  the token anywhere but this file. To rotate the token, edit the file and restart.
- **Backups:** the ledger lives in the `poe2bot-data` volume. Back it up with
  `docker run --rm -v poe2bot-data:/data -v "$PWD":/backup alpine tar czf /backup/poe2bot-data.tgz -C /data .`
- **Resource use:** tiny — one Python process, a few HTTP requests every 30 min, a small
  SQLite file. Fine on a Pi-class box or the smallest VPS.
