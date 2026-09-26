import time
from types import SimpleNamespace

import pytest
from dbus_fast import Variant
from backend.bl_haos.bluetooth.constants import (
    A2DP_SINK_UUID,
    ADAPTER_INTERFACE,
    DEVICE_INTERFACE,
)
from backend.bl_haos.bluetooth.manager import BluetoothManager
from backend.bl_haos.health import FailureClass, HealthRegistry, HealthState, SpeakerState


@pytest.mark.asyncio
async def test_bluetooth_manager_lifecycle():
    mgr = BluetoothManager()
    await mgr.initialize()

    assert isinstance(mgr.get_adapters(), list)
    assert isinstance(mgr.get_devices(), list)

@pytest.mark.asyncio
async def test_bluetooth_manager_event_listeners():
    mgr = BluetoothManager()
    await mgr.initialize()

    events = []
    mgr.add_event_listener(lambda ev, data: events.append((ev, data)))

    # Simulate dynamic discovery via D-Bus ObjectManager InterfacesAdded signal
    adapter_path = "/org/bluez/hci0"
    mgr._on_interfaces_added(adapter_path, {
        ADAPTER_INTERFACE: {
            "Address": "00:11:22:33:44:55",
            "Name": "Controller",
            "Powered": True,
            "Discovering": False,
        }
    })

    assert len(events) == 1
    assert events[0][0] == "adapter_added"
    assert mgr.get_adapter_by_name("hci0") is not None

    # Trigger scan
    await mgr.start_scan("hci0")
    adapter = mgr.get_adapter_by_name("hci0")
    assert adapter.discovering is True

    await mgr.stop_scan("hci0")
    assert adapter.discovering is False

@pytest.mark.asyncio
async def test_bluetooth_manager_device_pairing_and_removal():
    mgr = BluetoothManager()
    await mgr.initialize()

    dev_path = "/org/bluez/hci0/dev_AA_BB_CC_11_22_33"
    dev_addr = "AA:BB:CC:11:22:33"
    mgr._on_interfaces_added(dev_path, {
        DEVICE_INTERFACE: {
            "Address": dev_addr,
            "Name": "Bluetooth Speaker",
            "Adapter": "/org/bluez/hci0",
            "UUIDs": [A2DP_SINK_UUID],
            "Class": 0x240414,
            "Paired": False,
            "Trusted": False,
            "Connected": False,
        }
    })

    devs = mgr.get_devices(audio_only=True)
    assert len(devs) == 1
    assert devs[0].address == dev_addr

    paired = await mgr.pair_and_trust(dev_addr)
    assert paired is True

    dev = mgr.get_device_by_address(dev_addr)
    assert dev.trusted is True
    assert dev.paired is True
    assert dev.connected is True

    connected = await mgr.connect_device(dev_addr)
    assert connected is True
    assert dev.connected is True

    disconnected = await mgr.disconnect_device(dev_addr)
    assert disconnected is True
    assert dev.connected is False

    removed = await mgr.remove_device(dev_addr)
    assert removed is True
    assert mgr.get_device_by_address(dev_addr) is None


@pytest.mark.asyncio
async def test_pairing_window_only_authorizes_the_requested_device():
    """Regression: any device in radio range used to be answered (THREAT-MODEL T3)."""
    mgr = BluetoothManager()
    requested_path = "/org/bluez/hci0/dev_10_22_33_44_55_66"
    other_path = "/org/bluez/hci0/dev_EC_81_93_53_A9_16"

    # Nothing is authorized before the operator asks for a pairing.
    assert mgr.pairing_window_active("10:22:33:44:55:66") is False
    assert mgr.pairing_is_authorized(requested_path) is False
    assert mgr.authorized_pin(requested_path) is None
    assert mgr.confirm_pairing(requested_path, 123456) is False

    await mgr.open_pairing_window("10:22:33:44:55:66", "1234")

    assert mgr.authorized_pin(requested_path) == "1234"
    assert mgr.authorized_passkey(requested_path) == 1234
    assert mgr.confirm_pairing(requested_path, 123456) is True

    # A different device is still refused while the window is open.
    assert mgr.pairing_window_active("ec:81:93:53:a9:16") is False
    assert mgr.pairing_is_authorized(other_path) is False
    assert mgr.authorized_pin(other_path) is None
    assert mgr.confirm_pairing(other_path, 654321) is False

    await mgr.close_pairing_window()
    assert mgr.pairing_is_authorized(requested_path) is False
    assert mgr.authorized_pin(requested_path) is None


