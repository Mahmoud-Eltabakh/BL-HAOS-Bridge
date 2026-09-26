import pytest
from backend.bl_haos.bluetooth.adapter import BluetoothAdapter
from backend.bl_haos.bluetooth.constants import DISCOVERY_TRANSPORT_AUTO
from dbus_fast import Variant


class FakeAdapterInterface:
    """Records the Adapter1 property/method calls the controller makes."""

    def __init__(self, fail_filter: bool = False) -> None:
        self.calls: list[str] = []
        self.filters: list[dict] = []
        self.properties_set: list[tuple[str, Variant]] = []
        self._fail_filter = fail_filter

    async def call_set_discovery_filter(self, payload):
        self.calls.append("set_discovery_filter")
        self.filters.append(payload)
        if self._fail_filter:
            raise Exception("org.bluez.Error.NotReady: filter refused")

    async def call_start_discovery(self):
        self.calls.append("start_discovery")

    async def call_stop_discovery(self):
        self.calls.append("stop_discovery")

    async def call_set(self, interface, name, value):
        self.calls.append(f"set:{name}")
        self.properties_set.append((name, value))


class FakeProxy:
    def __init__(self, interface) -> None:
        self._interface = interface

    def get_interface(self, _name):
        return self._interface


class FakeBus:
    def __init__(self, interface) -> None:
        self._interface = interface

    async def introspect(self, _service, _path):
        return object()

    def get_proxy_object(self, _service, _path, _introspection):
        return FakeProxy(self._interface)


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


@pytest.mark.asyncio
async def test_start_discovery_claims_the_filter_for_classic_devices():
    """A discovery filter belongs to the adapter, not to the client that set it.

    Home Assistant Core's Bluetooth integration leaves an LE-only transport
    filter behind, and a scan that inherits it never reports a Classic (BR/EDR)
    speaker - observed live on an HAOS instance where this add-on had already
    paired and streamed to such a device.
    """
    interface = FakeAdapterInterface()
    adapter = BluetoothAdapter(bus=FakeBus(interface), path="/org/bluez/hci0", properties={})

    await adapter.start_discovery()

    assert interface.calls == ["set_discovery_filter", "start_discovery"]
    assert interface.filters == [{"Transport": Variant("s", DISCOVERY_TRANSPORT_AUTO)}]
    assert adapter.discovering is True


@pytest.mark.asyncio
async def test_start_discovery_survives_a_refused_filter_change():
    """A rejected filter must not stop the operator's scan."""
    interface = FakeAdapterInterface(fail_filter=True)
    adapter = BluetoothAdapter(bus=FakeBus(interface), path="/org/bluez/hci0", properties={})

    await adapter.start_discovery()

    assert interface.calls == ["set_discovery_filter", "start_discovery"]
    assert adapter.discovering is True


@pytest.mark.asyncio
async def test_set_pairable_writes_the_adapter_property():
    interface = FakeAdapterInterface()
    adapter = BluetoothAdapter(
        bus=FakeBus(interface), path="/org/bluez/hci0", properties={"Pairable": Variant("b", True)}
    )

    await adapter.set_pairable(False)

    assert adapter.pairable is False
    assert interface.properties_set == [("Pairable", Variant("b", False))]
