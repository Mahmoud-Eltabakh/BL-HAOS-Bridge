"""Persistent Configuration Store for BL-HAOS."""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field

logger = logging.getLogger("bl_haos.config")

DEFAULT_CONFIG_PATH = "/data/bl_haos_config.json"
FALLBACK_CONFIG_PATH = "/tmp/bl_haos_config.json"


class SpeakerSettings(BaseModel):
    address: str
    custom_alias: Optional[str] = None
    auto_reconnect: bool = True
    preferred_adapter: Optional[str] = None
    default_volume: int = 70
    codec_override: Optional[str] = None


class SystemSettings(BaseModel):
    log_level: str = "info"
    default_codec: str = "auto"
    auto_reconnect_enabled: bool = True
    multiroom_sync_enabled: bool = True
    speakers: Dict[str, SpeakerSettings] = Field(default_factory=dict)


class ConfigStore:
    def __init__(self, config_file: Optional[str] = None):
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
                logger.info("Loaded configuration from %s", self.file_path)
            except Exception as e:
                logger.error("Failed to load config from %s: %s. Using defaults.", self.file_path, e)
                self.settings = SystemSettings()
        else:
            self.settings = SystemSettings()
            self.save()
        return self.settings

    def save(self) -> None:
        """Atomically persist settings to JSON file."""
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.file_path.with_suffix(".tmp")
            data = self.settings.model_dump(mode="json")
            temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            temp_path.replace(self.file_path)
            logger.info("Saved configuration to %s", self.file_path)
        except Exception as e:
            logger.error("Failed to save config to %s: %s", self.file_path, e)

    def get_speaker(self, address: str) -> SpeakerSettings:
        addr = address.strip().lower()
        if addr not in self.settings.speakers:
            self.settings.speakers[addr] = SpeakerSettings(address=addr)
            self.save()
        return self.settings.speakers[addr]

    def update_speaker(self, address: str, **kwargs) -> SpeakerSettings:
        speaker = self.get_speaker(address)
        for k, v in kwargs.items():
            if hasattr(speaker, k) and v is not None:
                setattr(speaker, k, v)
        self.save()
        return speaker
