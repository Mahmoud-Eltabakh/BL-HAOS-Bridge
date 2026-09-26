import json
from pathlib import Path

from backend.bl_haos.config import ConfigStore


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_playback_buffer_is_a_deployment_override(tmp_path, monkeypatch):
    """The buffer the audio clients hold is set by the deployment, not by settings.

    It is re-applied on every load, so a value left behind in the settings file
    never outlives the environment variable that set it, and a value outside the
    supported range is ignored instead of trusted (an unbounded buffer would turn
    a pause into minutes of queued audio).
    """
    from backend.bl_haos.constants import (
        ENV_PLAYBACK_BUFFER_MS,
        PLAYBACK_BUFFER_DEFAULT_MS,
        PLAYBACK_BUFFER_MAX_MS,
    )

    monkeypatch.setenv(ENV_PLAYBACK_BUFFER_MS, "900")
    store = ConfigStore(config_file=str(tmp_path / "settings.json"))
    assert store.settings.player.latency_msec == 900

    monkeypatch.setenv(ENV_PLAYBACK_BUFFER_MS, str(PLAYBACK_BUFFER_MAX_MS + 1))
    store.load()
    assert store.settings.player.latency_msec == PLAYBACK_BUFFER_DEFAULT_MS

    monkeypatch.setenv(ENV_PLAYBACK_BUFFER_MS, "loud")
    store.load()
    assert store.settings.player.latency_msec == PLAYBACK_BUFFER_DEFAULT_MS

    monkeypatch.delenv(ENV_PLAYBACK_BUFFER_MS)
    store.load()
    assert store.settings.player.latency_msec == PLAYBACK_BUFFER_DEFAULT_MS


def test_upgrade_ignores_settings_that_this_release_removed(tmp_path):
    """A config written by a previous release must still load.

    Multi-room was removed, so `multiroom_sync_enabled` and the per-speaker
    `latency_offset_ms` are no longer modelled. Because the models use
    `extra="forbid"`, leaving them in a stored config would raise during load and
    silently reset every alias, volume and adapter pin to its default.
    """
    stored = {
        "multiroom_sync_enabled": True,
        "log_level": "debug",
        "speakers": {
            "aa:bb:cc:11:22:33": {
                "address": "aa:bb:cc:11:22:33",
                "custom_alias": "Kitchen",
                "default_volume": 42,
                "latency_offset_ms": 120,
            }
        },
    }
    path = _write(tmp_path / "config.json", stored)

    store = ConfigStore(config_file=str(path))

    assert store.settings.log_level == "debug"
    speaker = store.settings.speakers["aa:bb:cc:11:22:33"]
    assert speaker.custom_alias == "Kitchen"
    assert speaker.default_volume == 42
    assert not hasattr(store.settings, "multiroom_sync_enabled")
    assert not hasattr(speaker, "latency_offset_ms")


def test_unknown_settings_are_still_rejected(tmp_path):
    """Tolerating old keys must not weaken validation for genuine typos."""
    path = _write(tmp_path / "config.json", {"log_level": "debug", "not_a_real_setting": True})

    store = ConfigStore(config_file=str(path))

    assert store.settings.log_level == "info", "an unknown key must still fall back to defaults"


def test_removed_keys_are_not_written_back(tmp_path):
    path = _write(
        tmp_path / "config.json",
        {
            "multiroom_sync_enabled": True,
            "speakers": {"aa:bb:cc:11:22:33": {"address": "aa:bb:cc:11:22:33", "latency_offset_ms": 5}},
        },
    )

    store = ConfigStore(config_file=str(path))
    store.save()

    saved = path.read_text(encoding="utf-8")
    assert "multiroom_sync_enabled" not in saved
    assert "latency_offset_ms" not in saved
    assert json.loads(saved)["speakers"]["aa:bb:cc:11:22:33"]["address"] == "aa:bb:cc:11:22:33"
