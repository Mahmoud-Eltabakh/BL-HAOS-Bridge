"""Central Bluetooth Manager for BL-HAOS."""

import asyncio
import logging
from typing import Dict, List, Optional, Callable, Any
from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Variant

from .constants import (
    BLUEZ_SERVICE,
    DBUS_OM_IFACE,
    DBUS_PROPERTIES_IFACE,
    ADAPTER_INTERFACE,
    DEVICE_INTERFACE,
    AGENT_MANAGER_INTERFACE,
    AGENT_PATH,
)
from .adapter import BluetoothAdapter
from .device import BluetoothDevice
from .agent import BlueZAgent
from .models import AdapterInfo, DeviceInfo

logger = logging.getLogger("bl_haos.bluetooth.manager")


class BluetoothManager:
    def __init__(self):
        self.bus: Optional[MessageBus] = None
        self.agent: Optional[BlueZAgent] = None
        self.adapters: Dict[str, BluetoothAdapter] = {}
        self.devices: Dict[str, BluetoothDevice] = {}
        self._listeners: List[Callable[[str, Any], None]] = []
        self._initialized = False

    def add_event_listener(self, listener: Callable[[str, Any], None]):
        """Subscribe to live Bluetooth state and discovery events."""
        self._listeners.append(listener)

    def _notify(self, event_type: str, data: Any):
        for listener in self._listeners:
            try:
                listener(event_type, data)
            except Exception as e:
                logger.error("Error in event listener: %s", e)

    async def initialize(self) -> None:
        """Connect to system D-Bus, register Agent, and discover initial adapters/devices."""
        try:
            self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            logger.info("Connected to D-Bus System Bus")

            # Register Agent
            self.agent = BlueZAgent()
            self.bus.export(AGENT_PATH, self.agent)

            # Register with AgentManager1
            try:
                introspection = await self.bus.introspect(BLUEZ_SERVICE, "/org/bluez")
                proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, "/org/bluez", introspection)
                agent_mgr = proxy.get_interface(AGENT_MANAGER_INTERFACE)
                await agent_mgr.call_register_agent(AGENT_PATH, "DisplayYesNo")
                await agent_mgr.call_request_default_agent(AGENT_PATH)
                logger.info("BlueZ Pairing Agent successfully registered at %s", AGENT_PATH)
            except Exception as e:
                logger.warning("Agent registration warning: %s", e)

            # Subscribe to ObjectManager and PropertiesChanged signals
            await self._subscribe_signals()
            await self._load_managed_objects()
            self._initialized = True

        except Exception as e:
            logger.warning("System D-Bus connection not available: %s", e)
            self._initialized = True

    async def _subscribe_signals(self):
        if not self.bus:
            return
        # InterfacesAdded / Removed
        self.bus.add_message_handler(self._handle_dbus_message)

    def _handle_dbus_message(self, msg):
        try:
            if msg.member == "InterfacesAdded" and msg.interface == DBUS_OM_IFACE and len(msg.body) >= 2:
                path, interfaces = msg.body[0], msg.body[1]
                self._on_interfaces_added(path, interfaces)
            elif msg.member == "InterfacesRemoved" and msg.interface == DBUS_OM_IFACE and len(msg.body) >= 2:
                path, interfaces = msg.body[0], msg.body[1]
                self._on_interfaces_removed(path, interfaces)
            elif msg.member == "PropertiesChanged" and msg.interface == DBUS_PROPERTIES_IFACE and len(msg.body) >= 3:
                iface, changed = msg.body[0], msg.body[1]
                self._on_properties_changed(msg.path, iface, changed)
        except Exception as e:
            logger.debug("Non-fatal D-Bus message handler notice: %s", e)
        return False

    def _on_interfaces_added(self, path: str, interfaces: Dict[str, Any]):
        if ADAPTER_INTERFACE in interfaces:
            adapter = BluetoothAdapter(self.bus, path, interfaces[ADAPTER_INTERFACE])
            self.adapters[path] = adapter
            self._notify("adapter_added", adapter.to_info())
        if DEVICE_INTERFACE in interfaces:
            device = BluetoothDevice(self.bus, path, interfaces[DEVICE_INTERFACE])
            self.devices[path] = device
            self._notify("device_discovered", device.to_info())

    def _on_interfaces_removed(self, path: str, interfaces: List[str]):
        if ADAPTER_INTERFACE in interfaces and path in self.adapters:
            del self.adapters[path]
            self._notify("adapter_removed", path)
        if DEVICE_INTERFACE in interfaces and path in self.devices:
            del self.devices[path]
            self._notify("device_removed", path)

    def _on_properties_changed(self, path: str, iface: str, changed: Dict[str, Any]):
        if iface == ADAPTER_INTERFACE and path in self.adapters:
            self.adapters[path].update_properties(changed)
            self._notify("adapter_updated", self.adapters[path].to_info())
        elif iface == DEVICE_INTERFACE and path in self.devices:
            self.devices[path].update_properties(changed)
            self._notify("device_updated", self.devices[path].to_info())

    async def _load_managed_objects(self):
        if not self.bus:
            return
        introspection = await self.bus.introspect(BLUEZ_SERVICE, "/")
        proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, "/", introspection)
        om = proxy.get_interface(DBUS_OM_IFACE)
        objects = await om.call_get_managed_objects()

        for path, interfaces in objects.items():
            if ADAPTER_INTERFACE in interfaces:
                self.adapters[path] = BluetoothAdapter(self.bus, path, interfaces[ADAPTER_INTERFACE])
            if DEVICE_INTERFACE in interfaces:
                self.devices[path] = BluetoothDevice(self.bus, path, interfaces[DEVICE_INTERFACE])

    def get_adapters(self) -> List[AdapterInfo]:
        return [adapter.to_info() for adapter in self.adapters.values()]

    def get_devices(self, audio_only: bool = True) -> List[DeviceInfo]:
        devices = [dev.to_info() for dev in self.devices.values()]
        if audio_only:
            return [d for d in devices if d.is_audio_sink]
        return devices

    def get_device_by_address(self, address: str) -> Optional[BluetoothDevice]:
        target = address.strip().lower()
        for dev in self.devices.values():
            if dev.address.lower() == target:
                return dev
        return None

    def get_adapter_by_name(self, name: str = "hci0") -> Optional[BluetoothAdapter]:
        for adapter in self.adapters.values():
            if adapter.interface_name == name or adapter.path.endswith(name):
                return adapter
        return None

    async def start_scan(self, adapter_name: Optional[str] = None) -> None:
        """Start discovery on specified adapter or all adapters."""
        if adapter_name:
            adapter = self.get_adapter_by_name(adapter_name)
            if adapter:
                await adapter.start_discovery()
        else:
            for adapter in self.adapters.values():
                await adapter.start_discovery()

    async def stop_scan(self, adapter_name: Optional[str] = None) -> None:
        """Stop discovery on specified adapter or all adapters."""
        if adapter_name:
            adapter = self.get_adapter_by_name(adapter_name)
            if adapter:
                await adapter.stop_discovery()
        else:
            for adapter in self.adapters.values():
                await adapter.stop_discovery()

    async def pair_and_trust(self, address: str) -> bool:
        """Pair with device and set trusted flag for auto-reconnection."""
        dev = self.get_device_by_address(address)
        if not dev:
            raise ValueError(f"Device with address {address} not found")
        await dev.pair()
        await dev.set_trusted(True)
        return True

    async def connect_device(self, address: str) -> bool:
        """Connect to device."""
        dev = self.get_device_by_address(address)
        if not dev:
            raise ValueError(f"Device with address {address} not found")
        await dev.connect()
        return True

    async def disconnect_device(self, address: str) -> bool:
        """Disconnect from device."""
        dev = self.get_device_by_address(address)
        if not dev:
            raise ValueError(f"Device with address {address} not found")
        await dev.disconnect()
        return True

    async def remove_device(self, address: str) -> bool:
        """Remove device from adapter cache and unpair."""
        dev = self.get_device_by_address(address)
        if not dev:
            return False
        adapter_path = dev.adapter_path
        if not self.bus:
            if dev.path in self.devices:
                del self.devices[dev.path]
            return True

        introspection = await self.bus.introspect(BLUEZ_SERVICE, adapter_path)
        proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, adapter_path, introspection)
        adapter_iface = proxy.get_interface(ADAPTER_INTERFACE)
        await adapter_iface.call_remove_device(dev.path)
        if dev.path in self.devices:
            del self.devices[dev.path]
        return True
