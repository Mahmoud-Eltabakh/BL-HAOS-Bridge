import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from backend.bl_haos.bluetooth.device import BluetoothOperationInProgress
from backend.bl_haos.bluetooth.manager import BluetoothManager
from backend.bl_haos.bluetooth.models import DeviceInfo
from backend.bl_haos.bluetooth.reconnect import AutoReconnectEngine, ReconnectState
from backend.bl_haos.constants import (
    RECONNECT_DISCONNECT_GRACE_SECONDS,
    RECONNECT_FLAP_LIMIT,
    RECONNECT_POST_CONNECT_SETTLE_SECONDS,
)
from backend.bl_haos.health import FailureClass, HealthRegistry, SpeakerState


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
async def test_scheduled_retry_waits_for_the_full_settle_window(monkeypatch):
    engine = AutoReconnectEngine(BluetoothManager())
    address = "AA:BB:CC:11:22:33"
    engine.register_speaker(address)
    profile = engine.profiles[address.lower()]
    attempt = AsyncMock()
    monkeypatch.setattr(engine, "_attempt_reconnect", attempt)
    now = 1000.0
    monkeypatch.setattr(time, "time", lambda: now)

    engine._on_device_event(_device_info(address, connected=True))
    now += 1
    engine._on_device_event(_device_info(address, connected=False))
    now += RECONNECT_DISCONNECT_GRACE_SECONDS + 1
    await engine._tick()
    profile.next_retry_time = now

    now = 1000 + RECONNECT_POST_CONNECT_SETTLE_SECONDS - 0.1
    engine._on_device_event(_device_info(address, connected=False, rssi=-55))
    await engine._tick()
    assert not engine._inflight
    attempt.assert_not_awaited()

    now = 1000 + RECONNECT_POST_CONNECT_SETTLE_SECONDS
    await engine._tick()
    await asyncio.gather(*engine._inflight.values())
    attempt.assert_awaited_once_with(profile)


@pytest.mark.asyncio
async def test_default_breaker_opens_even_when_stale_recovery_keeps_failing(monkeypatch):
    connect = AsyncMock(side_effect=RuntimeError("br-connection-unknown"))
    manager = SimpleNamespace(
        add_event_listener=lambda listener: None,
        get_device_by_address=lambda address: SimpleNamespace(adapter_name="hci0"),
        connect_device=connect,
    )
    health = HealthRegistry()
    engine = AutoReconnectEngine(manager, health_registry=health)
    engine.register_speaker("AA:BB:CC:11:22:33")
    profile = engine.profiles["aa:bb:cc:11:22:33"]
    now = 1000.0
    monkeypatch.setattr(time, "time", lambda: now)

    for _ in range(engine.max_failures):
        await engine._attempt_reconnect(profile)
        if profile.state == ReconnectState.CIRCUIT_BROKEN:
            break

    assert profile.state == ReconnectState.CIRCUIT_BROKEN
    assert profile.circuit_broken_until == now + engine.breaker_cooldown
    speaker = health.snapshot().speakers[profile.address]
    assert speaker.state == SpeakerState.UNAVAILABLE
    assert speaker.failure.classification == FailureClass.RECONNECT_EXHAUSTED
    calls_before = connect.await_count
    now = profile.circuit_broken_until - 0.1
    await engine._tick()
    assert connect.await_count == calls_before
    assert not engine._inflight

    now = profile.circuit_broken_until
    await engine._tick()
    await asyncio.gather(*engine._inflight.values())
    assert connect.await_count == calls_before + 1
    assert profile.state == ReconnectState.BACKOFF
    assert profile.consecutive_failures == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("busy_during_recovery", [False, True])
