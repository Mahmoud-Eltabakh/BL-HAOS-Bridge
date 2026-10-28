import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from dbus_fast import Variant
from backend.bl_haos.bluetooth.constants import (
    A2DP_SINK_UUID,
    ADAPTER_INTERFACE,
    DEVICE_INTERFACE,
)
from backend.bl_haos.bluetooth.device import BluetoothDevice, BluetoothOperationInProgress
from backend.bl_haos.bluetooth.manager import BluetoothManager
from backend.bl_haos.bluetooth.reconnect import AutoReconnectEngine, ReconnectState
from backend.bl_haos.health import FailureClass, HealthRegistry, HealthState, SpeakerState


@pytest.mark.asyncio
@pytest.mark.parametrize("detached", [False, True])
async def test_phase27_review_concurrent_resolution_keeps_canonical_record(monkeypatch, detached):
    manager = BluetoothManager()
    address = "AA:BB:CC:11:22:33"
    path = "/org/bluez/hci0/dev_AA_BB_CC_11_22_33"
    properties = _device_properties(address, paired=True, trusted=True, connected=False)
    if detached:
        offline = BluetoothDevice(None, path, properties.copy())
        offline.mark_detached()
        manager.devices[path] = offline
    entered = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    connecting = asyncio.Event()
    finish = asyncio.Event()
    resolutions = 0

    async def get_all(*args):
        nonlocal resolutions
        index = resolutions
        resolutions += 1
        entered[index].set()
        await release[index].wait()
        return properties.copy()

    async def connect():
        connecting.set()
        await finish.wait()

    interface = SimpleNamespace(call_get_all=get_all, call_connect=AsyncMock(side_effect=connect), call_set=AsyncMock())
    manager.bus = SimpleNamespace(introspect=AsyncMock(), get_proxy_object=lambda *args: SimpleNamespace(get_interface=lambda name: interface))
    manager.adapters["/org/bluez/hci0"] = SimpleNamespace(path="/org/bluez/hci0")
    monkeypatch.setattr(manager, "stop_scan", AsyncMock())
    first = asyncio.create_task(manager.connect_device(address))
    second = None
    try:
        await asyncio.wait_for(entered[0].wait(), 2)
        second = asyncio.create_task(manager.connect_device(address))
        await asyncio.wait_for(entered[1].wait(), 2)
        release[0].set()
        await asyncio.wait_for(connecting.wait(), 2)
        canonical = manager.devices[path]
        release[1].set()
        with pytest.raises(BluetoothOperationInProgress):
            await asyncio.wait_for(second, 2)
        assert manager.devices[path] is canonical
        finish.set()
        assert await asyncio.wait_for(first, 2)
        assert manager.get_device_by_address(address) is canonical
        assert canonical.connected and canonical.paired and canonical.trusted
        assert manager.get_devices()[0].connected
        interface.call_connect.assert_awaited_once()
        assert not manager._connection_locks["hci0"].locked()
    finally:
        for event in release:
            event.set()
        finish.set()
        tasks = [task for task in (first, second) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["stop_scan", "settle", "connect", "trust"])
@pytest.mark.parametrize("replacement", [False, True])
async def test_phase27_review_withdrawn_record_never_succeeds(monkeypatch, stage, replacement):
    manager = BluetoothManager()
    address = "AA:BB:CC:11:22:33"
    path = "/org/bluez/hci0/dev_AA_BB_CC_11_22_33"
    properties = _device_properties(address, paired=True, trusted=True, connected=False)
    device = BluetoothDevice(None, path, properties.copy())
    manager.devices[path] = device
    entered = asyncio.Event()
    release = asyncio.Event()

    async def barrier(boundary):
        if boundary == stage:
            entered.set()
            await release.wait()

    async def stop_scan(*args):
        await barrier("stop_scan")

    async def settle(*args):
        await barrier("settle")

    async def connect():
        await barrier("connect")
        device.update_properties({"Connected": True})

    async def trust(*args):
        await barrier("trust")

    connect_mock = AsyncMock(side_effect=connect)
    monkeypatch.setattr(manager, "stop_scan", stop_scan)
    monkeypatch.setattr("backend.bl_haos.bluetooth.manager.asyncio.sleep", settle)
    monkeypatch.setattr(device, "connect", connect_mock)
    monkeypatch.setattr(device, "set_trusted", AsyncMock(side_effect=trust))
    engine = AutoReconnectEngine(manager)
    engine.register_speaker(address)
    profile = engine.profiles[address.lower()]
    profile.state = ReconnectState.BACKOFF
    profile.consecutive_failures = 3
    profile.backoff_step = 3
    worker = asyncio.create_task(engine._attempt_reconnect(profile))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert manager._connection_locks["hci0"].locked()
        if replacement:
            manager.devices[path] = BluetoothDevice(None, path, properties.copy())
        else:
            manager._on_interfaces_removed(path, [DEVICE_INTERFACE])
        release.set()
        await asyncio.wait_for(worker, 2)
        assert profile.state == ReconnectState.BACKOFF
        assert profile.consecutive_failures == 4
        assert profile.backoff_step == 4
        assert profile.next_retry_time > time.time()
        assert connect_mock.await_count == (0 if stage in ("stop_scan", "settle") else 1)
        assert not manager.devices[path].connected
        assert manager.devices[path].paired and manager.devices[path].trusted
        assert device.paired and device.trusted
        assert not manager._connection_locks["hci0"].locked()
    finally:
        release.set()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("message,detached", [("br-connection-unknown", False), ("interface not found on this object: org.bluez.Device1", True)])
