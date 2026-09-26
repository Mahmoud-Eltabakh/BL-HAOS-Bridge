import asyncio
import time

import pytest
from backend.bl_haos.bluetooth.manager import BluetoothManager
from backend.bl_haos.bluetooth.models import DeviceInfo
from backend.bl_haos.bluetooth.reconnect import AutoReconnectEngine, ReconnectState
from backend.bl_haos.constants import (
    RECONNECT_DISCONNECT_GRACE_SECONDS,
    RECONNECT_FLAP_LIMIT,
    RECONNECT_POST_CONNECT_SETTLE_SECONDS,
)
from backend.bl_haos.health import HealthRegistry, SpeakerState


def _device_info(address: str, *, connected: bool, rssi: int | None = None) -> DeviceInfo:
    """A BlueZ-shaped device record for the engine's event handler."""
    return DeviceInfo(
        path=f"/org/bluez/hci0/dev_{address.replace(':', '_')}",
        adapter_path="/org/bluez/hci0",
        address=address,
        connected=connected,
        trusted=True,
        is_audio_sink=True,
        rssi=rssi,
    )


@pytest.mark.asyncio
async def test_reconnect_state_machine():
    """A dropout is acted on only after the grace window; a flap is not acted on."""
    mgr = BluetoothManager()
    await mgr.initialize()

    engine = AutoReconnectEngine(mgr, initial_backoff=1.0, backoff_multiplier=2.0)
    dev_addr = "AA:BB:CC:11:22:33"

    engine.register_speaker(dev_addr)
    profile = engine.profiles[dev_addr.lower()]
    assert profile.state == ReconnectState.IDLE

    engine._running = True
    engine._on_device_event(_device_info(dev_addr, connected=True))
    assert profile.state == ReconnectState.CONNECTED

    # A dropout is not believed immediately: acting on one mis-reported flag is what
    # used to start a reconnect cycle against a link that was fine.
    engine._on_device_event(_device_info(dev_addr, connected=False))
    assert profile.state == ReconnectState.CONNECTED
    assert profile.disconnected_since is not None
    await engine._tick()
    assert profile.state == ReconnectState.CONNECTED, "the grace window has not elapsed yet"

    # It is back inside the window, so the drop was a flap: nothing to reconnect, and
    # the counter is what makes it visible to an operator.
    engine._on_device_event(_device_info(dev_addr, connected=True))
    assert profile.disconnected_since is None
    assert profile.suppressed_flaps == 1
    assert profile.state == ReconnectState.CONNECTED

    # This dropout survives the window, so the backoff is armed.
    engine._on_device_event(_device_info(dev_addr, connected=False))
    profile.disconnected_since = time.time() - RECONNECT_DISCONNECT_GRACE_SECONDS - 1
    profile.connected_at = time.time() - RECONNECT_POST_CONNECT_SETTLE_SECONDS - 1
    await engine._tick()
    assert profile.state == ReconnectState.BACKOFF
    assert profile.next_retry_time > time.time()

    # An advertised RSSI pulls a pending retry forward, once the link has settled.
    engine._on_device_event(_device_info(dev_addr, connected=False, rssi=-58))
    assert profile.next_retry_time <= time.time()


@pytest.mark.asyncio
async def test_presence_does_not_fast_track_while_the_link_settles():
    """A reconnect is not pulled forward while a link is still coming up."""
    mgr = BluetoothManager()
    await mgr.initialize()
    engine = AutoReconnectEngine(mgr, initial_backoff=30.0, backoff_multiplier=2.0)
    dev_addr = "AA:BB:CC:11:22:33"
    engine.register_speaker(dev_addr)
    profile = engine.profiles[dev_addr.lower()]
    engine._running = True

    engine._on_device_event(_device_info(dev_addr, connected=True, rssi=-55))
    engine._on_device_event(_device_info(dev_addr, connected=False))
    profile.disconnected_since = time.time() - RECONNECT_DISCONNECT_GRACE_SECONDS - 1
    await engine._tick()
    assert profile.state == ReconnectState.BACKOFF
    retry_time = profile.next_retry_time

    # The speaker advertises while the link has only just come up: the retry stays
    # where it was instead of hammering Connect during the A2DP negotiation.
    engine._on_device_event(_device_info(dev_addr, connected=False, rssi=-58))
    assert profile.next_retry_time == retry_time