async def test_busy_reconnect_defers_without_stale_failure(monkeypatch, busy_during_recovery):
    busy = BluetoothOperationInProgress("Bluetooth connection already in progress")
    errors = [RuntimeError("stale proxy"), busy] if busy_during_recovery else [busy]
    connect = AsyncMock(side_effect=errors)
    manager = SimpleNamespace(
        add_event_listener=lambda listener: None,
        get_device_by_address=lambda address: SimpleNamespace(adapter_name="hci0"),
        connect_device=connect,
    )
    health = HealthRegistry()
    engine = AutoReconnectEngine(manager, health_registry=health)
    engine.register_speaker("AA:BB:CC:11:22:33")
    profile = engine.profiles["aa:bb:cc:11:22:33"]
    if busy_during_recovery:
        profile.consecutive_failures = 1
    now = 1000.0
    monkeypatch.setattr(time, "time", lambda: now)

    await engine._attempt_reconnect(profile)

    assert profile.state == ReconnectState.BACKOFF
    assert profile.next_retry_time == now + RECONNECT_POST_CONNECT_SETTLE_SECONDS
    assert profile.consecutive_failures == 0
    assert health.snapshot().speakers[profile.address].failure is None
    assert connect.await_count == (2 if busy_during_recovery else 1)
    now = profile.next_retry_time - 0.1
    await engine._tick()
    assert not engine._inflight


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
    async def mock_fail_connect(addr, *, reset_existing=False):
        raise RuntimeError("Connection refused / Device unavailable")

    mgr.connect_device = mock_fail_connect

    await engine._attempt_reconnect(profile)
    assert profile.consecutive_failures == 1
    assert profile.state == ReconnectState.BACKOFF

    await engine._attempt_reconnect(profile)
    assert profile.consecutive_failures >= 3
    assert profile.state == ReconnectState.BACKOFF

    await engine._attempt_reconnect(profile)
    assert profile.consecutive_failures >= 3
    assert profile.state == ReconnectState.CIRCUIT_BROKEN


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

    async def mock_connect(addr, *, reset_existing=False):
        calls.append(("connect", addr, reset_existing))
        if len([item for item in calls if item[0] == "connect"]) <= 1:
            raise RuntimeError("Device unavailable")

    mgr.connect_device = mock_connect

    await engine._attempt_reconnect(profile)

    assert calls == [
        ("connect", dev_addr.lower(), False),
        ("connect", dev_addr.lower(), True),
    ]
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

    async def connect(_address, *, reset_existing=False):
        nonlocal connect_calls
        connect_calls += 1
        if connect_calls == 1:
            raise RuntimeError("stale proxy")

    manager.connect_device = connect

    await engine._attempt_reconnect(profile)

    speaker = health.snapshot().speakers[address.lower()]
    assert profile.state == ReconnectState.CONNECTED
    assert profile.consecutive_failures == 0
    assert speaker.state == SpeakerState.CONNECTED
    assert speaker.failure is None
    assert profile.connected_at > 0
    assert profile.disconnected_since is None


@pytest.mark.asyncio
async def test_stale_recovery_preserves_pairing_for_a_speaker_not_in_pairing_mode():
    address = "AA:BB:CC:11:22:33"
    state = {"device_present": True, "trusted": True}
    calls: list[tuple[str, bool | None]] = []

    class RecoveryManager:
        def add_event_listener(self, _listener):
            pass

        def get_device_by_address(self, _address):
            if state["device_present"]:
                return type("Device", (), {"adapter_name": "hci0"})()
            return None

        async def remove_device(self, _address):
            calls.append(("remove", None))
            state["device_present"] = False
            state["trusted"] = False

        async def pair_and_trust(self, _address):
            calls.append(("pair", None))
            if not state["device_present"]:
                raise ValueError("Device not discoverable outside pairing mode")
            state["trusted"] = True

        async def connect_device(self, _address, *, reset_existing=False):
            calls.append(("connect", reset_existing))
            if not state["device_present"] or not state["trusted"]:
                raise ValueError("Existing Bluetooth bond was removed")
            if not reset_existing:
                raise RuntimeError("br-connection-unknown")

    manager = RecoveryManager()
    engine = AutoReconnectEngine(manager, max_failures_before_breaker=5)
    engine.register_speaker(address)
    profile = engine.profiles[address.lower()]
    profile.consecutive_failures = 2

    await engine._attempt_reconnect(profile)

    assert state == {"device_present": True, "trusted": True}
    assert calls == [("connect", False), ("connect", True)]
    assert profile.state == ReconnectState.CONNECTED
    assert profile.consecutive_failures == 0


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
