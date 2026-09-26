"""Deterministic offline runtime used by SIL and operator demonstrations."""

from __future__ import annotations

from typing import Any

from .bluetooth.models import AdapterInfo, DeviceInfo
from .constants import (
    COMMAND_PAUSE,
    COMMAND_PLAY,
    COMMAND_SET_VOLUME,
    COMMAND_STOP,
    COMPONENT_BLUETOOTH,
    COMPONENT_DEMO,
    COMPONENT_NATIVE_BRIDGE,
    COMPONENT_PIPEWIRE,
    DEFAULT_VOLUME_RATIO,
    EVENT_DEMO_SCENARIO,
    PLAYBACK_IDLE,
    PLAYBACK_PLAYING,
    SOURCE_DEMO,
)
from .diagnostics import DiagnosticsService
from .health import FailureClass, HealthRegistry, HealthState, SpeakerState

# The demonstration speaker the dashboard focuses on in every scenario.
DEMO_PRIMARY_SPEAKER = "aa:bb:cc:11:22:33"

DEMO_SCENARIOS = {
    "healthy": None,
    "pairing_failure": ("pairing_failed", HealthState.DEGRADED),
    "sink_missing": (FailureClass.SINK_MISSING.value, HealthState.DEGRADED),
    "reconnect_exhausted": (FailureClass.RECONNECT_EXHAUSTED.value, HealthState.UNAVAILABLE),
    "native_integration_unavailable": ("native_integration_unavailable", HealthState.DEGRADED),
    "restart_degraded": (FailureClass.STARTUP_FAILED.value, HealthState.DEGRADED),
}


class _DemoAgent:
    def __init__(self) -> None:
        self.pin_callback = None


class _DemoAdapterWrapper:
    def __init__(self, adapter_info: AdapterInfo) -> None:
        self._info = adapter_info

    @property
    def interface(self) -> str:
        return self._info.interface

    @property
    def powered(self) -> bool:
        return self._info.powered

    @property
    def discovering(self) -> bool:
        return self._info.discovering

    async def set_power(self, powered: bool) -> None:
        self._info.powered = powered


