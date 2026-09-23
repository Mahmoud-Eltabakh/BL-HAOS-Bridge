import pytest
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
