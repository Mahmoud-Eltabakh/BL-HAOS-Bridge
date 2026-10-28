import asyncio
import math
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from backend.bl_haos.bluetooth.device import BluetoothDevice, BluetoothOperationInProgress
from backend.bl_haos.bluetooth.manager import BluetoothManager
from backend.bl_haos.bluetooth.models import DeviceInfo
from backend.bl_haos.bluetooth.reconnect import AutoReconnectEngine, ReconnectState
from backend.bl_haos.constants import (
    RECONNECT_DISCONNECT_GRACE_SECONDS,
    RECONNECT_FLAP_LIMIT,
    RECONNECT_POST_CONNECT_SETTLE_SECONDS,
)
from backend.bl_haos.health import FailureClass, HealthRegistry, SpeakerState


@pytest.mark.parametrize("initial", [0.0, 1e-320])
def test_phase27_review_backoff_scaled_exponential_at_1024(monkeypatch, initial):
    engine = AutoReconnectEngine(BluetoothManager(), initial_backoff=initial)
    monkeypatch.setattr("backend.bl_haos.bluetooth.reconnect.random.uniform", lambda *args: 0)
    try:
        delay = engine._calculate_backoff_delay(1024)
    except OverflowError as error:
        pytest.fail(f"Bounded scaled backoff must not overflow: {error}")
    expected = math.ldexp(initial, 1024)
    assert math.isfinite(delay)
    assert delay == pytest.approx(expected, rel=1e-12, abs=0)
    assert engine._calculate_backoff_delay(1000000) == (0 if initial == 0 else 120)


@pytest.mark.parametrize("options", [
    {"initial_backoff": -1},
    {"initial_backoff": float("nan")},
    {"initial_backoff": float("inf")},
    {"backoff_multiplier": 0},
    {"backoff_multiplier": 0.5},
    {"backoff_multiplier": float("nan")},
    {"backoff_multiplier": float("inf")},
    {"max_backoff": 0},
    {"max_backoff": -1},
    {"max_backoff": float("nan")},
    {"max_backoff": float("inf")},
])
def test_phase27_review_backoff_rejects_unsupported_domain(options):
    with pytest.raises(ValueError):
        AutoReconnectEngine(BluetoothManager(), **options)


@pytest.mark.parametrize("initial,multiplier,cap,expected", [
    (0, 1, 120, 0),
    (1e-320, 1, 120, 1e-320),
    (200, 2, 120, 120),
    (10, 2, 5, 5),
])
def test_phase27_review_backoff_supported_boundaries(monkeypatch, initial, multiplier, cap, expected):
    engine = AutoReconnectEngine(BluetoothManager(), initial_backoff=initial, backoff_multiplier=multiplier, max_backoff=cap)
    monkeypatch.setattr("backend.bl_haos.bluetooth.reconnect.random.uniform", lambda *args: 0)
    assert engine._calculate_backoff_delay(1024) == expected


@pytest.mark.asyncio
async def test_phase27_connection_scheduled_tick_has_one_bluez_attempt():
    interface = SimpleNamespace(call_connect=AsyncMock(side_effect=RuntimeError("br-connection-unknown")), call_connect_profile=AsyncMock())
    bus = SimpleNamespace(introspect=AsyncMock(), get_proxy_object=lambda *args: SimpleNamespace(get_interface=lambda name: interface))
    manager = BluetoothManager()
    address = "AA:BB:CC:11:22:33"
    device = BluetoothDevice(bus, "/org/bluez/hci0/dev_AA_BB_CC_11_22_33", {"Address": address, "Adapter": "/org/bluez/hci0", "Paired": True, "Trusted": True})
    manager.devices[device.path] = device
    engine = AutoReconnectEngine(manager)
    engine.register_speaker(address)
    profile = engine.profiles[address.lower()]
    profile.state = ReconnectState.BACKOFF
    await engine._tick()
    await asyncio.gather(*engine._inflight.values())
    interface.call_connect_profile.assert_not_awaited()
    interface.call_connect.assert_awaited_once()
    assert profile.consecutive_failures == 1
    assert not device.connected
    assert manager.devices[device.path] is device


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


def _phase27_pacing_engine():
    connect = AsyncMock(side_effect=RuntimeError("br-connection-unknown"))
    manager = SimpleNamespace(
        add_event_listener=lambda listener: None,
        get_device_by_address=lambda address: SimpleNamespace(adapter_name="hci0"),
        connect_device=connect,
    )
    engine = AutoReconnectEngine(manager, health_registry=HealthRegistry())
    engine.register_speaker("AA:BB:CC:11:22:33")
    return engine, engine.profiles["aa:bb:cc:11:22:33"], connect


