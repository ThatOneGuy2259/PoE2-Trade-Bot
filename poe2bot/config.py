from __future__ import annotations
from dataclasses import dataclass
from collections.abc import Mapping


@dataclass(frozen=True)
class Settings:
    discord_token: str
    alert_channel_id: int
    health_channel_id: int | None
    db_path: str
    poll_interval_min: int
    poe2scout_ua: str
    dead_man_url: str | None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        missing = [k for k in ("DISCORD_TOKEN", "ALERT_CHANNEL_ID") if not env.get(k)]
        if missing:
            raise ValueError(f"missing required env vars: {', '.join(missing)}")
        health = env.get("HEALTH_CHANNEL_ID")
        return cls(
            discord_token=env["DISCORD_TOKEN"],
            alert_channel_id=int(env["ALERT_CHANNEL_ID"]),
            health_channel_id=int(health) if health else None,
            db_path=env.get("DB_PATH", "./poe2bot.db"),
            poll_interval_min=int(env.get("POLL_INTERVAL_MIN", "30")),
            poe2scout_ua=env.get("POE2SCOUT_UA", "poe2bot/0.1 (contact: unset)"),
            dead_man_url=env.get("DEAD_MAN_URL") or None,
        )
