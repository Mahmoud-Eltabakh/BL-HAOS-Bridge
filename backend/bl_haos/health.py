"""Canonical, bounded runtime health contract for the bridge."""

import re
import time
from enum import Enum
import json
from typing import Any
from urllib.parse import urlsplit

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
    PAIRING_FAILED = "pairing_failed"
    NATIVE_INTEGRATION_UNAVAILABLE = "native_integration_unavailable"
    DBUS_UNAVAILABLE = "dbus_unavailable"
    DBUS_DISCONNECTED = "dbus_disconnected"
    STALE_BLUEZ_OBJECT = "stale_bluez_object"
    PIPEWIRE_UNAVAILABLE = "pipewire_unavailable"
    SINK_MISSING = "sink_missing"
    SINK_UNAVAILABLE_TRANSPORT_HELD = "sink_unavailable_transport_held"
    SNAPCAST_UNAVAILABLE = "snapcast_unavailable"
    RECONNECT_EXHAUSTED = "reconnect_exhausted"
    STARTUP_FAILED = "startup_failed"
    SHUTDOWN_INCOMPLETE = "shutdown_incomplete"


MAX_DETAIL_LENGTH = 256
MAX_MEDIA_URL_LENGTH = 2048
MAX_IDENTIFIER_LENGTH = 64
_SECRET_PATTERN = re.compile(r"(?i)(token|password|secret|authorization|bearer)\s*[:=]\s*[^\s,;]+")
_SENSITIVE_QUERY_KEYS = re.compile(r"(?i)(token|password|secret|authorization|bearer|api[_-]?key|key)")
_MAC_PATTERN = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")
_ADAPTER_PATTERN = re.compile(r"^hci[0-9]{1,2}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def normalize_address(address: str) -> str:
    if not isinstance(address, str):
        raise ValueError("Invalid Bluetooth address")
    address = address.strip()
    if not address or len(address) > 17 or any(ord(char) < 32 for char in address):
        raise ValueError("Invalid Bluetooth address")
    normalized = address.lower().replace("-", ":")
    if not _MAC_PATTERN.fullmatch(normalized):
        raise ValueError("Invalid Bluetooth address")
    first_octet = int(normalized[:2], 16)
    if first_octet == 0xFF or first_octet & 1:
        raise ValueError("Invalid Bluetooth address")
    return normalized


def validate_adapter_name(name: str) -> str:
    if not isinstance(name, str) or not _ADAPTER_PATTERN.fullmatch(name):
        raise ValueError("Invalid Bluetooth adapter")
    return name


def validate_identifier(value: str, label: str = "Identifier") -> str:
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{label} is invalid")
    return value


def validate_media_url(url: str) -> str:
    if not isinstance(url, str) or not url or len(url) > MAX_MEDIA_URL_LENGTH:
        raise ValueError("Media URL must be a safe HTTP(S) URL")
    if any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise ValueError("Media URL must be a safe HTTP(S) URL")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Media URL must be a safe HTTP(S) URL")
    try:
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
    except ValueError as error:
        raise ValueError("Media URL must be a safe HTTP(S) URL") from error
    return url


def validate_media_type(media_type: str | None) -> str | None:
    if media_type is None:
        return None
    if len(media_type) > 128 or not re.fullmatch(r"audio/[A-Za-z0-9.+-]+", media_type):
        raise ValueError("Media type must be an audio media type")
    return media_type.lower()


def validate_pin(pin: str | None) -> str | None:
    if pin is not None and not re.fullmatch(r"[0-9]{4,8}", pin):
        raise ValueError("PIN must contain 4 to 8 digits")
    return pin


def safe_detail(detail: Any) -> str | None:
    if detail is None:
        return None
    if isinstance(detail, (dict, list, tuple, set)):
        detail = redact_value(detail)
        text = json.dumps(detail, sort_keys=True, default=str)
    else:
        text = str(detail)
    text = text.replace("\r", " ").replace("\n", " ")
    text = _SECRET_PATTERN.sub(r"\1=[redacted]", text)
    if "://" in text:
        text = re.sub(r"[a-z]+://[^\s]+", _redact_url_match, text, flags=re.I)
    return text[:MAX_DETAIL_LENGTH]


def _redact_url_match(match: re.Match[str]) -> str:
    return "[url redacted]"


def redact_value(value: Any) -> Any:
    """Recursively remove credentials from support-facing values."""
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _SENSITIVE_QUERY_KEYS.search(str(key)) else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact_value(item) for item in value]
    if isinstance(value, str) and "://" in value:
        return re.sub(r"[a-z]+://[^\s]+", _redact_url_match, value, flags=re.I)
    return value


def authorized_bearer(authorization: str | None, expected: str) -> bool:
    """Compare bearer credentials without exposing the expected token."""
    supplied = authorization.removeprefix("Bearer ") if isinstance(authorization, str) else ""
    import hmac

    return bool(expected) and hmac.compare_digest(supplied, expected)


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

    def __init__(self, clock: Any = time.time):
        self.components: dict[str, ComponentHealth] = {}
        self.speakers: dict[str, SpeakerHealth] = {}
        self.lifecycle = HealthState.STARTING
        self._listeners: list[Any] = []
        self.clock = clock

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
                classification=FailureClass(failure), detail=safe_detail(detail), observed_at=self.clock()
            )
        self.components[name] = ComponentHealth(
            name=name, state=state, required=required, failure=observation, source=source, observed_at=self.clock()
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
                classification=FailureClass(failure), detail=safe_detail(detail), attempt=attempt,
                observed_at=self.clock(),
            )
        self.speakers[address] = SpeakerHealth(
            address=address,
            state=state,
            failure=observation or (None if state == SpeakerState.CONNECTED else (previous.failure if previous else None)),
            transition=f"{previous.state.value}->{state.value}" if previous else state.value,
            attempt=attempt,
            observed_at=self.clock(),
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
            updated_at=self.clock(),
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