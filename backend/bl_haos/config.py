"""Persistent Configuration Store for BL-HAOS."""

import json
import logging
import os
import secrets
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .constants import (
    CONFIG_DIRECTORY,
    CONFIG_FILE_MODE,
    CONFIG_TEMP_SUFFIX,
    DEFAULT_CODEC,
    DEFAULT_CONFIG_PATH,
    DEFAULT_DEMO_SCENARIO,
    DEFAULT_LOG_LEVEL,
    DEFAULT_VOLUME_PERCENT,
    DEFAULT_VOLUME_RATIO,
    ENV_DEMO_MODE,
    ENV_DEMO_SCENARIO,
    ENV_NATIVE_TOKEN,
    ENV_PLAYBACK_BUFFER_MS,
    FALLBACK_CONFIG_PATH,
    MAX_ALIAS_LENGTH,
    PLAYBACK_BUFFER_DEFAULT_MS,
    PLAYBACK_BUFFER_MAX_MS,
    PLAYBACK_BUFFER_MIN_MS,
    SUPPORTED_CODECS,
    TOKEN_ENTROPY_BYTES,
    TRUTHY_FLAGS,
    VOLUME_MAX_PERCENT,
    VOLUME_MAX_RATIO,
    VOLUME_MIN_PERCENT,
    VOLUME_MIN_RATIO,
    VOLUME_READBACK_TOLERANCE,
)

logger = logging.getLogger("bl_haos.config")

_MISSING = object()


class SpeakerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str
    custom_alias: str | None = None
    auto_reconnect: bool = True
    preferred_adapter: str | None = None
    default_volume: int = Field(default=DEFAULT_VOLUME_PERCENT, ge=VOLUME_MIN_PERCENT, le=VOLUME_MAX_PERCENT)
    codec_override: str | None = None

    @field_validator("custom_alias")
    @classmethod
    def valid_alias(cls, value: str | None) -> str | None:
        if value is not None and (len(value) > MAX_ALIAS_LENGTH or any(ord(char) < 32 for char in value)):
            raise ValueError("Speaker alias is invalid")
        return value

    @field_validator("codec_override")
    @classmethod
    def valid_codec(cls, value: str | None) -> str | None:
        if value is not None and value not in SUPPORTED_CODECS:
            raise ValueError("Codec override is invalid")
        return value


class PlayerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pulse_socket: str = "/run/audio/pulse.sock"
    sink_probe_timeout: float = Field(default=5, gt=0)
    stop_grace_seconds: float = Field(default=2, gt=0)
    kill_grace_seconds: float = Field(default=1.5, gt=0)
    command_stop_budget_seconds: float = Field(default=0.25, gt=0)
    # Audio held ahead of the speaker. A Bluetooth link delivers in bursts and a
    # speaker drains its own buffer at a fixed rate, so a client that holds only
    # its default keeps running dry and the result is audible stutter; too much
    # and audio keeps playing after a pause. See PLAYBACK_BUFFER_* in constants.
    latency_msec: int = Field(
        default=PLAYBACK_BUFFER_DEFAULT_MS, ge=PLAYBACK_BUFFER_MIN_MS, le=PLAYBACK_BUFFER_MAX_MS
    )
    sample_rate_hz: int = Field(default=48000, gt=0)
    channels: int = Field(default=2, gt=0)
    default_volume: float = Field(default=DEFAULT_VOLUME_RATIO, ge=VOLUME_MIN_RATIO, le=VOLUME_MAX_RATIO)
    keepalive_interval_seconds: float = Field(default=240, gt=0)
    keepalive_pulse_duration_seconds: float = Field(default=1, gt=0)
    pipe_chunk_size: int = Field(default=64 * 1024, gt=0)
    # How often the real sink volume is read back so a level changed on the
    # speaker itself reaches Home Assistant and the dashboard. Cheap enough to
    # run often (one read-only client call per connected speaker).
    volume_poll_seconds: float = Field(default=30, gt=0)
    volume_readback_tolerance: float = Field(default=VOLUME_READBACK_TOLERANCE, ge=0, le=1)


class SystemSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_level: str = DEFAULT_LOG_LEVEL
    default_codec: str = DEFAULT_CODEC
    auto_reconnect_enabled: bool = True
    native_token: str = Field(default="", exclude=True, repr=False)
    demo_mode: bool = False
    demo_scenario: str = DEFAULT_DEMO_SCENARIO
    player: PlayerSettings = Field(default_factory=PlayerSettings)
    speakers: dict[str, SpeakerSettings] = Field(default_factory=dict)


# Settings that older releases persisted but this build no longer models. They
# are dropped before validation so an in-place upgrade keeps the rest of the
# stored settings instead of failing to load and silently reverting to defaults.
LEGACY_ROOT_KEYS = ("multiroom_sync_enabled",)
LEGACY_SPEAKER_KEYS = ("latency_offset_ms",)


def _drop_legacy_keys(data: dict) -> dict:
    """Strip settings that a previous release wrote but this build no longer models."""
    dropped: list[str] = [key for key in LEGACY_ROOT_KEYS if data.pop(key, _MISSING) is not _MISSING]

    speakers = data.get("speakers")
    if isinstance(speakers, dict):
        for address, entry in speakers.items():
            if isinstance(entry, dict):
                for key in LEGACY_SPEAKER_KEYS:
                    if entry.pop(key, _MISSING) is not _MISSING:
                        dropped.append(f"speakers.{address}.{key}")

    if dropped:
        logger.info("Ignoring %d setting(s) removed from this release: %s", len(dropped), ", ".join(dropped))
    return data