async def test_phase27_connection_one_failure_preserves_bond(monkeypatch, message, detached):
    manager = BluetoothManager()
    device = BluetoothDevice(None, "/org/bluez/hci0/dev_AA_BB_CC_11_22_33", _device_properties("AA:BB:CC:11:22:33", paired=True, trusted=True, connected=False))
    manager.devices[device.path] = device
    connect = AsyncMock(side_effect=RuntimeError(message))
    monkeypatch.setattr(device, "connect", connect)
    with pytest.raises(RuntimeError, match=message):
        await manager.connect_device(device.address)
    assert connect.await_count == 1
    assert manager.devices[device.path] is device
    assert device.detached is detached
    assert device.paired and device.trusted


@pytest.mark.asyncio
@pytest.mark.parametrize("reset_first", [False, True])
async def test_phase27_connection_shared_actual_adapter_ownership(monkeypatch, reset_first):
    manager = BluetoothManager()
    entered = asyncio.Event()
    release = asyncio.Event()
    devices = []
    for suffix, adapter in [("33", "hci0"), ("34", "hci0"), ("35", "hci1")]:
        address = f"AA:BB:CC:11:22:{suffix}"
        properties = _device_properties(address, paired=True, trusted=True, connected=False)
        properties["Adapter"] = f"/org/bluez/{adapter}"
        device = BluetoothDevice(None, f"/org/bluez/{adapter}/dev_{address.replace(':', '_')}", properties)
        manager.devices[device.path] = device
        devices.append(device)

    async def blocked_connect():
        entered.set()
        await release.wait()

    monkeypatch.setattr(devices[0], "connect", blocked_connect)
    stop_scan = AsyncMock()
    monkeypatch.setattr(manager, "stop_scan", stop_scan)
    first = asyncio.create_task(manager.connect_device(devices[0].address, reset_existing=reset_first))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        for contender in devices[:2]:
            with pytest.raises(BluetoothOperationInProgress):
                await asyncio.wait_for(manager.connect_device(contender.address, reset_existing=not reset_first), 0.6)
        assert stop_scan.await_count == 1
        assert await manager.connect_device(devices[2].address)
        lock = manager._connection_locks["hci0"]
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not lock.locked()
        release.set()
        assert await manager.connect_device(devices[0].address)
        assert manager._connection_locks["hci0"] is lock
    finally:
        release.set()
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("block_resolution", [False, True])
async def test_phase27_connection_complete_operation_timeout_releases_ownership(monkeypatch, block_resolution):
    manager = BluetoothManager()
    device = BluetoothDevice(None, "/org/bluez/hci0/dev_AA_BB_CC_11_22_33", _device_properties("AA:BB:CC:11:22:33", paired=True, trusted=True, connected=False))
    manager.devices[device.path] = device
    real_timeout = asyncio.timeout
    deadlines = []
    release = asyncio.Event()

    def bounded_timeout(delay):
        deadlines.append(delay)
        return real_timeout(0.4)

    async def blocked(*args):
        await release.wait()
        return device

    monkeypatch.setattr("backend.bl_haos.bluetooth.manager.asyncio.timeout", bounded_timeout)
    monkeypatch.setattr(manager if block_resolution else device, "ensure_device" if block_resolution else "connect", blocked)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(manager.connect_device(device.address), 0.8)
    assert deadlines == [30.0]
    assert all(not lock.locked() for lock in manager._connection_locks.values())
    release.set()
    assert await manager.connect_device(device.address)


