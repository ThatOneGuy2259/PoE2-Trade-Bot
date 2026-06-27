import pytest
from poe2bot.config import Settings

def test_from_env_minimal():
    s = Settings.from_env({"DISCORD_TOKEN": "t", "ALERT_CHANNEL_ID": "42"})
    assert s.discord_token == "t" and s.alert_channel_id == 42
    assert s.poll_interval_min == 30  # default

def test_alert_channel_optional():
    # only the token is required; the alert channel can be set later via /setchannel
    s = Settings.from_env({"DISCORD_TOKEN": "t"})
    assert s.discord_token == "t" and s.alert_channel_id is None

def test_from_env_missing_required():
    with pytest.raises(ValueError) as e:
        Settings.from_env({})
    assert "DISCORD_TOKEN" in str(e.value)
    assert "ALERT_CHANNEL_ID" not in str(e.value)   # no longer required
