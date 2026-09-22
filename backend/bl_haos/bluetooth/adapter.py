"""Bluetooth Adapter Controller wrapping org.bluez.Adapter1."""

import logging
from typing import Dict, Any, Optional
from dbus_fast.aio import MessageBus
from dbus_fast import Variant

from .constants import BLUEZ_SERVICE, ADAPTER_INTERFACE, DBUS_PROPERTIES_IFACE
from .models import AdapterInfo

logger = logging.getLogger("bl_haos.bluetooth.adapter")


class BluetoothAdapter:
    def __init__(self, bus: Optional[MessageBus], path: str, properties: Dict[str, Any]):
        self.bus = bus
        self.path = path
        self._properties = properties
        self.interface_name = path.split("/")[-1]

    @property
    def interface(self) -> str:
        return self.interface_name

    def _get_prop(self, key: str, default: Any = None) -> Any:
        val = self._properties.get(key, default)
        if isinstance(val, Variant):
            return val.value
        return val

    @property
    def address(self) -> str:
        return self._get_prop("Address", "")

    @property
    def name(self) -> str:
        return self._get_prop("Name", f"Bluetooth Adapter ({self.interface_name})")

    @property
    def alias(self) -> str:
        return self._get_prop("Alias", self.name)

    @property
    def powered(self) -> bool:
        return bool(self._get_prop("Powered", False))

    @property
    def discovering(self) -> bool:
        return bool(self._get_prop("Discovering", False))

    @property
    def discoverable(self) -> bool:
        return bool(self._get_prop("Discoverable", False))

    @property
    def pairable(self) -> bool:
        return bool(self._get_prop("Pairable", False))

    def update_properties(self, changed: Dict[str, Any]):
        """Update cached properties from D-Bus PropertiesChanged signal."""
        for k, v in changed.items():
            self._properties[k] = v.value if isinstance(v, Variant) else v

    def to_info(self) -> AdapterInfo:
        return AdapterInfo(
            path=self.path,
            interface=self.interface_name,
            address=self.address,
            name=self.name,
            alias=self.alias,
            powered=self.powered,
            discoverable=self.discoverable,
            discovering=self.discovering,
            pairable=self.pairable,
            class_of_device=self._get_prop("Class"),
            uuids=self._get_prop("UUIDs", []),
        )

    async def set_power(self, powered: bool) -> None:
        """Set adapter powered state."""
        if not self.bus:
            self._properties["Powered"] = powered
            return
        introspection = await self.bus.introspect(BLUEZ_SERVICE, self.path)
        proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, self.path, introspection)
        props_iface = proxy.get_interface(DBUS_PROPERTIES_IFACE)
        await props_iface.call_set(ADAPTER_INTERFACE, "Powered", Variant("b", powered))
        self._properties["Powered"] = powered

    async def start_discovery(self) -> None:
        """Start discovery scan on this adapter."""
        if not self.bus:
            self._properties["Discovering"] = True
            return
        try:
            introspection = await self.bus.introspect(BLUEZ_SERVICE, self.path)
            proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, self.path, introspection)
            adapter_iface = proxy.get_interface(ADAPTER_INTERFACE)
            await adapter_iface.call_start_discovery()
        except Exception as e:
            if "InProgress" in str(e) or "already in progress" in str(e).lower():
                logger.debug("Discovery already in progress on %s", self.interface_name)
            else:
                raise e
        self._properties["Discovering"] = True

    async def stop_discovery(self) -> None:
        """Stop discovery scan on this adapter."""
        if not self.bus:
            self._properties["Discovering"] = False
            return
        try:
            introspection = await self.bus.introspect(BLUEZ_SERVICE, self.path)
            proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, self.path, introspection)
            adapter_iface = proxy.get_interface(ADAPTER_INTERFACE)
            await adapter_iface.call_stop_discovery()
        except Exception as e:
            if "InProgress" in str(e) or "not discovering" in str(e).lower() or "not in progress" in str(e).lower():
                logger.debug("Discovery already stopped on %s", self.interface_name)
            else:
                pass
        self._properties["Discovering"] = False

    async def connect_device(self, address: str, address_type: str = "public") -> Optional[str]:
        """Connect directly to a device by MAC address, creating the D-Bus object if needed."""
        if not self.bus:
            return None
        introspection = await self.bus.introspect(BLUEZ_SERVICE, self.path)
        proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, self.path, introspection)
        adapter_iface = proxy.get_interface(ADAPTER_INTERFACE)
        props = {
            "Address": Variant("s", address.strip().upper()),
            "AddressType": Variant("s", address_type),
        }
        return await adapter_iface.call_connect_device(props)