@pytest.mark.asyncio
async def test_repeated_flaps_earn_a_cooldown_instead_of_a_retry_loop(monkeypatch):
    """A marginal link is left alone instead of being reconnected in a loop."""
    mgr = BluetoothManager()
    await mgr.initialize()
    health = HealthRegistry()
    engine = AutoReconnectEngine(mgr, initial_backoff=0.1, health_registry=health)
    dev_addr = "AA:BB:CC:11:22:33"
    engine.register_speaker(dev_addr)
    profile = engine.profiles[dev_addr.lower()]
    engine._running = True

    attempts: list[str] = []

    async def _record_attempt(target):
        attempts.append(target.address)

    monkeypatch.setattr(engine, "_attempt_reconnect", _record_attempt)

    for _ in range(RECONNECT_FLAP_LIMIT):
        # One flap cycle: the dropout survives the grace window (so it counts), the
        # speaker comes back, and the next dropout starts a fresh window.
        engine._on_device_event(_device_info(dev_addr, connected=False))
        profile.disconnected_since = time.time() - RECONNECT_DISCONNECT_GRACE_SECONDS - 1
        await engine._tick()
        engine._on_device_event(_device_info(dev_addr, connected=True))

    assert profile.cooldown_until > time.time(), "the flap limit must set a cooldown"
    attempts_before = len(attempts)
    await engine._tick()
    assert len(attempts) == attempts_before, "the cooldown must hold reconnects"
    assert health.speakers[dev_addr.lower()].suppressed_flaps >= 1

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

    # The reconnect engine now self-heals once a stale device is detected, so
    # the second failure triggers recovery and the breaker is reached only after
    # the recovery path itself is exhausted.
    await engine._attempt_reconnect(profile)
    assert profile.consecutive_failures == 1
    assert profile.state == ReconnectState.BACKOFF

    await engine._attempt_reconnect(profile)
    assert profile.consecutive_failures >= 3
    assert profile.state == ReconnectState.BACKOFF

    await engine._attempt_reconnect(profile)
    assert profile.consecutive_failures >= 3
    assert profile.state in (ReconnectState.BACKOFF, ReconnectState.CIRCUIT_BROKEN)


@pytest.mark.asyncio
async def test_reconnect_recovers_stale_device_after_repeated_failures():
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
            "Trusted": True,
        }
    })

    engine = AutoReconnectEngine(mgr, initial_backoff=0.1, max_failures_before_breaker=5)
    engine.register_speaker(dev_addr)
    profile = engine.profiles[dev_addr.lower()]
    profile.consecutive_failures = 2

    calls = []

    async def mock_remove(addr):
        calls.append(("remove", addr))

    async def mock_pair(addr):
        calls.append(("pair", addr))

    async def mock_connect(addr):
        calls.append(("connect", addr))
        if len([item for item in calls if item[0] == "connect"]) <= 1:
            raise RuntimeError("Device unavailable")

    mgr.remove_device = mock_remove
    mgr.pair_and_trust = mock_pair
    mgr.connect_device = mock_connect

    await engine._attempt_reconnect(profile)

    assert ("remove", dev_addr.lower()) in calls
    assert ("pair", dev_addr.lower()) in calls
    assert ("connect", dev_addr.lower()) in calls
    assert profile.state == ReconnectState.CONNECTED
    assert profile.consecutive_failures == 0


@pytest.mark.asyncio
async def test_successful_stale_recovery_publishes_connected_without_old_failure():
    manager = BluetoothManager()
    await manager.initialize()
    address = "AA:BB:CC:11:22:33"
    manager._on_interfaces_added("/org/bluez/hci0/dev_AA_BB_CC_11_22_33", {
        "org.bluez.Device1": {
            "Address": address,
            "Name": "Speaker",
            "Adapter": "/org/bluez/hci0",
            "UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb"],
            "Connected": False,
        }
    })
    health = HealthRegistry()
    engine = AutoReconnectEngine(manager, health_registry=health, max_failures_before_breaker=5)
    engine.register_speaker(address)
    profile = engine.profiles[address.lower()]
    profile.consecutive_failures = 2

    connect_calls = 0

    async def connect(_address):
        nonlocal connect_calls
        connect_calls += 1
        if connect_calls == 1:
            raise RuntimeError("stale proxy")

    manager.remove_device = lambda _address: asyncio.sleep(0)
    manager.pair_and_trust = lambda _address: asyncio.sleep(0)
    manager.connect_device = connect

    await engine._attempt_reconnect(profile)

    speaker = health.snapshot().speakers[address.lower()]
    assert profile.state == ReconnectState.CONNECTED
    assert profile.consecutive_failures == 0
    assert speaker.state == SpeakerState.CONNECTED
    assert speaker.failure is None


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
