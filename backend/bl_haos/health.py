"""Canonical, bounded runtime health contract for the bridge."""

import hashlib
import ipaddress
import re
import time
from enum import Enum
import json
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from .constants import (
    BEARER_PREFIX,
    BROADCAST_ADDRESS_OCTET,
    COMPONENT_LIFECYCLE,
    DELETE_CODEPOINT,
    HEALTH_CONTRACT_VERSION,
    HTTP_SCHEMES,
    MAX_ADDRESS_LENGTH,
    MAX_DETAIL_LENGTH,
    MAX_IDENTIFIER_LENGTH,
    MAX_MEDIA_TYPE_LENGTH,
    MAX_MEDIA_URL_LENGTH,
    MAX_TCP_PORT,
    MEDIA_BLOCKED_HOST_NAMES,
    MEDIA_LOCALHOST_SUFFIX,
    MIN_PORTABLE_CODEPOINT,
    MIN_TCP_PORT,
    MULTICAST_ADDRESS_BIT,
    PIN_DIGITS_MAX,
    PIN_DIGITS_MIN,
    REDACTED_PLACEHOLDER,
    SECRET_TOKENS,
    SENSITIVE_QUERY_KEY_TOKENS,
    SOURCE_LIFECYCLE,
    TOKEN_FINGERPRINT_CHARS,
    URL_PATTERN,
    URL_REDACTION_PLACEHOLDER,
)


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
    RECONNECT_EXHAUSTED = "reconnect_exhausted"
    STARTUP_FAILED = "startup_failed"
    SHUTDOWN_INCOMPLETE = "shutdown_incomplete"


_SECRET_PATTERN = re.compile(rf"(?i)({'|'.join(SECRET_TOKENS)})\s*[:=]\s*[^\s,;]+")
_SENSITIVE_QUERY_KEYS = re.compile(rf"(?i)({'|'.join(SENSITIVE_QUERY_KEY_TOKENS)})")
_MAC_PATTERN = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")
_ADAPTER_PATTERN = re.compile(r"^hci[0-9]{1,2}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_PIN_PATTERN = re.compile(rf"^[0-9]{{{PIN_DIGITS_MIN},{PIN_DIGITS_MAX}}}$")


def normalize_address(address: str) -> str:
    if not isinstance(address, str):
        raise ValueError("Invalid Bluetooth address")
    address = address.strip()
    if not address or len(address) > MAX_ADDRESS_LENGTH or any(ord(char) < MIN_PORTABLE_CODEPOINT for char in address):
        raise ValueError("Invalid Bluetooth address")
    normalized = address.lower().replace("-", ":")
    if not _MAC_PATTERN.fullmatch(normalized):
        raise ValueError("Invalid Bluetooth address")
    first_octet = int(normalized[:2], 16)
    if first_octet == BROADCAST_ADDRESS_OCTET or first_octet & MULTICAST_ADDRESS_BIT:
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


def token_fingerprint(token: str) -> str:
    """Return a short digest that identifies a credential without revealing it.

    Operators compare the value before and after a rotation, or against the
    integration's value, to tell whether the credential they think is in use is
    the one installed. Eight hex characters of a SHA-256 digest cannot be
    reversed into the 32-byte secret.
    """
    if not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:TOKEN_FINGERPRINT_CHARS]


