"""Central Bluetooth Manager for BL-HAOS."""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from dbus_fast import BusType, Message, MessageType
from dbus_fast.aio import MessageBus

from .adapter import BluetoothAdapter
from .agent import BlueZAgent
from .constants import (
    ADAPTER_INTERFACE,
    AGENT_MANAGER_INTERFACE,
    AGENT_PATH,
    BLUEZ_SERVICE,
    BLUEZ_SIGNAL_MATCH_RULES,
    DBUS_DAEMON_PATH,
    DBUS_DAEMON_SERVICE,
    DBUS_OM_IFACE,
    DBUS_PROPERTIES_IFACE,
    DEVICE_INTERFACE,
)
from .device import BluetoothDevice
from .models import AdapterInfo, DeviceInfo
from ..constants import (
    ADAPTER_NAME_FALLBACK,
    AGENT_CAPABILITY,
    BLUEZ_ROOT_PATH,
    BLUEZ_ROOT_PATH_TRAILER,
    COMPONENT_BLUETOOTH,
    DEFAULT_PIN,
    DEVICE_PATH_PREFIX,
    EVENT_DBUS_DISCONNECTED,
    EVENT_DEVICE_DISCOVERED,
    EVENT_DEVICE_UPDATED,
    PAIRING_WINDOW_SECONDS,
    SOURCE_BLUEZ,
)
from ..health import FailureClass, HealthRegistry, HealthState, SpeakerState, normalize_address, safe_detail, validate_adapter_name

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
        # Operator-initiated pairing consent (THREAT-MODEL.md, T3): the agent
        # answers BlueZ only for this address, and only until the window expires.
        self._pairing_address: str | None = None
        self._pairing_pin: str | None = None
        self._pairing_expires_at: float = 0.0

    def add_event_listener(self, listener: Callable[[str, Any], None]):
        """Subscribe to live Bluetooth state and discovery events."""
        self._listeners.append(listener)

    def _notify(self, event_type: str, data: Any):
        for listener in self._listeners:
            try:
                listener(event_type, data)
            except Exception as e:
                # One listener must never break D-Bus signal handling, but a bare
                # "listener failed" hides which subscriber dropped which event -
                # and every event a subscriber misses is a state change Home
                # Assistant never sees.
                logger.error(
                    "Event listener %s failed for '%s': %s",
                    getattr(listener, "__qualname__", repr(listener)),
                    event_type,
                    safe_detail(e),
                )

    def _observe_bluetooth(
        self,
        state: HealthState,
        *,
        failure: FailureClass | None = None,
        detail: Any = None,
    ) -> None:
        """Publish one bounded observation for the Bluetooth component."""
        if self.health:
            self.health.observe_component(
                COMPONENT_BLUETOOTH, state, failure=failure, detail=detail, source=SOURCE_BLUEZ
            )

    async def initialize(self) -> None:
        """Connect to system D-Bus, register Agent, and discover initial adapters/devices."""
        try:
            logger.debug("Connecting to system D-Bus...")
            self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            logger.info("Connected to D-Bus System Bus")

            # Register Agent. Every callback consults the pairing window, so a
            # device in radio range is refused unless the operator asked for it.
            self.agent = self.build_agent()
            self.bus.export(AGENT_PATH, self.agent)

            # Register with AgentManager1
            try:
                introspection = await self.bus.introspect(BLUEZ_SERVICE, BLUEZ_ROOT_PATH)
                proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, BLUEZ_ROOT_PATH, introspection)
                agent_mgr = proxy.get_interface(AGENT_MANAGER_INTERFACE)
                await agent_mgr.call_register_agent(AGENT_PATH, AGENT_CAPABILITY)
                await agent_mgr.call_request_default_agent(AGENT_PATH)
                logger.info("BlueZ Pairing Agent successfully registered at %s", AGENT_PATH)
            except Exception as e:
                logger.warning("Agent registration warning: %s", e)

            # Subscribe to ObjectManager and PropertiesChanged signals
            await self._subscribe_signals()
            await self._load_managed_objects()
            # No pairing window is open at boot, so the adapter must not accept
            # pairing requests until an operator asks for one.
            await self.close_pairing_window()
            self._initialized = True
            logger.debug(
                "BluetoothManager initialized with %d adapter(s) and %d device(s)",
                len(self.adapters),
                len(self.devices),
            )
            if self.health:
                self.health.observe_component(COMPONENT_BLUETOOTH, HealthState.HEALTHY, source=SOURCE_BLUEZ)

        except Exception as e:
            logger.warning("System D-Bus connection not available: %s", e)
            self.bus = None
            self._initialized = False
            self._observe_bluetooth(HealthState.UNAVAILABLE, failure=FailureClass.DBUS_UNAVAILABLE, detail=e)

    async def _subscribe_signals(self):
        if not self.bus:
            return
        if self._signal_bus is self.bus:
            return
        # The bus daemon only forks BlueZ's broadcast signals to connections
        # that registered a matching match rule; `add_message_handler()` routes
        # already-delivered messages but installs no rule of its own. Without
        # this step discovery results never reach the handler, so the device
        # list stays empty and the UI never updates while scanning.
        for match_rule in BLUEZ_SIGNAL_MATCH_RULES:
            await self._add_match_rule(match_rule)
        self.bus.add_message_handler(self._handle_dbus_message)
        self._signal_bus = self.bus

    async def _add_match_rule(self, match_rule: str) -> bool:
        """Ask the D-Bus daemon to deliver a matching BlueZ signal to us."""
        if not self.bus:
            return False
        try:
            reply = await self.bus.call(
                Message(
                    destination=DBUS_DAEMON_SERVICE,
                    interface=DBUS_DAEMON_SERVICE,
                    path=DBUS_DAEMON_PATH,
                    member="AddMatch",
                    signature="s",
                    body=[match_rule],
                )
            )
        except Exception as e:
            logger.warning("BlueZ signal match rule failed (%s): %s", match_rule, e)
            return False
        if reply.message_type == MessageType.ERROR:
            logger.warning("BlueZ signal match rule rejected (%s): %s", match_rule, reply.body)
            return False
        logger.debug("Registered BlueZ signal match rule: %s", match_rule)
        return True

    def mark_dbus_disconnected(self, detail: Any = None) -> None:
        """Invalidate transport readiness and publish a bounded loss observation."""
        self.bus = None
        self._initialized = False
        self.adapters.clear()
        self.devices.clear()
        self._observe_bluetooth(
            HealthState.UNAVAILABLE,
            failure=FailureClass.DBUS_DISCONNECTED,
            detail=detail or "D-Bus transport disconnected",
        )
        self._notify(EVENT_DBUS_DISCONNECTED, detail)

    async def recover_dbus(self) -> bool:
        """Perform one bounded D-Bus reinitialization attempt."""
        if self.bus is not None and self._initialized:
            return True
        await self.initialize()
        if self.bus is None:
            self._observe_bluetooth(
                HealthState.UNAVAILABLE,
                failure=FailureClass.DBUS_UNAVAILABLE,
                detail="D-Bus reinitialization unavailable",
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
            self._drop_detached_duplicates(device)
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
            device = self.devices[path]
            if self._is_known_speaker(device):
                # BlueZ withdrew the object, but the operator still owns this
                # speaker. Keep the last known record as an offline speaker so the
                # API, the dashboard and the integration keep seeing it - deleting
                # it here is what made a switched-off speaker vanish from the
                # paired list and removed its Home Assistant entity.
                device.mark_detached()
                logger.debug("BlueZ device went offline: %s (%s)", device.address, path)
                self._observe_speaker_offline(device)
                self._notify(EVENT_DEVICE_UPDATED, device.to_info())
                return
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
                self._notify(EVENT_DEVICE_UPDATED, self.devices[path].to_info())
            else:
                dev = BluetoothDevice(self.bus, path, changed)
                self._drop_detached_duplicates(dev)
                self.devices[path] = dev
                logger.debug("BlueZ new device from property change: %s (%s)", dev.address, path)
                self._notify(EVENT_DEVICE_DISCOVERED, dev.to_info())

    @staticmethod
    def _is_known_speaker(device: BluetoothDevice) -> bool:
        """Is this record worth keeping after BlueZ withdraws the object?

        ``Trusted`` is the operator's explicit "this speaker is mine" and
        ``Paired`` means BlueZ itself persists the bond, so either one describes a
        speaker the operator expects to still see while it is switched off.
        Untrusted, unpaired devices are radio noise and keep the old behavior:
        they are dropped together with their BlueZ object.
        """
        return bool(device.trusted or device.paired)

    def _drop_detached_duplicates(self, device: BluetoothDevice) -> None:
        """Forget offline records for the same address at another object path.

        BlueZ can recreate a device object under a new path; keeping both would
        show the operator the same speaker twice, one of them offline.
        """
        for other_path, other in list(self.devices.items()):
            if other_path != device.path and other.detached and other.address == device.address:
                logger.debug("Dropping superseded offline record for %s (%s)", device.address, other_path)
                del self.devices[other_path]

    def _observe_speaker_offline(self, device: BluetoothDevice) -> None:
        """Record the transition to offline for the health snapshot."""
        if not self.health:
            return
        try:
            self.health.observe_speaker(normalize_address(device.address), SpeakerState.DISCONNECTED)
        except ValueError:
            # A malformed address cannot be observed; it is already unusable.
            return

    async def _load_managed_objects(self):
        if not self.bus:
            return
        introspection = await self.bus.introspect(BLUEZ_SERVICE, BLUEZ_ROOT_PATH_TRAILER)
        proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, BLUEZ_ROOT_PATH_TRAILER, introspection)
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
        if dev and dev.detached:
            # The BlueZ object is gone, so this proxy can neither pair nor connect.
            # Keep the offline record (the operator still sees the speaker) but
            # resolve a live proxy through D-Bus below; a live object supersedes
            # the record.
            logger.debug("Offline record for %s needs a live BlueZ proxy", address)
            dev = None
        if dev:
            return dev
        if not self.bus:
            return None
        address = normalize_address(address)
        formatted_addr = address.upper().replace(":", "_")
        for adapter in self.adapters.values():
            dev_path = f"{adapter.path}/{DEVICE_PATH_PREFIX}{formatted_addr}"
            try:
                introspection = await self.bus.introspect(BLUEZ_SERVICE, dev_path)
                proxy = self.bus.get_proxy_object(BLUEZ_SERVICE, dev_path, introspection)
                props_iface = proxy.get_interface(DBUS_PROPERTIES_IFACE)
                props = await props_iface.call_get_all(DEVICE_INTERFACE)
                dev = BluetoothDevice(self.bus, dev_path, props)
                self._drop_detached_duplicates(dev)
                self.devices[dev_path] = dev
                return dev
            except Exception:
                continue
        return None

    def get_adapter_by_name(self, name: str = ADAPTER_NAME_FALLBACK) -> BluetoothAdapter | None:
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

    # ------------------------------------------------------------------
    # Pairing consent (THREAT-MODEL.md, T3)
    #
    # BlueZ routes every pairing and authorization prompt to the process that
    # registered the default agent. Before this existed the agent answered with a
    # fixed PIN and auto-confirmed, so any device in radio range could pair while
    # the adapter was pairable - and a paired audio sink was then published to
    # Home Assistant as a speaker. Consent is now explicit, per device, and
    # bounded in time.
    # ------------------------------------------------------------------
    def build_agent(self) -> BlueZAgent:
        """Create the BlueZ agent wired to this manager's pairing window."""
        return BlueZAgent(
            pin_callback=self.authorized_pin,
            passkey_callback=self.authorized_passkey,
            confirm_callback=self.confirm_pairing,
            authorization_callback=self.pairing_is_authorized,
        )

    def address_from_device_path(self, device_path: str) -> str | None:
        """Resolve a BlueZ object path to the address of the device it names."""
        device = self.devices.get(device_path)
        candidate = device.address if device is not None else ""
        if not candidate and "dev_" in device_path:
            # The object may be too new to be in the cache; the path is canonical.
            candidate = device_path.rsplit("dev_", 1)[-1].replace("_", ":")
        try:
            return normalize_address(candidate)
        except ValueError:
            return None

    def pairing_window_active(self, address: str) -> bool:
        """True while the operator's pairing request for this address is open."""
        if self._pairing_address is None:
            return False
        if time.monotonic() >= self._pairing_expires_at:
            self._pairing_address = None
            self._pairing_pin = None
            return False
        return self._pairing_address == address

    async def open_pairing_window(self, address: str, pin: str | None = None) -> None:
        """Allow one specific device to pair, for a bounded time."""
        address = normalize_address(address)
        self._pairing_address = address
        self._pairing_pin = pin or DEFAULT_PIN
        self._pairing_expires_at = time.monotonic() + PAIRING_WINDOW_SECONDS
        logger.info(
            "Pairing window open for %s (%ds); all other devices are refused",
            address,
            PAIRING_WINDOW_SECONDS,
        )
        await self._set_adapters_pairable(True)

    async def close_pairing_window(self) -> None:
        """Close the window and stop the adapters accepting pairing requests."""
        was_open = self._pairing_address is not None
        self._pairing_address = None
        self._pairing_pin = None
        self._pairing_expires_at = 0.0
        if was_open:
            logger.info("Pairing window closed")
        await self._set_adapters_pairable(False)

    async def _set_adapters_pairable(self, pairable: bool) -> None:
        """Best-effort adapter posture change; a failure must not fail pairing."""
        for adapter in self.adapters.values():
            try:
                await adapter.set_pairable(pairable)
            except Exception as error:
                logger.warning(
                    "Could not set Pairable=%s on %s: %s",
                    pairable,
                    adapter.interface_name,
                    safe_detail(error),
                )

    def current_pairing_address(self) -> str | None:
        """The address that may currently pair, if any (surfaced in diagnostics)."""
        if self._pairing_address is None:
            return None
        return self._pairing_address if self.pairing_window_active(self._pairing_address) else None

    def pairing_is_authorized(self, device_path: str) -> bool:
        """Whether BlueZ may complete a pairing or authorization for this device."""
        address = self.address_from_device_path(device_path)
        if address is None:
            return False
        if self.pairing_window_active(address):
            return True
        device = self.devices.get(device_path)
        return bool(device is not None and device.trusted)

    def authorized_pin(self, device_path: str) -> str | None:
        """The PIN to answer with, only for the device the operator asked to pair."""
        address = self.address_from_device_path(device_path)
        if address is None or not self.pairing_window_active(address):
            return None
        return self._pairing_pin

    def authorized_passkey(self, device_path: str) -> int | None:
        """Numeric passkey pairing, only when the operator supplied such a PIN."""
        pin = self.authorized_pin(device_path)
        if pin is None or not pin.isdigit():
            return None
        return int(pin)

    def confirm_pairing(self, device_path: str, passkey: int) -> bool:
        """Answer BlueZ's confirmation prompt only for an authorized pairing."""
        authorized = self.pairing_is_authorized(device_path)
        if not authorized:
            logger.warning("Refusing pairing confirmation for unauthorized device %s", device_path)
        return authorized

    async def pair_and_trust(self, address: str, pin: str | None = None) -> bool:
        """Pair with a device the operator asked for, then trust it for reconnects.

        The pairing window is opened for exactly this address and closed again in
        `finally`, so the agent answers BlueZ only while this call runs.
        """
        address = normalize_address(address)
        logger.debug("Starting pair_and_trust for %s", address)
        await self.open_pairing_window(address, pin)
        try:
            return await self._pair_and_trust(address)
        finally:
            await self.close_pairing_window()

    async def _pair_and_trust(self, address: str) -> bool:
        """Pair and trust one address; the caller holds its pairing window."""
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

        logger.debug("Invoking BlueZ pair on %s (%s)", address, dev.path)
        await dev.pair()
        # Pairing may leave a newly recreated BlueZ object paired but not
        # connected, so explicitly establish the A2DP link before publishing
        # it as a trusted speaker.
        logger.debug("Connecting device %s after pairing to establish audio profile", address)
        await dev.connect()

        logger.debug("Marking device %s as trusted", address)
        await dev.set_trusted(True)
        logger.info("Device %s successfully paired, connected, and trusted", address)
        return True

    async def connect_device(self, address: str, *, reset_existing: bool = False) -> bool:
        """Connect to a device.

        ``reset_existing`` is for the operator path only. Connecting used to always
        drop an existing link first to clear a stale A2DP transport; when the
        auto-reconnect engine did the same thing, its own retry tore down a link
        that was fine (or still coming up) and re-negotiated the codec, which is
        heard as continuous disconnects and quality drops. An already-connected
        device is now reported as success, and only an explicit operator request
        resets the link.
        """
        address = normalize_address(address)
        logger.debug("Initiating connect_device for %s (reset_existing=%s)", address, reset_existing)
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
            if getattr(dev, "connected", False):
                if not reset_existing:
                    # Already up: connecting again would only risk breaking it, and
                    # BlueZ would answer AlreadyConnected anyway.
                    logger.debug("Device %s is already connected; nothing to do", address)
                    return True
                # A connected BlueZ ACL can retain a stale A2DP transport in
                # PipeWire. Force a clean link before reconnecting the profile.
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
        # Look in the cache first so an offline speaker can still be forgotten:
        # ``ensure_device`` deliberately refuses a detached record, whose BlueZ
        # proxy is gone.
        dev = self.get_device_by_address(address)
        detached = bool(dev and dev.detached)
        if dev is None:
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
        if not self.bus or detached:
            # Either there is no bus to ask or BlueZ already withdrew the object:
            # there is nothing left to unpair, so forgetting the record is enough.
            self.devices.pop(dev.path, None)
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
