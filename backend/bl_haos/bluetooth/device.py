"""Bluetooth Device Controller wrapping org.bluez.Device1."""

import logging
import time
from typing import Any

from dbus_fast import Variant
from dbus_fast.aio import MessageBus

from .constants import (
    A2DP_SINK_UUID,
    AUDIO_SERVICE_SHORT_UUIDS,
    AUDIO_SINK_UUIDS,
    BLUEZ_SERVICE,
    DBUS_PROPERTIES_IFACE,
    DEVICE_INTERFACE,
    MAJOR_DEVICE_CLASS_AUDIO_VIDEO,
    MINOR_DEVICE_CLASSES_AUDIO,
)
from .models import DeviceInfo
from ..constants import (
    ADAPTER_NAME_FALLBACK,
    AUDIO_DEVICE_LABEL,
    AUDIO_ICON_HINTS,
    AUDIO_NAME_KEYWORDS,
    BLUETOOTH_DEVICE_LABEL,
    CLASS_OF_DEVICE_MAJOR_MASK,
    CLASS_OF_DEVICE_MINOR_MASK,
    ICON_TO_DEVICE_TYPE,
    NON_AUDIO_ICON_HINTS,
    SPEAKER_DEVICE_TYPE,
)

logger = logging.getLogger("bl_haos.bluetooth.device")


class BluetoothOperationInProgress(RuntimeError):
    """Raised when BlueZ is already pairing or connecting this device."""