@pytest.mark.asyncio
async def test_pairing_window_expires_without_closing_it():
    mgr = BluetoothManager()
    path = "/org/bluez/hci0/dev_10_22_33_44_55_66"

    await mgr.open_pairing_window("10:22:33:44:55:66")
    assert mgr.authorized_pin(path) == "0000"

    mgr._pairing_expires_at = time.monotonic() - 1

    assert mgr.pairing_window_active("10:22:33:44:55:66") is False
    assert mgr.pairing_is_authorized(path) is False
    assert mgr.authorized_pin(path) is None


@pytest.mark.asyncio
async def test_pair_and_trust_holds_the_window_open_and_always_closes_it(monkeypatch):
    mgr = BluetoothManager()
    observed = {}

    async def successful_pair(address):
        observed["open_during_pair"] = mgr.pairing_window_active(address)
        observed["agent_authorized"] = mgr.pairing_is_authorized(
            f"/org/bluez/hci0/dev_{address.replace(':', '_')}"
        )
        return True

    monkeypatch.setattr(mgr, "_pair_and_trust", successful_pair)
    assert await mgr.pair_and_trust("10:22:33:44:55:66", "9999") is True
    assert observed == {"open_during_pair": True, "agent_authorized": True}
    assert mgr.pairing_window_active("10:22:33:44:55:66") is False

    async def failing_pair(address):
        raise ValueError("Device not found")

    monkeypatch.setattr(mgr, "_pair_and_trust", failing_pair)
    with pytest.raises(ValueError, match="Device not found"):
        await mgr.pair_and_trust("10:22:33:44:55:66", "9999")
    assert mgr.pairing_window_active("10:22:33:44:55:66") is False


@pytest.mark.asyncio
async def test_manager_installs_a_deny_by_default_agent():
    """The wired agent must refuse everything outside an operator's window."""
    mgr = BluetoothManager()
    agent = mgr.build_agent()
    path = "/org/bluez/hci0/dev_EC_81_93_53_A9_16"

    assert agent.get_pin(path) is None
    assert agent.confirm_passkey(path, 111111) is False

    await mgr.open_pairing_window("ec:81:93:53:a9:16")

    assert agent.get_pin(path) == "0000"
    assert agent.confirm_passkey(path, 111111) is True


@pytest.mark.asyncio
async def test_connect_refreshes_device_after_stale_bluez_interface():
    mgr = BluetoothManager()
    await mgr.initialize()

    address = "AA:BB:CC:11:22:33"

    class FakeDevice:
        path = "/org/bluez/hci0/dev_AA_BB_CC_11_22_33"
        adapter_name = "hci0"

        def __init__(self, should_fail):
            self.should_fail = should_fail

        async def connect(self):
            if self.should_fail:
                raise RuntimeError("interface not found on this object: org.bluez.Device1")

    stale = FakeDevice(should_fail=True)
    refreshed = FakeDevice(should_fail=False)
    mgr.devices[stale.path] = stale

    async def refresh(_address):
        return refreshed

    mgr.ensure_device = refresh

    assert await mgr.connect_device(address) is True


