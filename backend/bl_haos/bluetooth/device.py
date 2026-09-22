"""Bluetooth Device Controller wrapping org.bluez.Device1."""

import time
import logging
from typing import Dict, Any, Optional, List
from dbus_fast.aio import MessageBus
from dbus_fast import Variant

from .constants import (
    BLUEZ_SERVICE,
    DEVICE_INTERFACE,
    DBUS_PROPERTIES_IFACE,
    AUDIO_SINK_UUIDS,
    MAJOR_DEVICE_CLASS_AUDIO_VIDEO,
    MINOR_DEVICE_CLASSES_AUDIO,
)
from .models import DeviceInfo

logger = logging.getLogger("bl_haos.bluetooth.device")


class BluetoothDevice:
    def __init__(self, bus: Optional[MessageBus], path: str, properties: Dict[str, Any]):
        self.bus = bus
        self.path = path
        self._properties = properties
        self.last_seen = time.time()

    def _get_prop(self, key: str, default: Any = None) -> Any:
        val = self._properties.get(key, default)
        if isinstance(val, Variant):
            return val.value
        return val

    @property
    def address(self) -> str:
        return self._get_prop("Address", "")

    @property
    def name(self) -> Optional[str]:
        return self._get_prop("Name")

    @property
    def alias(self) -> str:
        return self._get_prop("Alias", self.name or self.address)

    @property
    def adapter_path(self) -> str:
        return self._get_prop("Adapter", "")

    @property
    def adapter_name(self) -> str:
        return self.adapter_path.split("/")[-1] if self.adapter_path else "hci0"

    @property
    def paired(self) -> bool:
        return bool(self._get_prop("Paired", False))

    @property
    def trusted(self) -> bool:
        return bool(self._get_prop("Trusted", False))

    @property
    def connected(self) -> bool:
        return bool(self._get_prop("Connected", False))

    @property
    def blocked(self) -> bool:
        return bool(self._get_prop("Blocked", False))

    @property
    def rssi(self) -> Optional[int]:
        return self._get_prop("RSSI")

    @property
    def class_of_device(self) -> Optional[int]:
        return self._get_prop("Class")

    @property
    def uuids(self) -> List[str]:
        return [str(u).lower() for u in self._get_prop("UUIDs", [])]

    @property
    def is_audio_sink(self) -> bool:
        """Determine if this device functions as a Bluetooth audio sink (speaker/headphone)."""
        # Check UUIDs
        for uuid in self.uuids:
            if uuid in AUDIO_SINK_UUIDS:
                return True

        # Check Class of Device
        if self.class_of_device is not None:
            major = self.class_of_device & 0x1F00
            if major == MAJOR_DEVICE_CLASS_AUDIO_VIDEO:
                return True

        # Check icon hint
        icon = self._get_prop("Icon", "")
        if icon in ("audio-card", "audio-speakers", "audio-headphones", "audio-headset"):
            return True

        return False

    @property
    def device_type(self) -> str:
        """Human-readable device classification."""
        if self.class_of_device is not None:
            minor = self.class_of_device & 0x1FFC
            if minor in MINOR_DEVICE_CLASSES_AUDIO:
                return MINOR_DEVICE_CLASSES_AUDIO[minor]
            if (self.class_of_device & 0x1F00) == MAJOR_DEVICE_CLASS_AUDIO_VIDEO:
                return "Audio Device"

        icon = self._get_prop("Icon", "")
        if "speaker" in icon:
            return "Speaker"
        if "headphone" in icon:
            return "Headphones"
        if "headset" in icon:
            return "Headset"

        return "Speaker" if self.is_audio_sink else "Bluetooth Device"

    def update_properties(self, changed: Dict[str, Any]):
        """Update properties and timestamp from PropertiesChanged signal."""
        for k, v in changed.items():
            self._properties[k] = v.value if isinstance(v, Variant) else v
        self.last_seen = time.time()

    def to_info(self) -> DeviceInfo:
        return DeviceInfo(
            path=self.path,
            adapter_path=self.adapter_path,
            adapter_name=self.adapter_name,
            address=self.address,
            name=self.name,
            alias=self.alias,
            icon=self._get_prop("Icon"),
            paired=self.paired,
            trusted=self.trusted,
            connected=self.connected,
            blocked=self.blocked,
            legacy_pairing=bool(self._get_prop("LegacyPairing", False)),
            rssi=self.rssi,
            tx_power=self._get_prop("TxPower"),
            class_of_device=self.class_of_device,
            uuids=self.uuids,
            is_audio_sink=self.is_audio_sink,
            device_type=self.device_type,
            battery_percentage=self._get_prop("Percentage"),
            last_seen=self.last_seen,
        )

    async def connect(self) -> None:
        """Connect to device."""
        if not self.bus:
            self._properties["Connected"] = True
            return
        introspection = await self.bus.introspect(BLUEZ_SERVICE, self.path)
        proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, self.path, introspection)
        dev_iface = proxy.get_interface(DEVICE_INTERFACE)
        await dev_iface.call_connect()
        self._properties["Connected"] = True

    async def disconnect(self) -> None:
        """Disconnect from device."""
        if not self.bus:
            self._properties["Connected"] = False
            return
        introspection = await self.bus.introspect(BLUEZ_SERVICE, self.path)
        proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, self.path, introspection)
        dev_iface = proxy.get_interface(DEVICE_INTERFACE)
        await dev_iface.call_disconnect()
        self._properties["Connected"] = False

    async def pair(self) -> None:
        """Initiate pairing."""
        if not self.bus:
            self._properties["Paired"] = True
            return
        introspection = await self.bus.introspect(BLUEZ_SERVICE, self.path)
        proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, self.path, introspection)
        dev_iface = proxy.get_interface(DEVICE_INTERFACE)
        await dev_iface.call_pair()
        self._properties["Paired"] = True

    async def set_trusted(self, trusted: bool) -> None:
        """Set trusted flag on device."""
        if not self.bus:
            self._properties["Trusted"] = trusted
            return
        introspection = await self.bus.introspect(BLUEZ_SERVICE, self.path)
        proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, self.path, introspection)
        props_iface = proxy.get_interface(DBUS_PROPERTIES_IFACE)
        await props_iface.call_set(DEVICE_INTERFACE, "Trusted", Variant("b", trusted))
        self._properties["Trusted"] = trusted
