"""Canonical, bounded runtime health contract for the bridge."""

import re
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class HealthState(str, Enum):
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    STOPPING = "stopping"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


class SpeakerState(str, Enum):
    UNKNOWN = "unknown"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"
    UNAVAILABLE = "unavailable"


class FailureClass(str, Enum):
    DBUS_UNAVAILABLE = "dbus_unavailable"
    DBUS_DISCONNECTED = "dbus_disconnected"
    STALE_BLUEZ_OBJECT = "stale_bluez_object"
    PIPEWIRE_UNAVAILABLE = "pipewire_unavailable"
    SINK_MISSING = "sink_missing"
    SNAPCAST_UNAVAILABLE = "snapcast_unavailable"
    RECONNECT_EXHAUSTED = "reconnect_exhausted"
    STARTUP_FAILED = "startup_failed"
    SHUTDOWN_INCOMPLETE = "shutdown_incomplete"


MAX_DETAIL_LENGTH = 256
_SECRET_PATTERN = re.compile(r"(?i)(token|password|secret|authorization|bearer)\s*[:=]\s*[^\s,;]+")


def normalize_address(address: str) -> str:
    return address.strip().lower().replace("-", ":")


def safe_detail(detail: Any) -> str | None:
    if detail is None:
        return None
    text = str(detail).replace("\r", " ").replace("\n", " ")
    text = _SECRET_PATTERN.sub(r"\1=[redacted]", text)
    if "://" in text:
        text = re.sub(r"[a-z]+://[^\s]+", "[url redacted]", text, flags=re.I)
    return text[:MAX_DETAIL_LENGTH]


class FailureObservation(BaseModel):
    classification: FailureClass
    detail: str | None = Field(default=None, max_length=MAX_DETAIL_LENGTH)
    observed_at: float = Field(default_factory=time.time)
    attempt: int | None = Field(default=None, ge=0)


class ComponentHealth(BaseModel):
    name: str
    state: HealthState = HealthState.UNKNOWN
    required: bool = True
    failure: FailureObservation | None = None
    source: str | None = None
    observed_at: float = Field(default_factory=time.time)


class SpeakerHealth(BaseModel):
    address: str
    state: SpeakerState = SpeakerState.UNKNOWN
    failure: FailureObservation | None = None
    transition: str | None = None
    observed_at: float = Field(default_factory=time.time)
    attempt: int = Field(default=0, ge=0)


class HealthSnapshot(BaseModel):
    version: int = 1
    status: HealthState
    lifecycle: HealthState
    components: dict[str, ComponentHealth] = Field(default_factory=dict)
    speakers: dict[str, SpeakerHealth] = Field(default_factory=dict)
    updated_at: float = Field(default_factory=time.time)


class HealthRegistry:
    """Own observations and publish one canonical snapshot to all consumers."""

    def __init__(self):
        self.components: dict[str, ComponentHealth] = {}
        self.speakers: dict[str, SpeakerHealth] = {}
        self.lifecycle = HealthState.STARTING
        self._listeners: list[Any] = []

    def add_listener(self, listener: Any) -> None:
        self._listeners.append(listener)

    def observe_component(
        self,
        name: str,
        state: HealthState,
        *,
        required: bool = True,
        failure: FailureClass | str | None = None,
        detail: Any = None,
        source: str | None = None,
    ) -> HealthSnapshot:
        observation = None
        if failure:
            observation = FailureObservation(
                classification=FailureClass(failure), detail=safe_detail(detail)
            )
        self.components[name] = ComponentHealth(
            name=name, state=state, required=required, failure=observation, source=source
        )
        return self.snapshot()

    def observe_speaker(
        self,
        address: str,
        state: SpeakerState,
        *,
        failure: FailureClass | str | None = None,
        detail: Any = None,
        attempt: int = 0,
    ) -> HealthSnapshot:
        address = normalize_address(address)
        previous = self.speakers.get(address)
        observation = None
        if failure:
            observation = FailureObservation(
                classification=FailureClass(failure), detail=safe_detail(detail), attempt=attempt
            )
        self.speakers[address] = SpeakerHealth(
            address=address,
            state=state,
            failure=observation or (None if state == SpeakerState.CONNECTED else (previous.failure if previous else None)),
            transition=f"{previous.state.value}->{state.value}" if previous else state.value,
            attempt=attempt,
        )
        return self.snapshot()

    def set_lifecycle(self, state: HealthState, *, failure: FailureClass | str | None = None, detail: Any = None) -> HealthSnapshot:
        self.lifecycle = state
        if failure:
            self.observe_component("lifecycle", state, required=False, failure=failure, detail=detail, source="lifecycle")
        return self.snapshot()

    def snapshot(self) -> HealthSnapshot:
        states = [component.state for component in self.components.values() if component.required]
        if any(state == HealthState.UNAVAILABLE for state in states):
            status = HealthState.UNAVAILABLE
        elif self.lifecycle in {HealthState.STOPPING, HealthState.STOPPED}:
            status = self.lifecycle
        elif self.lifecycle == HealthState.STARTING and not states:
            status = self.lifecycle
        elif any(state in {HealthState.DEGRADED, HealthState.UNKNOWN} for state in states):
            status = HealthState.DEGRADED
        elif self.lifecycle == HealthState.STARTING:
            status = self.lifecycle
        else:
            status = HealthState.HEALTHY
        return HealthSnapshot(
            status=status,
            lifecycle=self.lifecycle,
            components=self.components.copy(),
            speakers=self.speakers.copy(),
        )

    async def publish(self, event_bus: Any = None) -> HealthSnapshot:
        snapshot = self.snapshot()
        for listener in self._listeners:
            result = listener(snapshot)
            if hasattr(result, "__await__"):
                await result
        if event_bus is not None:
            await event_bus.broadcast("health", snapshot)
        return snapshot