class BluetoothDevice:
    def __init__(self, bus: MessageBus | None, path: str, properties: dict[str, Any]):
        self.bus = bus
        self.path = path
        self._properties = properties
        self.last_seen = time.time()
        # BlueZ withdraws the object of a device it treats as temporary, which is
        # every device that is not paired - including a speaker the operator has
        # trusted. The bridge keeps the last known record and flags it, so the
        # device stays visible as offline instead of disappearing from the API,
        # the dashboard and Home Assistant.
        self.detached = False

    def _get_prop(self, key: str, default: Any = None) -> Any:
        val = self._properties.get(key, default)
        if isinstance(val, Variant):
            return val.value
        return val

    @property
    def address(self) -> str:
        return self._get_prop("Address", "")

    @property
    def name(self) -> str | None:
        return self._get_prop("Name")

    @property
    def alias(self) -> str:
        return self._get_prop("Alias", self.name or self.address)

    @property
    def adapter_path(self) -> str:
        return self._get_prop("Adapter", "")

    @property
    def adapter_name(self) -> str:
        return self.adapter_path.split("/")[-1] if self.adapter_path else ADAPTER_NAME_FALLBACK

    @property
    def paired(self) -> bool:
        return bool(self._get_prop("Paired", False))

    @property
    def trusted(self) -> bool:
        return bool(self._get_prop("Trusted", False))

    @property
    def connected(self) -> bool:
        # A detached record keeps its last known properties, so ``Connected`` can
        # still read True after BlueZ withdrew the object. The device is offline
        # either way, and every consumer (available flags, UI badges, reconnect
        # engine) must see that.
        return bool(self._get_prop("Connected", False)) and not self.detached

    @property
    def blocked(self) -> bool:
        return bool(self._get_prop("Blocked", False))

    @property
    def rssi(self) -> int | None:
        return self._get_prop("RSSI")

    @property
    def class_of_device(self) -> int | None:
        return self._get_prop("Class")

    @property
    def uuids(self) -> list[str]:
        return [str(u).lower() for u in self._get_prop("UUIDs", [])]

    @property
    def is_audio_sink(self) -> bool:
        """Determine if this device functions as a Bluetooth audio sink (speaker/headphone)."""
        # Check UUIDs
        for uuid in self.uuids:
            u_clean = str(uuid).lower().strip()
            if u_clean in AUDIO_SINK_UUIDS:
                return True
            # Also check if 16-bit audio service identifier is embedded in standard 128-bit UUID
            if any(part in u_clean for part in AUDIO_SERVICE_SHORT_UUIDS):
                return True

        # Check Class of Device
        if self.class_of_device is not None:
            major = self.class_of_device & CLASS_OF_DEVICE_MAJOR_MASK
            if major == MAJOR_DEVICE_CLASS_AUDIO_VIDEO:
                return True
            minor = self.class_of_device & CLASS_OF_DEVICE_MINOR_MASK
            if minor in MINOR_DEVICE_CLASSES_AUDIO:
                return True

        # Check icon hint
        icon = str(self._get_prop("Icon", "")).lower()
        if any(hint in icon for hint in AUDIO_ICON_HINTS):
            return True

        # Check name or alias audio keywords
        name_or_alias = f"{self.name or ''} {self.alias or ''}".lower()
        if any(keyword in name_or_alias for keyword in AUDIO_NAME_KEYWORDS):
            return True

        # If currently connected or paired (and not an explicit non-audio input device)
        if (self.connected or self.paired) and icon not in NON_AUDIO_ICON_HINTS:
            return True

        return False

    @property
    def device_type(self) -> str:
        """Human-readable device classification."""
        if self.class_of_device is not None:
            minor = self.class_of_device & CLASS_OF_DEVICE_MINOR_MASK
            if minor in MINOR_DEVICE_CLASSES_AUDIO:
                return MINOR_DEVICE_CLASSES_AUDIO[minor]
            if (self.class_of_device & CLASS_OF_DEVICE_MAJOR_MASK) == MAJOR_DEVICE_CLASS_AUDIO_VIDEO:
                return AUDIO_DEVICE_LABEL

        icon = self._get_prop("Icon", "")
        for hint, label in ICON_TO_DEVICE_TYPE:
            if hint in icon:
                return label

        return SPEAKER_DEVICE_TYPE if self.is_audio_sink else BLUETOOTH_DEVICE_LABEL

    def update_properties(self, changed: dict[str, Any]):
        """Update properties and timestamp from PropertiesChanged signal."""
        for k, v in changed.items():
            self._properties[k] = v.value if isinstance(v, Variant) else v
        self.last_seen = time.time()

    def mark_detached(self) -> None:
        """Keep the last known record after BlueZ withdrew the device object."""
        self.detached = True
        self._properties["Connected"] = False

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
            detached=self.detached,
        )

    async def connect(self) -> None:
        """Connect to device."""
        logger.debug("Executing BlueZ connect on device %s (%s)", self.address, self.path)
        if not self.bus:
            self._properties["Connected"] = True
            return
        introspection = await self.bus.introspect(BLUEZ_SERVICE, self.path)
        proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, self.path, introspection)
        dev_iface = proxy.get_interface(DEVICE_INTERFACE)
        connect_error = None
        try:
            await dev_iface.call_connect()
        except Exception as conn_err:
            err_str = str(conn_err)
            if "AlreadyConnected" in err_str or "InProgress" in err_str or "In Progress" in err_str:
                connect_error = None
                try:
                    await dev_iface.call_connect_profile(A2DP_SINK_UUID)
                except Exception as profile_err:
                    prof_str = str(profile_err)
                    if "InProgress" in prof_str or "In Progress" in prof_str:
                        raise BluetoothOperationInProgress(
                            f"Bluetooth connection already in progress for {self.address}"
                        ) from profile_err
                    if "AlreadyConnected" not in prof_str:
                        raise profile_err
            else:
                connect_error = conn_err
                try:
                    await dev_iface.call_connect_profile(A2DP_SINK_UUID)
                except Exception as profile_err:
                    prof_str = str(profile_err)
                    if "InProgress" in prof_str or "In Progress" in prof_str:
                        raise BluetoothOperationInProgress(
                            f"Bluetooth connection already in progress for {self.address}"
                        ) from profile_err
                    if "AlreadyConnected" not in prof_str:
                        raise connect_error or profile_err
        self._properties["Connected"] = True
        logger.debug("BlueZ connect succeeded for %s", self.address)

    async def disconnect(self) -> None:
        """Disconnect from device."""
        logger.debug("Executing BlueZ disconnect on device %s (%s)", self.address, self.path)
        if not self.bus:
            self._properties["Connected"] = False
            return
        try:
            introspection = await self.bus.introspect(BLUEZ_SERVICE, self.path)
            proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, self.path, introspection)
            dev_iface = proxy.get_interface(DEVICE_INTERFACE)
            await dev_iface.call_disconnect()
        except Exception:
            pass
        self._properties["Connected"] = False
        logger.debug("BlueZ disconnect completed for %s", self.address)

    async def pair(self) -> None:
        """Initiate pairing."""
        logger.debug("Executing BlueZ pair on device %s (%s)", self.address, self.path)
        if not self.bus:
            self._properties["Paired"] = True
            return
        introspection = await self.bus.introspect(BLUEZ_SERVICE, self.path)
        proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, self.path, introspection)
        dev_iface = proxy.get_interface(DEVICE_INTERFACE)
        try:
            await dev_iface.call_pair()
        except Exception as e:
            err_str = str(e)
            if "InProgress" in err_str or "In Progress" in err_str or "br-connection-busy" in err_str:
                raise BluetoothOperationInProgress(
                    f"Bluetooth pairing already in progress for {self.address}"
                ) from e
            if "AlreadyExists" in err_str or "AlreadyConnected" in err_str or "AlreadyPaired" in err_str:
                pass
            else:
                raise e
        self._properties["Paired"] = True
        logger.debug("BlueZ pair succeeded for %s", self.address)

    async def set_trusted(self, trusted: bool) -> None:
        """Set trusted flag on device."""
        logger.debug("Setting Trusted=%s on BlueZ Device %s (%s)", trusted, self.address, self.path)
        if not self.bus:
            self._properties["Trusted"] = trusted
            return
        try:
            introspection = await self.bus.introspect(BLUEZ_SERVICE, self.path)
            proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, self.path, introspection)
            props_iface = proxy.get_interface(DBUS_PROPERTIES_IFACE)
            await props_iface.call_set(DEVICE_INTERFACE, "Trusted", Variant("b", trusted))
        except Exception as e:
            logger.debug("Failed to set trusted flag directly: %s", e)
        self._properties["Trusted"] = trusted
