"""Persistent Configuration Store for BL-HAOS."""

import json
import logging
import os
import secrets
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger("bl_haos.config")

DEFAULT_CONFIG_PATH = "/data/bl_haos_config.json"
FALLBACK_CONFIG_PATH = "/tmp/bl_haos_config.json"


class SpeakerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str
    custom_alias: str | None = None
    auto_reconnect: bool = True
    preferred_adapter: str | None = None
    default_volume: int = Field(default=70, ge=0, le=100)
    codec_override: str | None = None
    latency_offset_ms: int = Field(default=0, ge=-5000, le=5000)

    @field_validator("custom_alias")
    @classmethod
    def valid_alias(cls, value: str | None) -> str | None:
        if value is not None and (len(value) > 128 or any(ord(char) < 32 for char in value)):
            raise ValueError("Speaker alias is invalid")
        return value

    @field_validator("codec_override")
    @classmethod
    def valid_codec(cls, value: str | None) -> str | None:
        if value is not None and value not in {"auto", "sbc", "sbc_xq", "aac", "aptx", "aptx_hd", "ldac"}:
            raise ValueError("Codec override is invalid")
        return value


class SystemSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_level: str = "info"
    default_codec: str = "auto"
    auto_reconnect_enabled: bool = True
    multiroom_sync_enabled: bool = True
    native_token: str = Field(default="", exclude=True, repr=False)
    demo_mode: bool = False
    demo_scenario: str = "healthy"
    speakers: dict[str, SpeakerSettings] = Field(default_factory=dict)


class ConfigStore:
    def __init__(self, config_file: str | None = None):
        if config_file:
            self.file_path = Path(config_file)
        elif Path("/data").exists() and os.access("/data", os.W_OK):
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
                self.settings = SystemSettings.model_validate(data)
                self.settings.demo_mode = self._configured_demo_mode()
                self.settings.demo_scenario = os.environ.get("BLHAOS_DEMO_SCENARIO", self.settings.demo_scenario)
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
        return self.settings

    @staticmethod
    def _configured_token() -> str:
        configured = os.environ.get("BLHAOS_NATIVE_TOKEN", "")
        if configured and not any(ord(char) < 32 for char in configured):
            return configured
        return secrets.token_urlsafe(32)

    @staticmethod
    def _configured_demo_mode() -> bool:
        return os.environ.get("BLHAOS_DEMO_MODE", "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _configured_demo_scenario() -> str:
        return os.environ.get("BLHAOS_DEMO_SCENARIO", "healthy").strip() or "healthy"

    def save(self) -> None:
        """Atomically persist settings to JSON file."""
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.file_path.with_suffix(".tmp")
            data = self.settings.model_dump(mode="json")
            data["native_token"] = self.settings.native_token
            temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            temp_path.chmod(0o600)
            temp_path.replace(self.file_path)
            self.file_path.chmod(0o600)
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