class ConfigStore:
    def __init__(self, config_file: str | None = None):
        if config_file:
            self.file_path = Path(config_file)
        elif Path(CONFIG_DIRECTORY).exists() and os.access(CONFIG_DIRECTORY, os.W_OK):
            self.file_path = Path(DEFAULT_CONFIG_PATH)
        else:
            self.file_path = Path(FALLBACK_CONFIG_PATH)

        self.settings: SystemSettings = SystemSettings()
        self.load()

    def load(self) -> SystemSettings:
        """Load settings from JSON file."""
        if self.file_path.exists():
            try:
                data = json.loads(self.file_path.read_text(encoding="utf-8"))
                self.settings = SystemSettings.model_validate(_drop_legacy_keys(data))
                self.settings.demo_mode = self._configured_demo_mode()
                self.settings.demo_scenario = os.environ.get(ENV_DEMO_SCENARIO, self.settings.demo_scenario)
                if not self.settings.native_token or any(ord(char) < 32 for char in self.settings.native_token):
                    self.settings.native_token = self._configured_token()
                    self.save()
                logger.info("Loaded configuration from %s", self.file_path)
            except Exception as e:
                logger.error("Failed to load config from %s: invalid configuration. Using defaults.", self.file_path)
                self.settings = SystemSettings(native_token=self._configured_token())
        else:
            self.settings = SystemSettings(
                native_token=self._configured_token(),
                demo_mode=self._configured_demo_mode(),
                demo_scenario=self._configured_demo_scenario(),
            )
            self.save()
        # Deployment-level overrides that are not persisted settings: they belong
        # to the container, so they are re-applied on every load instead of being
        # written to the settings file.
        self.settings.player.latency_msec = self._configured_playback_buffer_ms()
        return self.settings

    @staticmethod
    def _configured_token() -> str:
        configured = os.environ.get(ENV_NATIVE_TOKEN, "")
        if configured and not any(ord(char) < 32 for char in configured):
            return configured
        return secrets.token_urlsafe(TOKEN_ENTROPY_BYTES)

    @staticmethod
    def _configured_demo_mode() -> bool:
        return os.environ.get(ENV_DEMO_MODE, "").strip().lower() in TRUTHY_FLAGS

    @staticmethod
    def _configured_demo_scenario() -> str:
        return os.environ.get(ENV_DEMO_SCENARIO, DEFAULT_DEMO_SCENARIO).strip() or DEFAULT_DEMO_SCENARIO

    @staticmethod
    def _configured_playback_buffer_ms() -> int:
        """The buffer the audio clients hold, in milliseconds.

        A speaker whose link needs more slack than the default (a stuttering
        LDAC link, a busy host) can be given more without a settings write, and a
        value outside the supported range is ignored rather than trusted: an
        unbounded buffer would turn a pause into minutes of queued audio.
        """
        configured = os.environ.get(ENV_PLAYBACK_BUFFER_MS, "").strip()
        if not configured:
            return PLAYBACK_BUFFER_DEFAULT_MS
        try:
            value = int(configured)
        except ValueError:
            logger.warning(
                "Ignoring %s=%s: not a whole number of milliseconds", ENV_PLAYBACK_BUFFER_MS, configured
            )
            return PLAYBACK_BUFFER_DEFAULT_MS
        if not PLAYBACK_BUFFER_MIN_MS <= value <= PLAYBACK_BUFFER_MAX_MS:
            logger.warning(
                "Ignoring %s=%s: outside %d-%d ms",
                ENV_PLAYBACK_BUFFER_MS,
                configured,
                PLAYBACK_BUFFER_MIN_MS,
                PLAYBACK_BUFFER_MAX_MS,
            )
            return PLAYBACK_BUFFER_DEFAULT_MS
        return value

    def save(self) -> None:
        """Atomically persist settings to JSON file."""
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.file_path.with_suffix(CONFIG_TEMP_SUFFIX)
            data = self.settings.model_dump(mode="json")
            data["native_token"] = self.settings.native_token
            temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            temp_path.chmod(CONFIG_FILE_MODE)
            temp_path.replace(self.file_path)
            self.file_path.chmod(CONFIG_FILE_MODE)
            logger.info("Saved configuration to %s", self.file_path)
        except Exception as e:
            logger.error("Failed to save config to %s", self.file_path)

    def get_speaker(self, address: str) -> SpeakerSettings | None:
        """Look up a speaker without creating or persisting anything."""
        return self.settings.speakers.get(address.strip().lower())

    def get_or_create_speaker(self, address: str) -> SpeakerSettings:
        """Create and persist a speaker record only for explicit configuration writes."""
        addr = address.strip().lower()
        if addr not in self.settings.speakers:
            self.settings.speakers[addr] = SpeakerSettings(address=addr)
            self.save()
        return self.settings.speakers[addr]

    def update_speaker(self, address: str, **kwargs) -> SpeakerSettings:
        speaker = self.get_or_create_speaker(address)
        updates = {key: value for key, value in kwargs.items() if value is not None}
        self.settings.speakers[speaker.address] = SpeakerSettings.model_validate(
            speaker.model_dump() | updates
        )
        self.save()
        return self.settings.speakers[speaker.address]

    def remove_speaker(self, address: str) -> bool:
        addr = address.strip().lower()
        if addr in self.settings.speakers:
            del self.settings.speakers[addr]
            self.save()
            return True
        return False