@pytest.mark.parametrize("jitter", [0.0, 0.15])
def test_phase27_pacing_defaults_saturate_without_negative_jitter(monkeypatch, jitter):
    engine, profile, connect = _phase27_pacing_engine()
    ranges = []

    def uniform(lower, upper):
        ranges.append((lower, upper))
        return jitter

    monkeypatch.setattr("backend.bl_haos.bluetooth.reconnect.random.uniform", uniform)
    assert engine.initial_backoff == 10.0
    assert engine.backoff_multiplier == 2.0
    assert engine.max_backoff == 120.0
    assert engine.breaker_cooldown == 300.0
    assert engine.max_failures == 8
    delays = [engine._calculate_backoff_delay(step) for step in [0, 1, 2, 3, 4, 1000000]]
    assert delays == [min(120.0, nominal * (1 + jitter)) for nominal in [10, 20, 40, 80, 120, 120]]
    assert all(bounds == (0, 0.15) for bounds in ranges)


@pytest.mark.asyncio
async def test_phase27_pacing_scheduled_failures_never_reset_or_amplify(monkeypatch):
    engine, profile, connect = _phase27_pacing_engine()
    now = 1000.0
    monkeypatch.setattr(time, "time", lambda: now)
    monkeypatch.setattr("backend.bl_haos.bluetooth.reconnect.random.uniform", lambda *args: 0)
    engine._arm_backoff(profile, now)
    assert profile.next_retry_time == now + 10
    for failure, delay in enumerate([20, 40, 80, 120, 120, 120, 120], 1):
        now = profile.next_retry_time
        await engine._tick()
        await asyncio.gather(*engine._inflight.values())
        assert connect.await_count == failure
        assert all(not call.kwargs.get("reset_existing", False) for call in connect.await_args_list)
        assert profile.consecutive_failures == failure
        assert profile.backoff_step == failure
        assert profile.next_retry_time == now + delay
        await engine._tick()
        assert not engine._inflight


@pytest.mark.asyncio
async def test_phase27_pacing_busy_and_presence_retain_history_and_later_deadlines(monkeypatch):
    engine, profile, connect = _phase27_pacing_engine()
    now = 1000.0
    monkeypatch.setattr(time, "time", lambda: now)
    monkeypatch.setattr("backend.bl_haos.bluetooth.reconnect.random.uniform", lambda *args: 0)
    profile.state = ReconnectState.BACKOFF
    profile.consecutive_failures = 5
    profile.backoff_step = 5
    profile.next_retry_time = now + 500
    for _ in range(5):
        engine._on_device_event(_device_info(profile.address, connected=False, rssi=-50))
    assert profile.next_retry_time == now + 500
    connect.side_effect = BluetoothOperationInProgress("busy")
    await engine._attempt_reconnect(profile)
    assert (profile.consecutive_failures, profile.backoff_step) == (5, 5)
    assert profile.next_retry_time == now + 500
    assert engine.health.snapshot().speakers[profile.address].failure is None
    profile.next_retry_time = now
    await engine._attempt_reconnect(profile)
    assert profile.next_retry_time == now + 120
    profile.backoff_step = 0
    profile.cooldown_until = now + 200
    await engine._attempt_reconnect(profile)
    assert profile.next_retry_time == now + 200


