import pytest
from dbus_fast import Variant
from backend.bl_haos.bluetooth.adapter import BluetoothAdapter

@pytest.mark.asyncio
async def test_bluetooth_adapter_properties():
    raw_props = {
        "Address": "00:1A:7D:DA:71:13",
        "Name": "BlueZ 5.66 Adapter",
        "Alias": "Living Room Adapter",
        "Powered": Variant("b", True),
        "Discovering": Variant("b", False),
        "Discoverable": Variant("b", True),
        "Pairable": Variant("b", True),
        "Class": Variant("u", 0x000400),
        "UUIDs": Variant("as", ["0000110b-0000-1000-8000-00805f9b34fb"]),
    }

    adapter = BluetoothAdapter(bus=None, path="/org/bluez/hci0", properties=raw_props)
    info = adapter.to_info()

    assert info.interface == "hci0"
    assert info.address == "00:1A:7D:DA:71:13"
    assert info.alias == "Living Room Adapter"
    assert info.powered is True
    assert info.discovering is False
    assert info.discoverable is True
    assert info.pairable is True

@pytest.mark.asyncio
async def test_bluetooth_adapter_state_changes():
    adapter = BluetoothAdapter(bus=None, path="/org/bluez/hci1", properties={})
    assert adapter.interface == "hci1"
    assert adapter.powered is False

    await adapter.set_power(True)
    assert adapter.powered is True

    await adapter.start_discovery()
    assert adapter.discovering is True

    await adapter.stop_discovery()
    assert adapter.discovering is False