@pytest.mark.asyncio
async def test_stale_refresh_failure_is_classified_once():
    health = HealthRegistry()
    mgr = BluetoothManager(health_registry=health)
    await mgr.initialize()
    address = "AA:BB:CC:11:22:33"

    class StaleDevice:
        path = "/org/bluez/hci0/dev_AA_BB_CC_11_22_33"
        adapter_name = "hci0"

        async def connect(self):
            raise RuntimeError("stale Device1 proxy")

    stale = StaleDevice()
    mgr.devices[stale.path] = stale
    refresh_calls = 0
    ensure_calls = 0

    async def refresh(_address):
        nonlocal ensure_calls, refresh_calls
        ensure_calls += 1
        if ensure_calls == 1:
            return stale
        refresh_calls += 1
        return None

    mgr.ensure_device = refresh

    with pytest.raises(RuntimeError, match="stale Device1 proxy"):
        await mgr.connect_device(address)

    assert refresh_calls == 1
    speaker = health.snapshot().speakers[address.lower()]
    assert speaker.state == SpeakerState.UNAVAILABLE
    assert speaker.failure.classification == FailureClass.STALE_BLUEZ_OBJECT


def test_dbus_loss_is_not_reported_as_healthy():
    health = HealthRegistry()
    mgr = BluetoothManager(health_registry=health)
    mgr.bus = object()
    mgr.mark_dbus_disconnected("transport closed")

    component = health.snapshot().components["bluetooth"]
    assert mgr.bus is None
    assert component.state == HealthState.UNAVAILABLE
    assert component.failure.classification == FailureClass.DBUS_DISCONNECTED


@pytest.mark.asyncio
async def test_subscribe_signals_registers_bluez_match_rules():
    """The bus daemon must be told to deliver BlueZ signals to this connection.

    dbus-fast's add_message_handler() only routes already-delivered messages and
    installs no match rule, so discovery results would never reach the bridge.
    """
    from dbus_fast import Message, MessageType

    from backend.bl_haos.bluetooth.constants import BLUEZ_SIGNAL_MATCH_RULES

    calls: list[Message] = []

    class FakeBus:
        def __init__(self):
            self.handlers = []

        async def call(self, message):
            calls.append(message)
            return Message(message_type=MessageType.METHOD_RETURN, reply_serial=1)

        def add_message_handler(self, handler):
            self.handlers.append(handler)

    mgr = BluetoothManager()
    mgr.bus = FakeBus()

    await mgr._subscribe_signals()

    assert [c.member for c in calls] == ["AddMatch"] * len(BLUEZ_SIGNAL_MATCH_RULES)
    assert [c.destination for c in calls] == ["org.freedesktop.DBus"] * len(BLUEZ_SIGNAL_MATCH_RULES)
    rules = [c.body[0] for c in calls]
    assert all("sender='org.bluez'" in rule for rule in rules)
    assert any("freedesktop.DBus.ObjectManager" in rule for rule in rules)
    assert any("PropertiesChanged" in rule for rule in rules)
    assert len(mgr.bus.handlers) == 1


@pytest.mark.asyncio
async def test_subscribe_signals_is_idempotent_for_one_bus():
    """Repeated initialization must not pile up duplicate match rules."""
    from dbus_fast import Message, MessageType

    from backend.bl_haos.bluetooth.constants import BLUEZ_SIGNAL_MATCH_RULES

    calls: list[Message] = []

    class FakeBus:
        async def call(self, message):
            calls.append(message)
            return Message(message_type=MessageType.METHOD_RETURN, reply_serial=1)

        def add_message_handler(self, handler):
            pass

    mgr = BluetoothManager()
    mgr.bus = FakeBus()

    await mgr._subscribe_signals()
    await mgr._subscribe_signals()

    assert len(calls) == len(BLUEZ_SIGNAL_MATCH_RULES)


@pytest.mark.asyncio
async def test_subscribe_signals_survives_a_rejected_match_rule():
    """A rejected rule must be logged, not fatal, so the daemon still starts."""
    from dbus_fast import Message, MessageType

    class FailingBus:
        def __init__(self):
            self.handlers = []

        async def call(self, message):
            raise RuntimeError("bus closed")

        def add_message_handler(self, handler):
            self.handlers.append(handler)

    mgr = BluetoothManager()
    mgr.bus = FailingBus()

    await mgr._subscribe_signals()

    assert len(mgr.bus.handlers) == 1
    assert mgr._signal_bus is mgr.bus