@pytest.mark.asyncio
@pytest.mark.parametrize("busy_on_refresh", [False, True])
async def test_connect_defers_busy_operation_without_retry_or_failure_observation(monkeypatch, busy_on_refresh):
    health = HealthRegistry()
    manager = BluetoothManager(health_registry=health)
    address = "AA:BB:CC:11:22:33"
    path = "/org/bluez/hci0/dev_AA_BB_CC_11_22_33"
    device = BluetoothDevice(None, path, {"Address": address, "Connected": False})
    busy = BluetoothOperationInProgress("Bluetooth connection already in progress")
    errors = [RuntimeError("stale proxy"), busy] if busy_on_refresh else [busy]
    connect = AsyncMock(side_effect=errors)
    monkeypatch.setattr(device, "connect", connect)
    ensure = AsyncMock(return_value=device)
    monkeypatch.setattr(manager, "ensure_device", ensure)
    monkeypatch.setattr(manager, "stop_scan", AsyncMock())
    monkeypatch.setattr("backend.bl_haos.bluetooth.manager.asyncio.sleep", AsyncMock())
    manager.devices[path] = device

    if busy_on_refresh:
        with pytest.raises(RuntimeError, match="stale proxy"):
            await manager.connect_device(address)
    with pytest.raises(BluetoothOperationInProgress):
        await manager.connect_device(address)

    assert connect.await_count == (2 if busy_on_refresh else 1)
    assert ensure.await_count == (2 if busy_on_refresh else 1)
    assert address.lower() not in health.speakers
    if not busy_on_refresh:
        assert manager.devices[path] is device


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
async def test_connect_resolves_stale_bluez_interface_on_next_request(monkeypatch):
    mgr = BluetoothManager()
    await mgr.initialize()

    address = "AA:BB:CC:11:22:33"

    path = "/org/bluez/hci0/dev_AA_BB_CC_11_22_33"
    stale = BluetoothDevice(None, path, _device_properties(address, paired=True, trusted=True, connected=False))
    refreshed = BluetoothDevice(None, path, _device_properties(address, paired=True, trusted=True, connected=False))
    connect = AsyncMock(side_effect=RuntimeError("interface not found on this object: org.bluez.Device1"))
    monkeypatch.setattr(stale, "connect", connect)
    mgr.devices[stale.path] = stale
    with pytest.raises(RuntimeError, match="interface not found"):
        await mgr.connect_device(address)
    assert stale.detached and stale.paired and stale.trusted
    connect.assert_awaited_once()
    interface = SimpleNamespace(call_get_all=AsyncMock(return_value=refreshed._properties))
    mgr.bus = SimpleNamespace(introspect=AsyncMock(), get_proxy_object=lambda *args: SimpleNamespace(get_interface=lambda name: interface))
    mgr.adapters["/org/bluez/hci0"] = SimpleNamespace(path="/org/bluez/hci0")
    monkeypatch.setattr(mgr, "stop_scan", AsyncMock())
    monkeypatch.setattr(BluetoothDevice, "connect", AsyncMock())
    assert await mgr.connect_device(address) is True
    assert not mgr.get_device_by_address(address).detached
    interface.call_get_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_refresh_failure_is_classified_once(monkeypatch):
    health = HealthRegistry()
    mgr = BluetoothManager(health_registry=health)
    await mgr.initialize()
    address = "AA:BB:CC:11:22:33"

    stale = BluetoothDevice(None, "/org/bluez/hci0/dev_AA_BB_CC_11_22_33", _device_properties(address, paired=True, trusted=True, connected=False))
    monkeypatch.setattr(stale, "connect", AsyncMock(side_effect=RuntimeError("stale Device1 proxy")))
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

    assert ensure_calls == 1
    assert refresh_calls == 0
    assert stale.detached and mgr.devices[stale.path] is stale
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


def _device_properties(address: str, *, paired: bool, trusted: bool, connected: bool = True) -> dict:
    """A speaker-shaped BlueZ device record."""
    return {
        "Address": address,
        "Name": "Bluetooth Speaker",
        "Adapter": "/org/bluez/hci0",
        "UUIDs": [A2DP_SINK_UUID],
        "Class": 0x240414,
        "Paired": paired,
        "Trusted": trusted,
        "Connected": connected,
    }


def test_trusted_speaker_stays_visible_when_bluez_withdraws_the_object():
    """A switched-off speaker must stay visible instead of disappearing.

    BlueZ drops the object of any device it treats as temporary, and a device that
    is trusted but not paired is exactly that. The bridge used to delete its own
    record together with it, which emptied the paired list and made the
    integration remove the Home Assistant entity rather than mark it unavailable.
    """
    mgr = BluetoothManager()
    events: list[tuple[str, object]] = []
    mgr.add_event_listener(lambda event, data: events.append((event, data)))
    dev_path = "/org/bluez/hci0/dev_AA_BB_CC_11_22_33"
    dev_addr = "AA:BB:CC:11:22:33"
    mgr._on_interfaces_added(dev_path, {DEVICE_INTERFACE: _device_properties(dev_addr, paired=False, trusted=True)})
    events.clear()

    mgr._on_interfaces_removed(dev_path, [DEVICE_INTERFACE])

    devices = mgr.get_devices(audio_only=True)
    assert [device.address for device in devices] == [dev_addr]
    assert devices[0].trusted is True
    assert devices[0].connected is False
    assert devices[0].detached is True
    # The dashboard keeps the card because the record is updated, not removed.
    assert [event for event, _ in events] == ["device_updated"]


