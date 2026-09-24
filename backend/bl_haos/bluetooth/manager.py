"""Central Bluetooth Manager for BL-HAOS."""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from dbus_fast import BusType
from dbus_fast.aio import MessageBus

from .adapter import BluetoothAdapter
from .agent import BlueZAgent
from .constants import (
    ADAPTER_INTERFACE,
    AGENT_MANAGER_INTERFACE,
    AGENT_PATH,
    BLUEZ_SERVICE,
    DBUS_OM_IFACE,
    DBUS_PROPERTIES_IFACE,
    DEVICE_INTERFACE,
)
from .device import BluetoothDevice
from .models import AdapterInfo, DeviceInfo
from ..health import FailureClass, HealthRegistry, HealthState, SpeakerState, normalize_address, validate_adapter_name

logger = logging.getLogger("bl_haos.bluetooth.manager")


class BluetoothManager:
    def __init__(self, health_registry: HealthRegistry | None = None):
        self.bus: MessageBus | None = None
        self.agent: BlueZAgent | None = None
        self.adapters: dict[str, BluetoothAdapter] = {}
        self.devices: dict[str, BluetoothDevice] = {}
        self._listeners: list[Callable[[str, Any], None]] = []
        self._initialized = False
        self.health = health_registry
        self._signal_bus: MessageBus | None = None

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
            logger.debug("Connecting to system D-Bus...")
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
            logger.debug(
                "BluetoothManager initialized with %d adapter(s) and %d device(s)",
                len(self.adapters),
                len(self.devices),
            )
            if self.health:
                self.health.observe_component("bluetooth", HealthState.HEALTHY, source="bluez")

        except Exception as e:
            logger.warning("System D-Bus connection not available: %s", e)
            self.bus = None
            self._initialized = False
            if self.health:
                self.health.observe_component(
                    "bluetooth",
                    HealthState.UNAVAILABLE,
                    failure=FailureClass.DBUS_UNAVAILABLE,
                    detail=e,
                    source="bluez",
                )

    async def _subscribe_signals(self):
        if not self.bus:
            return
        if self._signal_bus is self.bus:
            return
        # InterfacesAdded / Removed
        self.bus.add_message_handler(self._handle_dbus_message)
        self._signal_bus = self.bus

    def mark_dbus_disconnected(self, detail: Any = None) -> None:
        """Invalidate transport readiness and publish a bounded loss observation."""
        self.bus = None
        self._initialized = False
        self.adapters.clear()
        self.devices.clear()
        if self.health:
            self.health.observe_component(
                "bluetooth",
                HealthState.UNAVAILABLE,
                failure=FailureClass.DBUS_DISCONNECTED,
                detail=detail or "D-Bus transport disconnected",
                source="bluez",
            )
        self._notify("dbus_disconnected", detail)

    async def recover_dbus(self) -> bool:
        """Perform one bounded D-Bus reinitialization attempt."""
        if self.bus is not None and self._initialized:
            return True
        await self.initialize()
        if self.bus is None and self.health:
            self.health.observe_component(
                "bluetooth",
                HealthState.UNAVAILABLE,
                failure=FailureClass.DBUS_UNAVAILABLE,
                detail="D-Bus reinitialization unavailable",
                source="bluez",
            )
        return self.bus is not None

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

    def _on_interfaces_added(self, path: str, interfaces: dict[str, Any]):
        if ADAPTER_INTERFACE in interfaces:
            adapter = BluetoothAdapter(self.bus, path, interfaces[ADAPTER_INTERFACE])
            self.adapters[path] = adapter
            logger.debug("BlueZ adapter added: %s (%s)", adapter.interface_name, path)
            self._notify("adapter_added", adapter.to_info())
        if DEVICE_INTERFACE in interfaces:
            device = BluetoothDevice(self.bus, path, interfaces[DEVICE_INTERFACE])
            self.devices[path] = device
            logger.debug(
                "BlueZ device discovered: %s (%s) [audio_sink=%s, connected=%s]",
                device.address,
                device.name or "unknown",
                device.is_audio_sink,
                device.connected,
            )
            self._notify("device_discovered", device.to_info())

    def _on_interfaces_removed(self, path: str, interfaces: list[str]):
        if ADAPTER_INTERFACE in interfaces and path in self.adapters:
            logger.debug("BlueZ adapter removed: %s", path)
            del self.adapters[path]
            self._notify("adapter_removed", path)
        if DEVICE_INTERFACE in interfaces and path in self.devices:
            logger.debug("BlueZ device removed: %s", path)
            del self.devices[path]
            self._notify("device_removed", path)

    def _on_properties_changed(self, path: str, iface: str, changed: dict[str, Any]):
        if iface == ADAPTER_INTERFACE and path in self.adapters:
            self.adapters[path].update_properties(changed)
            logger.debug("BlueZ adapter properties changed on %s: %s", path, list(changed.keys()))
            self._notify("adapter_updated", self.adapters[path].to_info())
        elif iface == DEVICE_INTERFACE:
            if path in self.devices:
                self.devices[path].update_properties(changed)
                logger.debug("BlueZ device properties changed on %s: %s", path, list(changed.keys()))
                self._notify("device_updated", self.devices[path].to_info())
            else:
                dev = BluetoothDevice(self.bus, path, changed)
                self.devices[path] = dev
                logger.debug("BlueZ new device from property change: %s (%s)", dev.address, path)
                self._notify("device_discovered", dev.to_info())

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

    def get_adapters(self) -> list[AdapterInfo]:
        return [adapter.to_info() for adapter in self.adapters.values()]

    def get_devices(self, audio_only: bool = True) -> list[DeviceInfo]:
        devices = [dev.to_info() for dev in self.devices.values()]
        if audio_only:
            return [d for d in devices if d.is_audio_sink]
        return devices

    def get_device_by_address(self, address: str) -> BluetoothDevice | None:
        target = normalize_address(address)
        for dev in self.devices.values():
            try:
                if normalize_address(dev.address) == target:
                    return dev
            except ValueError:
                continue
        return None

    async def ensure_device(self, address: str) -> BluetoothDevice | None:
        """Look up device in local cache or query BlueZ D-Bus directly by MAC."""
        dev = self.get_device_by_address(address)
        if dev:
            return dev
        if not self.bus:
            return None
        address = normalize_address(address)
        formatted_addr = address.upper().replace(":", "_")
        for adapter in self.adapters.values():
            dev_path = f"{adapter.path}/dev_{formatted_addr}"
            try:
                introspection = await self.bus.introspect(BLUEZ_SERVICE, dev_path)
                proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, dev_path, introspection)
                props_iface = proxy.get_interface(DBUS_PROPERTIES_IFACE)
                props = await props_iface.call_get_all(DEVICE_INTERFACE)
                dev = BluetoothDevice(self.bus, dev_path, props)
                self.devices[dev_path] = dev
                return dev
            except Exception:
                continue
        return None

    def get_adapter_by_name(self, name: str = "hci0") -> BluetoothAdapter | None:
        name = validate_adapter_name(name)
        for adapter in self.adapters.values():
            if adapter.interface_name == name or adapter.path.endswith(name):
                return adapter
        return None

    async def start_scan(self, adapter_name: str | None = None) -> None:
        """Start discovery on specified adapter or all adapters."""
        if adapter_name:
            adapter_name = validate_adapter_name(adapter_name)
            adapter = self.get_adapter_by_name(adapter_name)
            if adapter:
                await adapter.start_discovery()
        else:
            for adapter in self.adapters.values():
                await adapter.start_discovery()

    async def stop_scan(self, adapter_name: str | None = None) -> None:
        """Stop discovery on specified adapter or all adapters."""
        if adapter_name:
            adapter_name = validate_adapter_name(adapter_name)
            adapter = self.get_adapter_by_name(adapter_name)
            if adapter:
                await adapter.stop_discovery()
        else:
            for adapter in self.adapters.values():
                await adapter.stop_discovery()

    async def pair_and_trust(self, address: str) -> bool:
        """Pair with device and set trusted flag for auto-reconnection."""
        address = normalize_address(address)
        logger.debug("Starting pair_and_trust for %s", address)
        try:
            await self.stop_scan()
        except Exception as e:
            logger.debug("Non-fatal notice stopping scan prior to pair: %s", e)
        await asyncio.sleep(0.3)
        dev = await self.ensure_device(address)
        if dev and self.bus and not dev.connected:
            # A cached Device1 proxy can survive BlueZ removing and recreating
            # the object. Refresh disconnected proxies before pairing again.
            self.devices.pop(dev.path, None)
            refreshed = await self.ensure_device(address)
            if refreshed:
                dev = refreshed
        if not dev:
            for adapter in self.adapters.values():
                try:
                    await adapter.connect_device(address)
                    dev = await self.ensure_device(address)
                    if dev:
                        break
                except Exception:
                    continue
            if not dev:
                dev = await self.ensure_device(address)

        if not dev:
            logger.debug("Device %s not found on any adapter for pairing", address)
            raise ValueError(f"Device with address {address} not found. Ensure device is powered on and in pairing mode.")

        try:
            logger.debug("Invoking BlueZ pair on %s (%s)", address, dev.path)
            await dev.pair()
        except Exception as e:
            logger.warning("Pair call fallback for %s: %s", address, e)
        # Pairing may leave a newly recreated BlueZ object paired but not
        # connected, so explicitly establish the A2DP link before publishing
        # it as a trusted speaker.
        logger.debug("Connecting device %s after pairing to establish audio profile", address)
        await dev.connect()

        logger.debug("Marking device %s as trusted", address)
        await dev.set_trusted(True)
        logger.debug("Device %s successfully paired, connected, and trusted", address)
        return True

    async def connect_device(self, address: str) -> bool:
        """Connect to device."""
        address = normalize_address(address)
        logger.debug("Initiating connect_device for %s", address)
        try:
            await self.stop_scan()
        except Exception as e:
            logger.debug("Non-fatal notice stopping scan prior to connect: %s", e)
        await asyncio.sleep(0.3)
        dev = await self.ensure_device(address)
        if not dev:
            for adapter in self.adapters.values():
                try:
                    await adapter.connect_device(address)
                    dev = await self.ensure_device(address)
                    if dev:
                        break
                except Exception:
                    continue
            if not dev:
                logger.debug("Device %s not found on any adapter for connect", address)
                raise ValueError(f"Device with address {address} not found. Ensure device is powered on and in pairing mode.")
        try:
            # A connected BlueZ ACL can retain a stale A2DP transport in
            # PipeWire. Force a clean link before reconnecting the profile.
            if getattr(dev, "connected", False):
                logger.debug("Resetting existing connection for %s before reconnecting", address)
                await dev.disconnect()
                await asyncio.sleep(1.0)
            for attempt in range(3):
                try:
                    logger.debug("Connect attempt %d for %s", attempt + 1, address)
                    await dev.connect()
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(1.0)
            try:
                await dev.set_trusted(True)
            except Exception as e:
                logger.debug("Failed to set trusted flag on connect: %s", e)
            logger.debug("Device %s connected successfully", address)
            return True
        except Exception as first_error:
            logger.debug("First connect attempt failed for %s: %s", address, first_error)
            # BlueZ can replace a discovered device object while scanning or
            # reconnecting. Refresh the cached object once before surfacing the
            # transient org.bluez.Device1 error to the API.
            if dev.path in self.devices:
                del self.devices[dev.path]
            refreshed = await self.ensure_device(address)
            if refreshed:
                try:
                    logger.debug("Retrying connect on refreshed BlueZ proxy for %s", address)
                    await refreshed.connect()
                except Exception as refresh_error:
                    if self.health:
                        self.health.observe_speaker(
                            normalize_address(address),
                            SpeakerState.UNAVAILABLE,
                            failure=FailureClass.STALE_BLUEZ_OBJECT,
                            detail=refresh_error,
                        )
                    raise refresh_error
                try:
                    await refreshed.set_trusted(True)
                except Exception:
                    pass
                logger.debug("Refreshed proxy connection succeeded for %s", address)
                return True
            if self.health:
                self.health.observe_speaker(
                    normalize_address(address),
                    SpeakerState.UNAVAILABLE,
                    failure=FailureClass.STALE_BLUEZ_OBJECT,
                    detail=first_error,
                )
            raise first_error

    async def disconnect_device(self, address: str) -> bool:
        """Disconnect from device."""
        address = normalize_address(address)
        logger.debug("Disconnecting device %s", address)
        dev = await self.ensure_device(address)
        if not dev:
            raise ValueError(f"Device with address {address} not found")
        await dev.disconnect()
        logger.debug("Disconnected device %s", address)
        return True

    async def remove_device(self, address: str) -> bool:
        """Untrust, unpair, disconnect, and completely remove device from adapter cache."""
        address = normalize_address(address)
        dev = await self.ensure_device(address)
        if not dev:
            return False

        # 1. Untrust first to ensure no auto-reconnect signals or trusted states remain
        try:
            await dev.set_trusted(False)
        except Exception as e:
            logger.debug("Failed to set trusted=False on %s: %s", address, e)

        # 2. Disconnect if connected
        if dev.connected:
            try:
                await dev.disconnect()
            except Exception as e:
                logger.debug("Failed to disconnect %s during removal: %s", address, e)

        adapter_path = dev.adapter_path
        if not self.bus:
            if dev.path in self.devices:
                del self.devices[dev.path]
            return True

        # 3. Call adapter RemoveDevice D-Bus method to remove pairing & BlueZ cache completely
        try:
            introspection = await self.bus.introspect(BLUEZ_SERVICE, adapter_path)
            proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, adapter_path, introspection)
            adapter_iface = proxy.get_interface(ADAPTER_INTERFACE)
            await adapter_iface.call_remove_device(dev.path)
        except Exception as e:
            logger.warning("BlueZ RemoveDevice failed for %s (%s): %s", address, dev.path, e)

        if dev.path in self.devices:
            del self.devices[dev.path]
        return True