@pytest.mark.asyncio
async def test_phase27_pacing_breaker_half_open_retains_history_until_success(monkeypatch):
    engine, profile, connect = _phase27_pacing_engine()
    now = 1000.0
    monkeypatch.setattr(time, "time", lambda: now)
    monkeypatch.setattr("backend.bl_haos.bluetooth.reconnect.random.uniform", lambda *args: 0)
    profile.state = ReconnectState.BACKOFF
    for failure in range(1, 9):
        now = max(now, profile.next_retry_time)
        await engine._tick()
        await asyncio.gather(*engine._inflight.values())
        assert connect.await_count == failure
    assert profile.state == ReconnectState.CIRCUIT_BROKEN
    assert profile.circuit_broken_until == now + 300
    deadline = profile.circuit_broken_until
    connect.side_effect = BluetoothOperationInProgress("busy")
    await engine._attempt_reconnect(profile)
    assert connect.await_count == 8
    profile.state = ReconnectState.BACKOFF
    profile.next_retry_time = now
    engine._on_device_event(_device_info(profile.address, connected=False, rssi=-50))
    now = deadline - 0.1
    await engine._tick()
    assert not engine._inflight
    assert profile.consecutive_failures == 8
    connect.side_effect = RuntimeError("still offline")
    now = deadline
    await engine._tick()
    await engine._tick()
    await asyncio.gather(*engine._inflight.values())
    assert connect.await_count == 9
    assert profile.consecutive_failures == 9
    assert profile.backoff_step == 9
    assert profile.state == ReconnectState.CIRCUIT_BROKEN
    assert profile.circuit_broken_until == now + 300
    now = profile.circuit_broken_until
    connect.side_effect = None
    await engine._tick()
    await asyncio.gather(*engine._inflight.values())
    assert connect.await_count == 10
    assert profile.state == ReconnectState.CONNECTED
    assert (profile.consecutive_failures, profile.backoff_step, profile.circuit_broken_until, profile.next_retry_time) == (0, 0, 0, 0)
    assert engine.health.snapshot().speakers[profile.address].failure is None


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", ["stop", "unregister"])
async def test_phase27_pacing_cleanup_releases_real_manager_lock(cleanup):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked():
        entered.set()
        await release.wait()

    interface = SimpleNamespace(call_connect=AsyncMock(side_effect=blocked), call_set=AsyncMock())
    bus = SimpleNamespace(introspect=AsyncMock(), get_proxy_object=lambda *args: SimpleNamespace(get_interface=lambda name: interface))
    manager = BluetoothManager()
    address = "AA:BB:CC:11:22:33"
    device = BluetoothDevice(bus, "/org/bluez/hci0/dev_AA_BB_CC_11_22_33", {"Address": address, "Adapter": "/org/bluez/hci0", "Paired": True})
    manager.devices[device.path] = device
    engine = AutoReconnectEngine(manager)
    engine.register_speaker(address, preferred_adapter="hci1")
    profile = engine.profiles[address.lower()]
    profile.state = ReconnectState.BACKOFF
    await engine._tick()
    workers = list(engine._inflight.values())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert manager._connection_locks["hci0"].locked()
        with pytest.raises(BluetoothOperationInProgress):
            await manager.connect_device(address, reset_existing=True)
        if cleanup == "stop":
            await engine.stop()
            assert profile.state == ReconnectState.BACKOFF
            assert profile.next_retry_time > time.time()
        else:
            engine.unregister_speaker(address)
            engine.register_speaker(address)
            replacement = engine.profiles[address.lower()]
            await asyncio.gather(*workers, return_exceptions=True)
            assert engine.profiles[address.lower()] is replacement
            assert replacement.state == ReconnectState.IDLE
        assert not manager._connection_locks["hci0"].locked()
        assert not device.connected
        release.set()
        assert await manager.connect_device(address)
    finally:
        release.set()
        await engine.stop()
        await asyncio.gather(*workers, return_exceptions=True)


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

    retry_time = profile.next_retry_time
    engine._on_device_event(_device_info(dev_addr, connected=False, rssi=-58))
    assert profile.next_retry_time == retry_time


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
    assert profile.state == ReconnectState.CIRCUIT_BROKEN
    assert profile.consecutive_failures == engine.max_failures + 1
    assert profile.circuit_broken_until == now + engine.breaker_cooldown


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_failures", [0, 4])
async def test_busy_reconnect_defers_without_stale_failure(monkeypatch, prior_failures):
    busy = BluetoothOperationInProgress("Bluetooth connection already in progress")
    connect = AsyncMock(side_effect=busy)
    manager = SimpleNamespace(
        add_event_listener=lambda listener: None,
        get_device_by_address=lambda address: SimpleNamespace(adapter_name="hci0"),
        connect_device=connect,
    )
    health = HealthRegistry()
    engine = AutoReconnectEngine(manager, health_registry=health)
    engine.register_speaker("AA:BB:CC:11:22:33")
    profile = engine.profiles["aa:bb:cc:11:22:33"]
    profile.consecutive_failures = prior_failures
    profile.backoff_step = prior_failures
    now = 1000.0
    monkeypatch.setattr(time, "time", lambda: now)
    monkeypatch.setattr("backend.bl_haos.bluetooth.reconnect.random.uniform", lambda *args: 0)

    await engine._attempt_reconnect(profile)

    assert profile.state == ReconnectState.BACKOFF
    assert profile.next_retry_time == now + max(RECONNECT_POST_CONNECT_SETTLE_SECONDS, min(120, 10 * 2 ** prior_failures))
    assert profile.consecutive_failures == prior_failures
    assert profile.backoff_step == prior_failures
    assert health.snapshot().speakers[profile.address].failure is None
    assert connect.await_count == 1
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
    assert profile.consecutive_failures == 2
    assert profile.state == ReconnectState.BACKOFF

    await engine._attempt_reconnect(profile)
    assert profile.consecutive_failures == 3
    assert profile.state == ReconnectState.CIRCUIT_BROKEN


