import time
import pytest
import asyncio
from backend.bl_haos.bluetooth.manager import BluetoothManager
from backend.bl_haos.bluetooth.models import DeviceInfo
from backend.bl_haos.bluetooth.reconnect import AutoReconnectEngine, ReconnectState

@pytest.mark.asyncio
async def test_reconnect_state_machine():
    mgr = BluetoothManager()
    await mgr.initialize()

    engine = AutoReconnectEngine(mgr, initial_backoff=1.0, backoff_multiplier=2.0)
    dev_addr = "AA:BB:CC:11:22:33"

    engine.register_speaker(dev_addr)
    profile = engine.profiles[dev_addr.lower()]
    assert profile.state == ReconnectState.IDLE

    # Simulate device connected
    dev_info_connected = DeviceInfo(
        path="/org/bluez/hci0/dev_AA_BB_CC_11_22_33",
        adapter_path="/org/bluez/hci0",
        address=dev_addr,
        connected=True,
        trusted=True,
        is_audio_sink=True,
    )
    engine._running = True
    engine._on_device_event(dev_info_connected)
    assert profile.state == ReconnectState.CONNECTED

    # Simulate device disconnected
    dev_info_disconnected = DeviceInfo(
        path="/org/bluez/hci0/dev_AA_BB_CC_11_22_33",
        adapter_path="/org/bluez/hci0",
        address=dev_addr,
        connected=False,
        trusted=True,
        is_audio_sink=True,
    )
    engine._on_device_event(dev_info_disconnected)
    assert profile.state == ReconnectState.BACKOFF
    assert profile.next_retry_time > time.time()

    # Fast-track check when RSSI presence is advertised
    old_retry_time = profile.next_retry_time
    dev_info_advertised = DeviceInfo(
        path="/org/bluez/hci0/dev_AA_BB_CC_11_22_33",
        adapter_path="/org/bluez/hci0",
        address=dev_addr,
        connected=False,
        trusted=True,
        is_audio_sink=True,
        rssi=-58,
    )
    engine._on_device_event(dev_info_advertised)
    assert profile.next_retry_time <= time.time()

@pytest.mark.asyncio
async def test_circuit_breaker_and_locking():
    mgr = BluetoothManager()
    await mgr.initialize()

    dev_addr = "AA:BB:CC:11:22:33"
    mgr._on_interfaces_added("/org/bluez/hci0/dev_AA_BB_CC_11_22_33", {
        "org.bluez.Device1": {
            "Address": dev_addr,
            "Name": "Speaker",
            "Adapter": "/org/bluez/hci0",
            "UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb"],
            "Class": 0x240414,
            "Connected": False,
        }
    })

    engine = AutoReconnectEngine(
        mgr,
        initial_backoff=0.1,
        max_failures_before_breaker=3,
        circuit_breaker_cooldown=2.0
    )

    engine.register_speaker(dev_addr)
    profile = engine.profiles[dev_addr.lower()]

    # Simulate connect failure
    async def mock_fail_connect(addr):
        raise RuntimeError("Connection refused / Device unavailable")

    mgr.connect_device = mock_fail_connect

    # Trigger 3 failures
    await engine._attempt_reconnect(profile)
    assert profile.consecutive_failures == 1
    assert profile.state == ReconnectState.BACKOFF

    await engine._attempt_reconnect(profile)
    assert profile.consecutive_failures == 2
    assert profile.state == ReconnectState.BACKOFF

    await engine._attempt_reconnect(profile)
    assert profile.consecutive_failures == 3
    assert profile.state == ReconnectState.CIRCUIT_BROKEN
    assert profile.circuit_broken_until > time.time()


def test_trusted_audio_sink_can_be_registered_at_startup():
    manager = BluetoothManager()
    engine = AutoReconnectEngine(manager)
    device = DeviceInfo(
        path="/org/bluez/hci0/dev_AA_BB_CC_11_22_33",
        adapter_path="/org/bluez/hci0",
        address="AA:BB:CC:11:22:33",
        trusted=True,
        connected=True,
        is_audio_sink=True,
    )

    engine.register_speaker(device.address)

    assert device.address.lower() in engine.profiles
