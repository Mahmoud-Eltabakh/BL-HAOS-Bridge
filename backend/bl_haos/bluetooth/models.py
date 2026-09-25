"""Pydantic Data Models for Bluetooth Adapters and Devices."""

from enum import Enum

from pydantic import BaseModel, Field

from ..constants import (
    ADAPTER_NAME_FALLBACK,
    BLUETOOTH_ADAPTER_LABEL,
    BLUETOOTH_DEVICE_LABEL,
    UNKNOWN_DEVICE_TYPE,
)


class PairingState(str, Enum):
    IDLE = "idle"
    PAIRING = "pairing"
    PAIRED = "paired"
    FAILED = "failed"


class AudioProfileType(str, Enum):
    A2DP_SINK = "a2dp_sink"
    A2DP_SOURCE = "a2dp_source"
    AVRCP = "avrcp"
    OTHER = "other"


class AdapterInfo(BaseModel):
    path: str
    interface: str = ADAPTER_NAME_FALLBACK
    address: str
    name: str = BLUETOOTH_ADAPTER_LABEL
    alias: str = BLUETOOTH_ADAPTER_LABEL
    powered: bool = True
    discoverable: bool = False
    discovering: bool = False
    pairable: bool = True
    class_of_device: int | None = None
    uuids: list[str] = Field(default_factory=list)


class DeviceInfo(BaseModel):
    path: str
    adapter_path: str
    adapter_name: str = ADAPTER_NAME_FALLBACK
    address: str
    name: str | None = None
    alias: str = BLUETOOTH_DEVICE_LABEL
    icon: str | None = None
    paired: bool = False
    trusted: bool = False
    connected: bool = False
    blocked: bool = False
    legacy_pairing: bool = False
    rssi: int | None = None
    tx_power: int | None = None
    class_of_device: int | None = None
    uuids: list[str] = Field(default_factory=list)
    is_audio_sink: bool = False
    device_type: str = UNKNOWN_DEVICE_TYPE
    battery_percentage: int | None = None
    last_seen: float | None = None
