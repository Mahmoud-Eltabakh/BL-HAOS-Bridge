"""Pydantic Data Models for Bluetooth Adapters and Devices."""

from enum import Enum

from pydantic import BaseModel, Field


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
    interface: str = "hci0"
    address: str
    name: str = "Bluetooth Adapter"
    alias: str = "Bluetooth Adapter"
    powered: bool = True
    discoverable: bool = False
    discovering: bool = False
    pairable: bool = True
    class_of_device: int | None = None
    uuids: list[str] = Field(default_factory=list)


class DeviceInfo(BaseModel):
    path: str
    adapter_path: str
    adapter_name: str = "hci0"
    address: str
    name: str | None = None
    alias: str = "Bluetooth Device"
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
    device_type: str = "Unknown"
    battery_percentage: int | None = None
    last_seen: float | None = None