@pytest.mark.asyncio
async def test_discovery_signals_populate_the_device_list():
    """An InterfacesAdded signal must surface the device through /api/devices."""
    mgr = BluetoothManager()
    added = []
    mgr.add_event_listener(lambda event, data: added.append(event))

    mgr._handle_dbus_message(
        SimpleNamespace(
            member="InterfacesAdded",
            interface="org.freedesktop.DBus.ObjectManager",
            path="/",
            body=[
                "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_10",
                {
                    DEVICE_INTERFACE: {
                        "Address": "AA:BB:CC:DD:EE:10",
                        "Name": "Living Room Speaker",
                        "Adapter": "/org/bluez/hci0",
                        "UUIDs": [A2DP_SINK_UUID],
                        "Class": 0x240414,
                        "Paired": False,
                        "Trusted": False,
                        "Connected": False,
                    }
                },
            ],
        )
    )

    devices = mgr.get_devices(audio_only=False)
    assert [d.address for d in devices] == ["AA:BB:CC:DD:EE:10"]
    assert added == ["device_discovered"]


@pytest.mark.asyncio
async def test_device_property_signal_updates_the_device_list():
    """PropertiesChanged must refresh the cached device so the UI sees updates."""
    mgr = BluetoothManager()
    dev_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_11"
    mgr._on_interfaces_added(
        dev_path,
        {
            DEVICE_INTERFACE: {
                "Address": "AA:BB:CC:DD:EE:11",
                "Name": "Kitchen Speaker",
                "Adapter": "/org/bluez/hci0",
                "UUIDs": [A2DP_SINK_UUID],
                "Class": 0x240414,
                "Paired": False,
                "Trusted": False,
                "Connected": False,
                "RSSI": -80,
            }
        },
    )
    updated = []
    mgr.add_event_listener(lambda event, data: updated.append(event))

    mgr._handle_dbus_message(
        SimpleNamespace(
            member="PropertiesChanged",
            interface="org.freedesktop.DBus.Properties",
            path=dev_path,
            body=[DEVICE_INTERFACE, {"RSSI": Variant("n", -55)}, []],
        )
    )

    assert mgr.get_devices(audio_only=False)[0].rssi == -55
    assert updated == ["device_updated"]


@pytest.mark.asyncio
async def test_bluetooth_manager_rejects_invalid_addresses_before_lookup():
    mgr = BluetoothManager()
    with pytest.raises(ValueError, match="Invalid Bluetooth address"):
        mgr.get_device_by_address("ff:ff:ff:ff:ff:ff")
    with pytest.raises(ValueError, match="Invalid Bluetooth address"):
        await mgr.ensure_device("AA:BB:CC:DD:EE")


def test_bluetooth_manager_accepts_canonical_and_hyphenated_addresses():
    mgr = BluetoothManager()
    assert mgr.get_device_by_address("AA-BB-CC-DD-EE-FF") is None
    assert mgr.get_device_by_address("aa:bb:cc:dd:ee:ff") is None


def test_bluetooth_manager_device_lookup_tolerates_invalid_cached_device_addresses():
    from backend.bl_haos.bluetooth.device import BluetoothDevice

    mgr = BluetoothManager()
    # Populate cache with devices that have empty or non-MAC addresses (e.g. malformed D-Bus properties or beacons)
    mgr.devices["/org/bluez/hci0/dev_invalid_1"] = BluetoothDevice(None, "/org/bluez/hci0/dev_invalid_1", {})
    mgr.devices["/org/bluez/hci0/dev_invalid_2"] = BluetoothDevice(None, "/org/bluez/hci0/dev_invalid_2", {"Address": "invalid-mac"})
    mgr.devices["/org/bluez/hci0/dev_EC_81_93_53_A9_16"] = BluetoothDevice(
        None, "/org/bluez/hci0/dev_EC_81_93_53_A9_16", {"Address": "EC:81:93:53:A9:16", "Name": "Logitech BT Adapter"}
    )

    found = mgr.get_device_by_address("ec:81:93:53:a9:16")
    assert found is not None
    assert found.name == "Logitech BT Adapter"