class DemoRuntime:
    """Small manager-compatible provider with no host-service initialization."""

    CLOCK = 1700000000.0

    def __init__(self, scenario: str, health: HealthRegistry | None = None) -> None:
        if scenario not in DEMO_SCENARIOS:
            raise ValueError("Unknown demo scenario")
        self.scenario = scenario
        self.bus = None
        self.agent = _DemoAgent()
        self.live_initialization_calls: list[str] = []
        self.health = health or HealthRegistry(clock=lambda: self.CLOCK)
        self.diagnostics_service = DiagnosticsService(self.health, clock=lambda: self.CLOCK)
        self.adapter = AdapterInfo(
            path="/demo/hci0",
            interface="hci0",
            address="00:11:22:33:44:55",
            name="Demo Adapter",
            alias="Demo Adapter",
            powered=True,
            discovering=False,
        )
        self.devices: dict[str, DeviceInfo] = {
            "aa:bb:cc:11:22:33": DeviceInfo(
                path="/demo/hci0/dev_AA_BB_CC_11_22_33",
                adapter_path="/demo/hci0",
                address="aa:bb:cc:11:22:33",
                name="Demo Speaker",
                alias="Demo Speaker",
                paired=True,
                trusted=True,
                connected=scenario == "healthy",
                is_audio_sink=scenario != "sink_missing",
                device_type="Speaker",
            ),
            "fc:58:fa:91:a2:b3": DeviceInfo(
                path="/demo/hci0/dev_FC_58_FA_91_A2_B3",
                adapter_path="/demo/hci0",
                address="fc:58:fa:91:a2:b3",
                name="Sony WH-1000XM4",
                alias="Sony WH-1000XM4",
                paired=False,
                trusted=False,
                connected=False,
                is_audio_sink=True,
                device_type="Headphones",
                rssi=-58,
            ),
            "08:eb:ed:44:55:66": DeviceInfo(
                path="/demo/hci0/dev_08_EB_ED_44_55_66",
                adapter_path="/demo/hci0",
                address="08:eb:ed:44:55:66",
                name="JBL Flip 6",
                alias="JBL Flip 6",
                paired=False,
                trusted=False,
                connected=False,
                is_audio_sink=True,
                device_type="Speaker",
                rssi=-65,
            ),
            "e4:58:b8:77:88:99": DeviceInfo(
                path="/demo/hci0/dev_E4_58_B8_77_88_99",
                adapter_path="/demo/hci0",
                address="e4:58:b8:77:88:99",
                name="Bose SoundLink Revolve",
                alias="Bose SoundLink Revolve",
                paired=False,
                trusted=False,
                connected=False,
                is_audio_sink=True,
                device_type="Speaker",
                rssi=-72,
            ),
        }
        self.device = self.devices[DEMO_PRIMARY_SPEAKER]
        self._states: dict[str, str] = {DEMO_PRIMARY_SPEAKER: PLAYBACK_IDLE}
        self._volumes: dict[str, float] = {DEMO_PRIMARY_SPEAKER: DEFAULT_VOLUME_RATIO}
        self._prepare_health()

    def _prepare_health(self) -> None:
        self.health.observe_component(COMPONENT_BLUETOOTH, HealthState.HEALTHY, source=SOURCE_DEMO)
        self.health.observe_component(COMPONENT_PIPEWIRE, HealthState.HEALTHY if self.scenario not in {"sink_missing"} else HealthState.UNAVAILABLE, source=SOURCE_DEMO)
        if self.scenario == "native_integration_unavailable":
            self.health.observe_component(COMPONENT_NATIVE_BRIDGE, HealthState.UNAVAILABLE, required=False, failure=FailureClass.NATIVE_INTEGRATION_UNAVAILABLE, source=SOURCE_DEMO)
        failure = DEMO_SCENARIOS[self.scenario]
        if failure:
            failure_class, state = failure
            self.health.observe_speaker(self.device.address, SpeakerState.UNAVAILABLE, failure=failure_class, detail="Deterministic demo failure", attempt=2)
            if failure_class == FailureClass.SINK_MISSING.value:
                self.health.observe_component(COMPONENT_PIPEWIRE, state, failure=failure_class, source=SOURCE_DEMO)
        else:
            self.health.observe_speaker(self.device.address, SpeakerState.CONNECTED)
        self.health.set_lifecycle(HealthState.HEALTHY if self.scenario == "healthy" else HealthState.DEGRADED)
        self.diagnostics_service.record_event(
            EVENT_DEMO_SCENARIO,
            component=COMPONENT_DEMO,
            detail={"scenario": self.scenario, "event_order": 1},
            correlation_id="demo-event-0001",
        )

    def get_adapters(self) -> list[AdapterInfo]:
        return [self.adapter]

    def get_adapter_by_name(self, name: str) -> _DemoAdapterWrapper | None:
        if name in (self.adapter.interface, self.adapter.name):
            return _DemoAdapterWrapper(self.adapter)
        return None

    async def start_scan(self, adapter_name: str | None = None) -> None:
        self.adapter.discovering = True

    async def stop_scan(self, adapter_name: str | None = None) -> None:
        self.adapter.discovering = False

    def get_devices(self, audio_only: bool = True) -> list[DeviceInfo]:
        results = list(self.devices.values())
        if audio_only:
            results = [d for d in results if d.is_audio_sink]
        return results

    def get_device_by_address(self, address: str) -> DeviceInfo | None:
        normalized = address.lower().replace("-", ":")
        return self.devices.get(normalized)

    async def pair_and_trust(self, address: str) -> bool:
        normalized = address.lower().replace("-", ":")
        dev = self.devices.get(normalized)
        if dev:
            dev.paired = True
            dev.trusted = True
            dev.connected = True
            self._states[normalized] = PLAYBACK_IDLE
            self._volumes.setdefault(normalized, DEFAULT_VOLUME_RATIO)
            self.health.observe_speaker(normalized, SpeakerState.CONNECTED)
            return True
        return False

    async def connect_device(self, address: str) -> bool:
        normalized = address.lower().replace("-", ":")
        dev = self.devices.get(normalized)
        if dev:
            dev.connected = True
            self.health.observe_speaker(normalized, SpeakerState.CONNECTED)
            return True
        return False

    async def disconnect_device(self, address: str) -> bool:
        normalized = address.lower().replace("-", ":")
        dev = self.devices.get(normalized)
        if dev:
            dev.connected = False
            self.health.observe_speaker(normalized, SpeakerState.DISCONNECTED)
            return True
        return False

    async def remove_device(self, address: str) -> bool:
        normalized = address.lower().replace("-", ":")
        dev = self.devices.get(normalized)
        if dev:
            dev.paired = False
            dev.trusted = False
            dev.connected = False
            return True
        return False

    async def ensure_device(self, address: str) -> DeviceInfo | None:
        return self.get_device_by_address(address)

    async def recover_dbus(self) -> bool:
        return True

    def get_state(self, address: str) -> str:
        normalized = address.lower().replace("-", ":")
        return self._states.get(normalized, PLAYBACK_IDLE)

    def get_volume(self, address: str) -> float:
        normalized = address.lower().replace("-", ":")
        return self._volumes.get(normalized, DEFAULT_VOLUME_RATIO)

    async def execute(self, address: str, operation: str, **kwargs: Any) -> None:
        normalized = address.lower().replace("-", ":")
        if operation == COMMAND_SET_VOLUME:
            volume = kwargs.get("volume")
            if volume is not None:
                self._volumes[normalized] = float(volume)
        elif operation == COMMAND_PLAY:
            self._states[normalized] = PLAYBACK_PLAYING
        elif operation in (COMMAND_PAUSE, COMMAND_STOP):
            self._states[normalized] = PLAYBACK_IDLE
        return None

    async def start_keepalive(self) -> None:
        return None

    def start_volume_watch(self) -> None:
        return None

    async def async_shutdown(self) -> None:
        return None

    def add_event_listener(self, listener: Any) -> None:
        return None

    def snapshot(self) -> dict[str, Any]:
        data = self.diagnostics_service.snapshot(
            adapters=[{"name": self.adapter.interface, "powered": self.adapter.powered, "discovering": self.adapter.discovering}],
            sinks={"available": self.device.connected and self.device.is_audio_sink, "count": int(self.device.connected and self.device.is_audio_sink)},
        )
        data["demo_mode"] = True
        data["demo_scenario"] = self.scenario
        return data

    @property
    def diagnostics(self) -> dict[str, Any]:
        return self.snapshot()

    def export(self) -> dict[str, Any]:
        return {"diagnostics": self.snapshot(), "events": self.diagnostics_service.events()}