import pytest
from backend.bl_haos.bluetooth.constants import A2DP_SINK_UUID
from backend.bl_haos.bluetooth.device import BluetoothDevice


@pytest.mark.asyncio
async def test_bluetooth_device_audio_sink_detection():
    # Audio device with A2DP sink UUID
    props_a2dp = {
        "Address": "11:22:33:44:55:66",
        "Name": "JBL Flip 6",
        "Adapter": "/org/bluez/hci0",
        "UUIDs": [A2DP_SINK_UUID],
        "Class": 0x240414, # Loudspeaker
        "RSSI": -55,
        "Paired": False,
        "Trusted": False,
        "Connected": False,
    }

    dev = BluetoothDevice(bus=None, path="/org/bluez/hci0/dev_11_22_33_44_55_66", properties=props_a2dp)
    info = dev.to_info()

    assert info.address == "11:22:33:44:55:66"
    assert info.name == "JBL Flip 6"
    assert info.is_audio_sink is True
    assert info.rssi == -55
    assert info.adapter_name == "hci0"
    assert info.device_type == "Loudspeaker"

@pytest.mark.asyncio
async def test_bluetooth_device_non_audio_device():
    # Non-audio BLE beacon or sensor
    props_sensor = {
        "Address": "AA:BB:CC:DD:EE:FF",
        "Name": "BLE Thermometer",
        "Adapter": "/org/bluez/hci0",
        "UUIDs": ["0000181a-0000-1000-8000-00805f9b34fb"],
        "Class": None,
        "RSSI": -75,
    }

    dev = BluetoothDevice(bus=None, path="/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", properties=props_sensor)
    info = dev.to_info()

    assert info.is_audio_sink is False
    assert info.device_type == "Bluetooth Device"

@pytest.mark.asyncio
async def test_bluetooth_device_connected_or_named_audio_detection():
    # Logitech BT Adapter or connected Bluetooth speaker
    props_logitech = {
        "Address": "EC:81:93:53:A9:16",
        "Name": "Logitech BT Adapter",
        "Adapter": "/org/bluez/hci0",
        "UUIDs": ["0000110d-0000-1000-8000-00805f9b34fb"],
        "Class": None,
        "Connected": True,
        "Paired": True,
        "Trusted": False,
    }

    dev = BluetoothDevice(bus=None, path="/org/bluez/hci0/dev_EC_81_93_53_A9_16", properties=props_logitech)
    info = dev.to_info()

    assert info.address == "EC:81:93:53:A9:16"
    assert info.is_audio_sink is True
    assert info.connected is True


@pytest.mark.asyncio
async def test_bluetooth_device_actions():
    dev = BluetoothDevice(bus=None, path="/org/bluez/hci0/dev_11_22_33_44_55_66", properties={})
    assert dev.connected is False
    assert dev.paired is False
    assert dev.trusted is False

    await dev.connect()
    assert dev.connected is True

    await dev.disconnect()
    assert dev.connected is False

    await dev.pair()
    assert dev.paired is True

    await dev.set_trusted(True)
    assert dev.trusted is True
