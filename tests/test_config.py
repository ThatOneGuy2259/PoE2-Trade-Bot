import pytest
from poe2bot.config import Settings

def test_from_env_minimal():
    s = Settings.from_env({"DISCORD_TOKEN": "t", "ALERT_CHANNEL_ID": "42"})
    assert s.discord_token == "t" and s.alert_channel_id == 42
    assert s.poll_interval_min == 30  # default

def test_from_env_missing_required():
    with pytest.raises(ValueError) as e:
        Settings.from_env({})
    assert "DISCORD_TOKEN" in str(e.value) and "ALERT_CHANNEL_ID" in str(e.value)
