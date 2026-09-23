"""Deterministic offline runtime used by SIL and operator demonstrations."""

from __future__ import annotations

from typing import Any

from .bluetooth.models import AdapterInfo, DeviceInfo
from .diagnostics import DiagnosticsService
from .health import FailureClass, HealthRegistry, HealthState, SpeakerState

DEMO_SCENARIOS = {
    "healthy": None,
    "pairing_failure": ("pairing_failed", HealthState.DEGRADED),
    "sink_missing": (FailureClass.SINK_MISSING.value, HealthState.DEGRADED),
    "reconnect_exhausted": (FailureClass.RECONNECT_EXHAUSTED.value, HealthState.UNAVAILABLE),
    "native_integration_unavailable": ("native_integration_unavailable", HealthState.DEGRADED),
    "restart_degraded": (FailureClass.STARTUP_FAILED.value, HealthState.DEGRADED),
}


class DemoRuntime:
    """Small manager-compatible provider with no host-service initialization."""

    CLOCK = 1700000000.0

    def __init__(self, scenario: str, health: HealthRegistry | None = None) -> None:
        if scenario not in DEMO_SCENARIOS:
            raise ValueError("Unknown demo scenario")
        self.scenario = scenario
        self.bus = None
        self.live_initialization_calls: list[str] = []
        self.health = health or HealthRegistry(clock=lambda: self.CLOCK)
        self.diagnostics_service = DiagnosticsService(self.health, clock=lambda: self.CLOCK)
        self.adapter = AdapterInfo(path="/demo/hci0", interface="hci0", address="00:11:22:33:44:55", name="Demo Adapter")
        self.device = DeviceInfo(
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
        )
        self._prepare_health()

    def _prepare_health(self) -> None:
        self.health.observe_component("bluetooth", HealthState.HEALTHY, source="demo")
        self.health.observe_component("pipewire", HealthState.HEALTHY if self.scenario not in {"sink_missing"} else HealthState.UNAVAILABLE, source="demo")
        if self.scenario == "native_integration_unavailable":
            self.health.observe_component("native_bridge", HealthState.UNAVAILABLE, required=False, failure=FailureClass.NATIVE_INTEGRATION_UNAVAILABLE, source="demo")
        failure = DEMO_SCENARIOS[self.scenario]
        if failure:
            failure_class, state = failure
            self.health.observe_speaker(self.device.address, SpeakerState.UNAVAILABLE, failure=failure_class, detail="Deterministic demo failure", attempt=2)
            if failure_class == FailureClass.SINK_MISSING.value:
                self.health.observe_component("pipewire", state, failure=failure_class, source="demo")
        else:
            self.health.observe_speaker(self.device.address, SpeakerState.CONNECTED)
        self.health.set_lifecycle(HealthState.HEALTHY if self.scenario == "healthy" else HealthState.DEGRADED)
        self.diagnostics_service.record_event(
            "demo_scenario",
            component="demo",
            detail={"scenario": self.scenario, "event_order": 1},
            correlation_id="demo-event-0001",
        )

    def get_adapters(self) -> list[AdapterInfo]:
        return [self.adapter]

    def get_devices(self, audio_only: bool = True) -> list[DeviceInfo]:
        return [self.device] if (not audio_only or self.device.is_audio_sink) else []

    def get_device_by_address(self, address: str) -> DeviceInfo | None:
        return self.device if address.lower().replace("-", ":") == self.device.address else None

    async def connect_device(self, address: str) -> bool:
        return address == self.device.address

    async def ensure_device(self, address: str) -> DeviceInfo | None:
        return self.get_device_by_address(address)

    async def recover_dbus(self) -> bool:
        return True

    def get_state(self, address: str) -> str:
        return "idle"

    def get_volume(self, address: str) -> float:
        return 0.70

    async def execute(self, address: str, operation: str, **kwargs: Any) -> None:
        return None

    async def start_keepalive(self) -> None:
        return None

    async def async_shutdown(self) -> None:
        return None

    def add_event_listener(self, listener: Any) -> None:
        return None

    def snapshot(self) -> dict[str, Any]:
        data = self.diagnostics_service.snapshot(
            adapters=[{"name": self.adapter.interface, "powered": True, "discovering": False}],
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