def media_host_is_blocked(hostname: str | None) -> bool:
    """Reject audio targets that can only be the bridge itself or a dead end.

    The check is deliberately syntactic: it needs no DNS, so playback never pays
    for a resolution and a caller cannot name the loopback interface, the cloud
    metadata service, or a legacy numeric spelling of either. Names that merely
    *resolve* to such an address are left to the decoder.
    """
    if not hostname:
        return True
    host = hostname.strip().lower().rstrip(".")
    if host in MEDIA_BLOCKED_HOST_NAMES or host.endswith(MEDIA_LOCALHOST_SUFFIX):
        return True
    address = _media_address(host)
    if address is None:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        # ::ffff:127.0.0.1 reaches loopback exactly like 127.0.0.1 does.
        address = address.ipv4_mapped
    return bool(
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _media_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse a host as an IP literal, including the legacy numeric spellings.

    ``getaddrinfo`` still accepts ``2130706433``, ``0x7f000001`` and the short
    ``127.1`` form, all of which reach loopback while failing a plain
    ``ipaddress.ip_address`` parse.
    """
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        numeric = int(host, 0)
    except ValueError:
        return None
    if not 0 <= numeric <= 0xFFFFFFFF:
        return None
    return ipaddress.IPv4Address(numeric)


def validate_media_url(url: str) -> str:
    if not isinstance(url, str) or not url or len(url) > MAX_MEDIA_URL_LENGTH:
        raise ValueError("Media URL must be a safe HTTP(S) URL")
    if any(ord(character) < MIN_PORTABLE_CODEPOINT or ord(character) == DELETE_CODEPOINT for character in url):
        raise ValueError("Media URL must be a safe HTTP(S) URL")
    parsed = urlsplit(url)
    if parsed.scheme not in HTTP_SCHEMES or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Media URL must be a safe HTTP(S) URL")
    if media_host_is_blocked(parsed.hostname):
        raise ValueError("Media URL must not target a loopback, link-local or multicast address")
    try:
        if parsed.port is not None and not MIN_TCP_PORT <= parsed.port <= MAX_TCP_PORT:
            raise ValueError
    except ValueError as error:
        raise ValueError("Media URL must be a safe HTTP(S) URL") from error
    return url


_MEDIA_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*(?:/[A-Za-z0-9!#$&^_.+-]+)?$")


def validate_media_type(media_type: str | None) -> str | None:
    """Accept any bounded, structurally safe media type token.

    Home Assistant sends its own content types ("music", "audio/mpeg",
    "video/mp4", "channel", ...) rather than strict MIME audio types. The value
    is informational only — ffmpeg sniffs the actual stream — so rejecting
    anything that is not ``audio/*`` breaks legitimate playback with a 422.
    Only unsafe or unbounded values are refused.
    """
    if media_type is None:
        return None
    if not isinstance(media_type, str):
        raise ValueError("Media type must be a media type string")
    candidate = media_type.strip()
    if not candidate or len(candidate) > MAX_MEDIA_TYPE_LENGTH or not _MEDIA_TYPE_PATTERN.fullmatch(candidate):
        raise ValueError("Media type must be a media type string")
    return candidate.lower()


def validate_pin(pin: str | None) -> str | None:
    if pin is not None and not _PIN_PATTERN.fullmatch(pin):
        raise ValueError(f"PIN must contain {PIN_DIGITS_MIN} to {PIN_DIGITS_MAX} digits")
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
    text = _SECRET_PATTERN.sub(rf"\1={REDACTED_PLACEHOLDER}", text)
    if "://" in text:
        text = re.sub(URL_PATTERN, _redact_url_match, text, flags=re.I)
    return text[:MAX_DETAIL_LENGTH]


def _redact_url_match(match: re.Match[str]) -> str:
    return URL_REDACTION_PLACEHOLDER


def redact_value(value: Any) -> Any:
    """Recursively remove credentials from support-facing values."""
    if isinstance(value, dict):
        return {
            str(key): REDACTED_PLACEHOLDER if _SENSITIVE_QUERY_KEYS.search(str(key)) else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact_value(item) for item in value]
    if isinstance(value, str) and "://" in value:
        return re.sub(URL_PATTERN, _redact_url_match, value, flags=re.I)
    return value


def authorized_bearer(authorization: str | None, expected: str) -> bool:
    """Compare bearer credentials without exposing the expected token."""
    supplied = authorization.removeprefix(BEARER_PREFIX) if isinstance(authorization, str) else ""
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
    version: int = HEALTH_CONTRACT_VERSION
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
            self.observe_component(
                COMPONENT_LIFECYCLE, state, required=False, failure=failure, detail=detail, source=SOURCE_LIFECYCLE
            )
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