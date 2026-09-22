import pytest
from backend.bl_haos.bluetooth.manager import BluetoothManager
from backend.bl_haos.bluetooth.adapter import BluetoothAdapter
from backend.bl_haos.bluetooth.device import BluetoothDevice
from backend.bl_haos.bluetooth.constants import ADAPTER_INTERFACE, DEVICE_INTERFACE, A2DP_SINK_UUID

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