@pytest.mark.asyncio
async def test_reconnect_recovers_on_next_scheduled_attempt_without_reset(monkeypatch):
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
    profile.backoff_step = 2
    now = 1000.0
    monkeypatch.setattr(time, "time", lambda: now)
    monkeypatch.setattr("backend.bl_haos.bluetooth.reconnect.random.uniform", lambda *args: 0)

    calls = []

    async def mock_connect(addr, *, reset_existing=False):
        calls.append(("connect", addr, reset_existing))
        if len([item for item in calls if item[0] == "connect"]) <= 1:
            raise RuntimeError("Device unavailable")

    mgr.connect_device = mock_connect

    await engine._attempt_reconnect(profile)

    assert calls == [("connect", dev_addr.lower(), False)]
    assert profile.consecutive_failures == 3
    assert profile.next_retry_time == now + 0.8
    now = profile.next_retry_time
    await engine._tick()
    await asyncio.gather(*engine._inflight.values())
    assert calls == [
        ("connect", dev_addr.lower(), False),
        ("connect", dev_addr.lower(), False),
    ]
    assert profile.state == ReconnectState.CONNECTED
    assert profile.consecutive_failures == 0


@pytest.mark.asyncio
async def test_successful_scheduled_recovery_publishes_connected_without_old_failure(monkeypatch):
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
    now = 1000.0
    monkeypatch.setattr(time, "time", lambda: now)

    connect_calls = 0

    async def connect(_address, *, reset_existing=False):
        nonlocal connect_calls
        connect_calls += 1
        if connect_calls == 1:
            raise RuntimeError("stale proxy")

    manager.connect_device = connect

    await engine._attempt_reconnect(profile)

    assert connect_calls == 1
    assert profile.state == ReconnectState.BACKOFF
    health.observe_speaker(address.lower(), SpeakerState.UNAVAILABLE, failure=FailureClass.STALE_BLUEZ_OBJECT)
    now = profile.next_retry_time
    await engine._tick()
    await asyncio.gather(*engine._inflight.values())
    assert connect_calls == 2
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
    assert calls == [("connect", False)]
    assert profile.state == ReconnectState.BACKOFF
    assert profile.consecutive_failures == 3


@pytest.mark.asyncio
async def test_phase27_pacing_manual_owner_defers_automatic_without_erasing_history(monkeypatch):
    manager = BluetoothManager()
    address = "AA:BB:CC:11:22:33"
    device = BluetoothDevice(None, "/org/bluez/hci0/dev_AA_BB_CC_11_22_33", {"Address": address, "Adapter": "/org/bluez/hci0", "Paired": True})
    manager.devices[device.path] = device
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked():
        entered.set()
        await release.wait()

    monkeypatch.setattr(device, "connect", blocked)
    engine = AutoReconnectEngine(manager)
    engine.register_speaker(address, preferred_adapter="hci1")
    profile = engine.profiles[address.lower()]
    profile.state = ReconnectState.BACKOFF
    profile.consecutive_failures = 4
    profile.backoff_step = 4
    now = 1000.0
    monkeypatch.setattr(time, "time", lambda: now)
    monkeypatch.setattr("backend.bl_haos.bluetooth.reconnect.random.uniform", lambda *args: 0)
    manual = asyncio.create_task(manager.connect_device(address, reset_existing=True))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await engine._tick()
        await asyncio.gather(*engine._inflight.values())
        assert (profile.consecutive_failures, profile.backoff_step) == (4, 4)
        assert profile.next_retry_time == now + 120
        assert profile.state == ReconnectState.BACKOFF
        assert manager._connection_locks["hci0"].locked()
    finally:
        manual.cancel()
        await asyncio.gather(manual, return_exceptions=True)
        await engine.stop()
    assert not manager._connection_locks["hci0"].locked()


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