def test_untrusted_device_is_still_dropped_with_its_bluez_object():
    """Radio noise keeps the old behavior: no record once BlueZ forgets it."""
    mgr = BluetoothManager()
    events: list[tuple[str, object]] = []
    mgr.add_event_listener(lambda event, data: events.append((event, data)))
    dev_path = "/org/bluez/hci0/dev_10_22_33_44_55_66"
    mgr._on_interfaces_added(
        dev_path, {DEVICE_INTERFACE: _device_properties("10:22:33:44:55:66", paired=False, trusted=False)}
    )
    events.clear()

    mgr._on_interfaces_removed(dev_path, [DEVICE_INTERFACE])

    assert mgr.get_devices(audio_only=False) == []
    assert [event for event, _ in events] == ["device_removed"]


def test_reappearing_speaker_reattaches_and_drops_the_offline_record():
    """A speaker seen again at a new object path must not be listed twice."""
    mgr = BluetoothManager()
    old_path = "/org/bluez/hci0/dev_AA_BB_CC_11_22_33"
    new_path = "/org/bluez/hci0/dev_AA_BB_CC_11_22_33_recreated"
    mgr._on_interfaces_added(
        old_path, {DEVICE_INTERFACE: _device_properties("AA:BB:CC:11:22:33", paired=True, trusted=True)}
    )
    mgr._on_interfaces_removed(old_path, [DEVICE_INTERFACE])
    assert mgr.get_devices(audio_only=True)[0].detached is True

    # BlueZ recreated the object under a different path: a fresh payload, as D-Bus
    # would deliver it (marking a record detached clears its stale Connected flag).
    mgr._on_interfaces_added(
        new_path, {DEVICE_INTERFACE: _device_properties("AA:BB:CC:11:22:33", paired=True, trusted=True)}
    )

    devices = mgr.get_devices(audio_only=True)
    assert len(devices) == 1
    assert devices[0].detached is False
    assert devices[0].connected is True


@pytest.mark.asyncio
async def test_offline_speaker_can_still_be_forgotten_and_never_connects_through_a_dead_proxy():
    """The offline record survives a failed resolve, and ``remove`` still works."""
    mgr = BluetoothManager()
    dev_path = "/org/bluez/hci0/dev_AA_BB_CC_11_22_33"
    dev_addr = "AA:BB:CC:11:22:33"
    mgr._on_interfaces_added(dev_path, {DEVICE_INTERFACE: _device_properties(dev_addr, paired=True, trusted=True)})
    mgr._on_interfaces_removed(dev_path, [DEVICE_INTERFACE])

    # There is no BlueZ object to connect through, and the offline record must
    # survive that failure so the operator still sees the speaker.
    assert await mgr.ensure_device(dev_addr) is None
    assert mgr.get_devices(audio_only=True)[0].detached is True

    assert await mgr.remove_device(dev_addr) is True
    assert mgr.get_devices(audio_only=False) == []



@pytest.mark.asyncio
async def test_already_connected_speaker_is_not_torn_down():
    """An auto-reconnect must not disconnect a link that is already up."""
    mgr = BluetoothManager()
    dev_path = "/org/bluez/hci0/dev_AA_BB_CC_11_22_33"
    dev_addr = "AA:BB:CC:11:22:33"
    mgr._on_interfaces_added(
        dev_path, {DEVICE_INTERFACE: _device_properties(dev_addr, paired=True, trusted=True, connected=True)}
    )
    dev = mgr.get_device_by_address(dev_addr)

    assert await mgr.connect_device(dev_addr) is True

    # The same live record, still connected: nothing was reset or re-created.
    assert mgr.get_device_by_address(dev_addr) is dev
    assert dev.connected is True


@pytest.mark.asyncio
async def test_operator_connect_still_resets_an_existing_link():
    """The operator path keeps the stale-transport reset it exists for."""
    mgr = BluetoothManager()
    dev_path = "/org/bluez/hci0/dev_AA_BB_CC_11_22_33"
    dev_addr = "AA:BB:CC:11:22:33"
    mgr._on_interfaces_added(
        dev_path, {DEVICE_INTERFACE: _device_properties(dev_addr, paired=True, trusted=True, connected=True)}
    )
    dev = mgr.get_device_by_address(dev_addr)
    resets: list[str] = []
    original_disconnect = dev.disconnect

    async def _record_disconnect():
        resets.append(dev.address)
        await original_disconnect()

    dev.disconnect = _record_disconnect

    assert await mgr.connect_device(dev_addr, reset_existing=True) is True
    assert resets == [dev_addr], "the operator reset must drop the stale link first"
    assert dev.connected is True
