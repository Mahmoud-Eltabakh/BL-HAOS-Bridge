"""REST API Route Handlers for BL-HAOS."""

import asyncio
import hmac
import logging
import math
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from ..bluetooth.models import AdapterInfo, DeviceInfo
from ..bluetooth.device import BluetoothOperationInProgress
from ..config import SpeakerSettings, SystemSettings
from ..ha.player import (
    MAX_QUEUE_ITEMS,
    MAX_QUEUE_PAYLOAD_BYTES,
    TITLE_MAX_LENGTH,
    QUEUE_ERROR_PLAYBACK_FAILED,
    MediaPlayerBridge,
    MediaPlayerError,
)
from ..constants import (
    A2DP_SINK_RETRY_ATTEMPTS,
    A2DP_SINK_RETRY_INTERVAL,
    API_PREFIX,
    APP_NAME,
    BEARER_PREFIX,
    COMMAND_SET_VOLUME,
    DEFAULT_PIN,
    MAX_ADDRESS_LENGTH,
    MAX_ALIAS_LENGTH,
    MAX_MEDIA_TYPE_LENGTH,
    MAX_MEDIA_URL_LENGTH,
    NATIVE_BRIDGE_ID,
    NATIVE_BRIDGE_VERSION,
    NATIVE_COMMAND_VERSION,
    PLAYBACK_IDLE,
    SUPPORTED_CODECS,
    VOLUME_MAX_PERCENT,
    VOLUME_MAX_RATIO,
    VOLUME_MIN_PERCENT,
    VOLUME_MIN_RATIO,
)
from ..health import (
    HealthRegistry,
    HealthState,
    normalize_address,
    token_fingerprint,
    validate_adapter_name,
    validate_media_type,
    validate_media_url,
    validate_pin,
    safe_detail,
)

from ..call_trace import trace_call

logger = logging.getLogger("bl_haos.api.routes")
router = APIRouter(prefix=API_PREFIX, tags=["api"])
NATIVE_QUEUE_VERSION = 1
QUEUE_CONTROL_OPERATIONS = frozenset({"next", "previous", "set_shuffle", "set_repeat"})
NON_RETRYABLE_QUEUE_OPERATIONS = frozenset({"queue_start", *QUEUE_CONTROL_OPERATIONS})
SAFE_QUEUE_CONTROL_REASONS = frozenset({
    "No active queue",
    "No next item in queue",
    "No active playback to pause",
    "No active playback to resume",
    "No failed queue item to retry",
    "Shuffle value is invalid",
    "Repeat mode is invalid",
})


@router.get("/incidents")
@trace_call
async def incident_observations(request: Request):
    """Return monitor status and the bounded latest incident observations."""
    recorder = getattr(request.app.state, "incident_recorder", None)
    if recorder is None:
        return {"enabled": False, "status": None, "observations": []}
    try:
        observations = await recorder.recent()
    except (OSError, ValueError) as error:
        logger.error("Incident history read failed: %s", type(error).__name__)
        raise HTTPException(status_code=503, detail="Incident history could not be read") from error
    return {"enabled": True, "status": recorder.status(), "observations": observations}


@trace_call
def require_native_auth(authorization: str | None, request: Request) -> None:
    """Reject native transport requests without the installation credential."""
    expected = request.app.state.config_store.settings.native_token
    supplied = authorization.removeprefix(BEARER_PREFIX) if isinstance(authorization, str) else ""
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Native bridge authentication required")


@trace_call
def native_diagnostics(app: Any) -> dict[str, Any]:
    """Return only bounded, non-secret native bridge readiness details."""
    devices = app.state.bt_manager.get_devices(audio_only=True)
    trusted_speakers = [device for device in devices if device.trusted and device.is_audio_sink]
    health = getattr(app.state, "health_registry", None)
    snapshot = health.snapshot() if health else None
    config_store = getattr(app.state, "config_store", None)
    token = getattr(getattr(config_store, "settings", None), "native_token", "") or ""
    return {
        "bridge_version": NATIVE_BRIDGE_VERSION,
        "native_transport_ready": hasattr(app.state, "ha_bridge"),
        "native_client_count": len(getattr(app.state.native_ws_manager, "active_connections", [])),
        "trusted_speaker_count": len(trusted_speakers),
        "connected_trusted_speaker_count": sum(device.connected for device in trusted_speakers),
        "health_status": snapshot.status.value if snapshot else HealthState.UNKNOWN.value,
        # Which device may currently pair, so an operator can see that a pairing
        # window is open (and close it by letting it expire).
        "pairing_authorized_address": getattr(app.state.bt_manager, "current_pairing_address", lambda: None)(),
    }


class PairRequest(BaseModel):
    address: str = Field(min_length=MAX_ADDRESS_LENGTH, max_length=MAX_ADDRESS_LENGTH)
    pin: str | None = DEFAULT_PIN

    @field_validator("address")
    @classmethod
    @trace_call
    def valid_address(cls, value: str) -> str:
        return normalize_address(value)

    @field_validator("pin")
    @classmethod
    @trace_call
    def valid_pin(cls, value: str | None) -> str | None:
        return validate_pin(value)


class PowerRequest(BaseModel):
    powered: bool


class ScanRequest(BaseModel):
    adapter_name: str | None = None

    @field_validator("adapter_name")
    @classmethod
    @trace_call
    def valid_adapter(cls, value: str | None) -> str | None:
        return validate_adapter_name(value) if value is not None else None


class VolumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    volume: int = Field(ge=VOLUME_MIN_PERCENT, le=VOLUME_MAX_PERCENT)


class SpeakerUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    custom_alias: str | None = None
    auto_reconnect: bool | None = None
    preferred_adapter: str | None = None
    default_volume: int | None = Field(default=None, ge=VOLUME_MIN_PERCENT, le=VOLUME_MAX_PERCENT)
    codec_override: str | None = None

    @field_validator("preferred_adapter")
    @classmethod
    @trace_call
    def valid_preferred_adapter(cls, value: str | None) -> str | None:
        return validate_adapter_name(value) if value is not None else None

    @field_validator("custom_alias")
    @classmethod
    @trace_call
    def valid_alias(cls, value: str | None) -> str | None:
        if value is not None and (len(value) > MAX_ALIAS_LENGTH or any(ord(char) < 32 for char in value)):
            raise ValueError("Speaker alias is invalid")
        return value

    @field_validator("codec_override")
    @classmethod
    @trace_call
    def valid_codec(cls, value: str | None) -> str | None:
        if value is not None and value not in SUPPORTED_CODECS:
            raise ValueError("Codec override is invalid")
        return value


class NativeQueueItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=MAX_MEDIA_URL_LENGTH)
    title: str | None = Field(default=None, max_length=TITLE_MAX_LENGTH)
    artist: str | None = Field(default=None, max_length=TITLE_MAX_LENGTH)

    @field_validator("url")
    @classmethod
    @trace_call
    def valid_url(cls, value: str) -> str:
        return validate_media_url(value)

    @field_validator("title", "artist")
    @classmethod
    @trace_call
    def valid_display_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if "://" in cleaned or any(ord(char) < 32 for char in cleaned):
            raise ValueError("Queue display text is invalid")
        return cleaned


class NativeCommandRequest(BaseModel):
    """Versioned command payload accepted from the bundled integration only."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[NATIVE_COMMAND_VERSION] = NATIVE_COMMAND_VERSION
    operation: Literal[
        "play", "pause", "stop", "set_volume", "play_media", "queue_start",
        "next", "previous", "set_shuffle", "set_repeat",
    ]
    queue_version: Literal[NATIVE_QUEUE_VERSION] | None = None
    volume: float | None = Field(default=None, ge=VOLUME_MIN_RATIO, le=VOLUME_MAX_RATIO)
    url: str | None = Field(default=None, max_length=MAX_MEDIA_URL_LENGTH)
    media_type: str | None = Field(default=None, max_length=MAX_MEDIA_TYPE_LENGTH)
    playlist: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX_LENGTH)
    items: list[NativeQueueItemRequest] | None = Field(
        default=None, min_length=1, max_length=MAX_QUEUE_ITEMS
    )
    shuffle: StrictBool | None = None
    repeat: Literal["off", "one", "all"] | None = None

    @field_validator("playlist")
    @classmethod
    @trace_call
    def valid_playlist_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned or "://" in cleaned or any(ord(char) < 32 for char in cleaned):
            raise ValueError("Playlist label is invalid")
        return cleaned

    @field_validator("url")
    @classmethod
    @trace_call
    def valid_url(cls, value: str | None) -> str | None:
        return validate_media_url(value) if value is not None else None

    @field_validator("media_type")
    @classmethod
    @trace_call
    def valid_media_type(cls, value: str | None) -> str | None:
        return validate_media_type(value)

    @model_validator(mode="after")
    @trace_call
    def validate_operation_payload(self):
        if self.operation in NON_RETRYABLE_QUEUE_OPERATIONS:
            if self.queue_version != NATIVE_QUEUE_VERSION:
                raise ValueError("Supported queue version is required")
            if self.volume is not None or self.url is not None or self.media_type is not None:
                raise ValueError("Queue fields cannot be combined with media command fields")
            if self.operation == "queue_start":
                if self.playlist is None or self.items is None:
                    raise ValueError("Playlist and queue items are required")
                if self.shuffle is not None or self.repeat is not None:
                    raise ValueError("Queue start cannot include queue controls")
                payload_bytes = len(self.playlist.encode("utf-8"))
                for item in self.items:
                    payload_bytes += len(item.url.encode("utf-8"))
                    payload_bytes += len((item.title or "").encode("utf-8"))
                    payload_bytes += len((item.artist or "").encode("utf-8"))
                if payload_bytes > MAX_QUEUE_PAYLOAD_BYTES:
                    raise ValueError("Queue request is too large")
                return self
            if self.playlist is not None or self.items is not None:
                raise ValueError("Queue start fields are only valid for queue_start")
            if self.operation == "set_shuffle":
                if not isinstance(self.shuffle, bool) or self.repeat is not None:
                    raise ValueError("Shuffle value is required")
            elif self.operation == "set_repeat":
                if self.repeat is None or self.shuffle is not None:
                    raise ValueError("Repeat mode is required")
            elif self.shuffle is not None or self.repeat is not None:
                raise ValueError("Queue mode fields are only valid for their matching operation")
            return self
        if self.queue_version is not None or self.shuffle is not None or self.repeat is not None:
            raise ValueError("Queue fields are only valid for queue operations")
        if self.operation == "queue_start":
            raise ValueError("Supported queue version is required")
        if self.playlist is not None or self.items is not None:
            raise ValueError("Queue fields are only valid for queue_start")
        if self.operation == "set_volume" and self.volume is None:
            raise ValueError("Volume is required")
        if self.operation == "play_media" and self.url is None:
            raise ValueError("Media URL is required")
        if self.operation != "play_media" and (self.url is not None or self.media_type is not None):
            raise ValueError("Media fields are only valid for play_media")
        return self


class IngressQueueCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue_version: Literal[NATIVE_QUEUE_VERSION]
    operation: Literal["play", "pause", "stop", "next", "previous", "set_shuffle", "set_repeat"]
    shuffle: StrictBool | None = None
    repeat: Literal["off", "one", "all"] | None = None

    @model_validator(mode="after")
    @trace_call
    def validate_queue_control(self):
        if self.operation == "set_shuffle":
            if not isinstance(self.shuffle, bool) or self.repeat is not None:
                raise ValueError("Shuffle value is required")
        elif self.operation == "set_repeat":
            if self.repeat is None or self.shuffle is not None:
                raise ValueError("Repeat mode is required")
        elif self.shuffle is not None or self.repeat is not None:
            raise ValueError("Queue mode fields are only valid for their matching operation")
        return self


@trace_call
def playback_envelope(bridge: Any, address: str) -> dict[str, Any]:
    """Return the live playback state shared by the native API and the dashboard.

    One builder for both surfaces keeps the volume the dashboard shows and the
    volume the integration reports from drifting apart.
    """
    if bridge is None:
        return {"state": PLAYBACK_IDLE, "volume": None}
    envelope = {
        "state": bridge.get_state(address),
        "volume": bridge.get_volume(address),
        **native_playback_timeline(bridge, address),
    }
    if callable(getattr(bridge, "get_queue", None)):
        envelope["queue"] = native_queue_snapshot(bridge, address)
    return envelope


@trace_call
def native_queue_snapshot(bridge: Any, address: str) -> dict[str, Any] | None:
    """Project only bounded display state; never serialize queue input URLs."""
    getter = getattr(bridge, "get_queue", None)
    snapshot = getter(address) if callable(getter) else None
    if not isinstance(snapshot, dict):
        return None

    result: dict[str, Any] = {}
    status = snapshot.get("status")
    if isinstance(status, str) and status in {
        "playing", "paused", "failed", "completed", "stopped", "unavailable"
    }:
        result["status"] = status
    for field in ("playlist", "title", "artist"):
        value = snapshot.get(field)
        cleaned = MediaPlayerBridge._text(value)
        if cleaned is not None:
            result[field] = cleaned
    error = snapshot.get("error")
    result["error"] = QUEUE_ERROR_PLAYBACK_FAILED if error == QUEUE_ERROR_PLAYBACK_FAILED else None
    for field in ("position", "duration", "position_updated_at"):
        value = snapshot.get(field)
        maximum = 4_102_444_800 if field == "position_updated_at" else 315_576_000
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum:
            result[field] = value
        elif isinstance(value, float) and math.isfinite(value) and 0 <= value <= maximum:
            result[field] = value
        else:
            result[field] = None
    for field in ("index", "count"):
        value = snapshot.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_QUEUE_ITEMS:
            result[field] = value
    if isinstance(snapshot.get("shuffle"), bool):
        result["shuffle"] = snapshot["shuffle"]
    repeat = snapshot.get("repeat")
    if isinstance(repeat, str) and repeat in {"off", "one", "all"}:
        result["repeat"] = repeat
    if "status" not in result:
        return None
    return result


@trace_call
def native_speaker_record(source: Request | Any, device: DeviceInfo) -> dict[str, Any]:
    """Expose only operator-trusted audio-sink metadata for the native integration."""
    address = device.address.strip().lower().replace("-", ":")
    app = source.app if hasattr(source, "app") else source
    bridge = getattr(app.state, "ha_bridge", None)
    record = {
        "address": address,
        "name": device.alias or device.name or device.address,
        "available": device.connected,
        "connected": device.connected,
        "adapter": device.adapter_name,
        # Trust is the operator's explicit "this speaker is mine". Merely being
        # paired or connected is what any device in radio range can achieve, so
        # it must not be reported as trust (see THREAT-MODEL.md, T3).
        "trusted": bool(device.trusted),
        "is_audio_sink": device.is_audio_sink,
        # Pairing state, so the integration can tell "switched off but still
        # paired" (keep the entity, disable it) from "BlueZ no longer knows this
        # device" (remove the entity). A device BlueZ withdraws is one it treats
        # as temporary, which is every device that is not paired.
        "paired": bool(device.paired),
        "detached": bool(device.detached),
        "playback": playback_envelope(bridge, device.address),
    }
    if callable(getattr(bridge, "get_queue", None)):
        record["queue_version"] = NATIVE_QUEUE_VERSION
    return record


@trace_call
def ui_speaker_record(source: Request | Any, device: DeviceInfo) -> DeviceInfo:
    """Return the device record the Ingress dashboard consumes.

    BlueZ metadata alone cannot show volume: the slider binds to
    ``playback.volume``, which only the media player bridge knows.
    """
    app = source.app if hasattr(source, "app") else source
    bridge = getattr(app.state, "ha_bridge", None)
    if bridge is None or not device.is_audio_sink:
        return device
    try:
        playback = playback_envelope(bridge, device.address)
    except ValueError:
        # A synthetic or malformed address is never a real speaker; listing must
        # not fail because one record cannot be looked up.
        return device
    return device.model_copy(update={"playback": playback})


@trace_call
def native_playback_timeline(bridge: Any, address: str) -> dict[str, Any]:
    """Add the position/duration envelope when the bridge can provide one."""
    getter = getattr(bridge, "get_timeline", None)
    if not callable(getter):
        return {}
    timeline = getter(address)
    if not isinstance(timeline, dict):
        return {}
    result: dict[str, Any] = {}
    for field in ("position", "duration", "position_updated_at"):
        value = timeline.get(field)
        maximum = 4_102_444_800 if field == "position_updated_at" else 315_576_000
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum:
            result[field] = value
        elif isinstance(value, float) and math.isfinite(value) and 0 <= value <= maximum:
            result[field] = value
    for field in ("title", "artist"):
        value = MediaPlayerBridge._text(timeline.get(field))
        if value is not None:
            result[field] = value
    return result


@router.get("/health")
@trace_call
async def get_health(request: Request):
    diagnostics = native_diagnostics(request.app)
    registry: HealthRegistry | None = getattr(request.app.state, "health_registry", None)
    snapshot = registry.snapshot() if registry else None
    logger.debug(
        "Health check requested (status=%s, dbus_connected=%s)",
        snapshot.status.value if snapshot else HealthState.UNKNOWN.value,
        request.app.state.bt_manager.bus is not None,
    )
    return {
        "status": "ok" if not snapshot or snapshot.status == HealthState.HEALTHY else snapshot.status.value,
        "service": APP_NAME,
        "dbus_connected": request.app.state.bt_manager.bus is not None,
        "adapters_count": len(request.app.state.bt_manager.get_adapters()),
        "devices_count": len(request.app.state.bt_manager.get_devices(audio_only=False)),
        "health": snapshot.model_dump(mode="json") if snapshot else None,
        "diagnostics": diagnostics,
    }


@router.get("/diagnostics/native")
@trace_call
async def get_native_diagnostics(request: Request):
    """Expose sanitized native bridge readiness for the Ingress dashboard."""
    logger.debug("Native diagnostics requested")
    return native_diagnostics(request.app)


@router.get("/native/identity")
@trace_call
async def get_native_identity(request: Request, authorization: str | None = Header(default=None)):
    """Return the fixed, versioned native bridge identity."""
    require_native_auth(authorization, request)
    logger.debug("Native bridge identity requested")
    identity = {
        "bridge_id": NATIVE_BRIDGE_ID,
        "version": NATIVE_BRIDGE_VERSION,
        # The caller necessarily holds the credential already, so a digest here
        # discloses nothing - it lets an operator confirm which credential the
        # bridge is using after a rotation. The unauthenticated diagnostics
        # endpoint deliberately stays free of any credential-shaped field.
        "credential_fingerprint": token_fingerprint(request.app.state.config_store.settings.native_token),
    }
    bridge = getattr(request.app.state, "ha_bridge", None)
    if callable(getattr(bridge, "get_queue", None)):
        identity["queue_version"] = NATIVE_QUEUE_VERSION
    return identity


@router.get("/native/speakers")
@trace_call
async def list_native_speakers(request: Request, authorization: str | None = Header(default=None)):
    """Return the current operator-trusted Bluetooth audio-sink snapshot."""
    require_native_auth(authorization, request)
    speakers = {
        record["address"]: record
        for device in request.app.state.bt_manager.get_devices(audio_only=True)
        if device.trusted
        for record in [native_speaker_record(request, device)]
    }
    logger.debug("Native speakers requested: returning %d speakers (%s)", len(speakers), list(speakers.keys()))
    return {"speakers": speakers}


@router.post("/native/speakers/{address}/command")
@trace_call
async def command_native_speaker(
    address: str,
    payload: NativeCommandRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Apply an authenticated command and return the post-operation speaker record."""
    require_native_auth(authorization, request)
    logger.debug("Received native command %s for address %s", payload.operation, address)
    try:
        normalized = normalize_address(address)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid Bluetooth address") from error
    device = next(
        (candidate for candidate in request.app.state.bt_manager.get_devices(audio_only=True)
         if candidate.address.strip().lower().replace("-", ":") == normalized),
        None,
    )
    if device is None:
        raise HTTPException(status_code=404, detail="Native speaker was not found")
    if not (device.trusted and device.is_audio_sink):
        raise HTTPException(status_code=409, detail="Native speaker is not trusted or not an audio sink")
    try:
        logger.debug(
            "Executing media player command: %s for %s (volume: %s, media_type: %s)",
            payload.operation, normalized, payload.volume,
            {"music": "audio", "audio/mpeg": "audio", "audio/flac": "audio", "audio/wav": "audio", "audio/ogg": "audio", "audio/mp4": "audio", "playlist": "playlist"}.get(payload.media_type, "other"),
        )
        if payload.operation == "queue_start":
            assert payload.playlist is not None and payload.items is not None
            await request.app.state.ha_bridge.start_queue(
                normalized,
                payload.playlist,
                [item.model_dump(exclude_none=True) for item in payload.items],
            )
        elif payload.operation in QUEUE_CONTROL_OPERATIONS:
            await request.app.state.ha_bridge.control_queue(
                normalized,
                payload.operation,
                shuffle=payload.shuffle,
                repeat=payload.repeat,
            )
        else:
            await request.app.state.ha_bridge.execute(
                normalized, payload.operation, volume=payload.volume, url=payload.url
            )
        logger.debug("Successfully executed media player command %s for %s", payload.operation, normalized)
    except MediaPlayerError as error:
        if payload.operation in NON_RETRYABLE_QUEUE_OPERATIONS:
            logger.warning("Native queue operation failed for %s (%s)", normalized, payload.operation)
            reason = str(error)
            raise HTTPException(
                status_code=409,
                detail=reason
                if payload.operation in QUEUE_CONTROL_OPERATIONS and reason in SAFE_QUEUE_CONTROL_REASONS
                else "Native playback command failed",
            ) from None
        is_sink_unavailable = (
            "Connected PipeWire A2DP sink is unavailable" in str(error)
            or "Connected Bluetooth audio sink is unavailable" in str(error)
            or "audio sink is unavailable" in str(error).lower()
        )
        if is_sink_unavailable:
            logger.info("A2DP sink unavailable for %s, attempting auto-reconnect", normalized)
            try:
                reconnected = await request.app.state.bt_manager.connect_device(normalized, reset_existing=True)
            except Exception as connect_error:
                logger.warning("Auto-reconnect BlueZ step failed for %s", normalized)
                raise HTTPException(
                    status_code=409,
                    detail="Auto-reconnect failed: could not re-establish the Bluetooth connection",
                ) from None
            if not reconnected:
                logger.warning("Auto-reconnect: BlueZ reported failure reconnecting %s", normalized)
                raise HTTPException(
                    status_code=409,
                    detail="Auto-reconnect failed: could not re-establish the Bluetooth connection",
                ) from None
            for attempt in range(A2DP_SINK_RETRY_ATTEMPTS):
                await asyncio.sleep(A2DP_SINK_RETRY_INTERVAL)
                try:
                    await request.app.state.ha_bridge.execute(
                        normalized, payload.operation, volume=payload.volume, url=payload.url
                    )
                    break
                except MediaPlayerError as retry_error:
                    sink_missing = (
                        "Connected PipeWire A2DP sink is unavailable" in str(retry_error)
                        or "Connected Bluetooth audio sink is unavailable" in str(retry_error)
                        or "audio sink is unavailable" in str(retry_error).lower()
                    )
                    if not sink_missing:
                        raise HTTPException(
                            status_code=409,
                            detail="Native playback command failed" if payload.operation == "play_media" else (safe_detail(retry_error) or "Native playback command failed"),
                        ) from None
                    if attempt == A2DP_SINK_RETRY_ATTEMPTS - 1:
                        logger.warning(
                            "Auto-reconnect: sink still unavailable for %s after reconnect",
                            normalized,
                        )
                        raise HTTPException(
                            status_code=409,
                            detail="Auto-reconnect failed: Bluetooth reconnected but no audio sink appeared",
                        ) from None
                except Exception as retry_error:
                    raise HTTPException(status_code=409, detail="Native playback command failed") from None
        else:
            raise HTTPException(
                status_code=409,
                detail="Native playback command failed" if payload.operation == "play_media" else (safe_detail(error) or "Native playback command failed"),
            ) from None
    except Exception:
        if payload.operation not in {"play_media", *NON_RETRYABLE_QUEUE_OPERATIONS}:
            raise
        raise HTTPException(status_code=409, detail="Native playback command failed") from None
    publish = getattr(request.app.state, "publish_native_speaker", None)
    if publish:
        await publish(normalized)
    return native_speaker_record(request, device)


@router.post("/devices/{address}/queue/command")
@trace_call
async def command_ingress_queue(
    address: str,
    payload: IngressQueueCommandRequest,
    request: Request,
):
    """Apply one queue control through the add-on's Supervisor-protected Ingress API."""
    try:
        normalized = normalize_address(address)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid Bluetooth address") from error
    device = next(
        (candidate for candidate in request.app.state.bt_manager.get_devices(audio_only=True)
         if candidate.address.strip().lower().replace("-", ":") == normalized),
        None,
    )
    if device is None or not (device.trusted and device.is_audio_sink):
        raise HTTPException(status_code=409, detail="Queue speaker is unavailable")
    if not device.connected:
        raise HTTPException(status_code=409, detail="Queue speaker is unavailable")
    bridge = getattr(request.app.state, "ha_bridge", None)
    if bridge is None:
        raise HTTPException(status_code=503, detail="Media player bridge is unavailable")
    try:
        if payload.operation in {"play", "pause", "stop"}:
            await bridge.execute(normalized, payload.operation)
        else:
            await bridge.control_queue(
                normalized,
                payload.operation,
                shuffle=payload.shuffle,
                repeat=payload.repeat,
            )
    except MediaPlayerError:
        raise HTTPException(status_code=409, detail="Queue command failed") from None
    except Exception:
        raise HTTPException(status_code=409, detail="Queue command failed") from None
    publish = getattr(request.app.state, "publish_native_speaker", None)
    if publish:
        await publish(normalized)
    return {"status": "ok", "playback": playback_envelope(bridge, normalized)}


# ==============================================================================
# Adapter Routes
# ==============================================================================

@router.get("/adapters", response_model=list[AdapterInfo])
@trace_call
async def list_adapters(request: Request):
    adapters = request.app.state.bt_manager.get_adapters()
    logger.debug("Listing adapters: %d adapters found", len(adapters))
    return adapters


@router.post("/adapters/{adapter_name}/power")
@trace_call
async def set_adapter_power(adapter_name: str, payload: PowerRequest, request: Request):
    try:
        adapter_name = validate_adapter_name(adapter_name)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid Bluetooth adapter") from error
    logger.debug("Setting adapter %s power to %s", adapter_name, payload.powered)
    adapter = request.app.state.bt_manager.get_adapter_by_name(adapter_name)
    if not adapter:
        raise HTTPException(status_code=404, detail=f"Adapter {adapter_name} not found")
    await adapter.set_power(payload.powered)
    return {"status": "ok", "interface": adapter_name, "powered": payload.powered}


@router.post("/scan/start")
@trace_call
async def start_scan(request: Request, payload: ScanRequest | None = None):
    adapter_name = payload.adapter_name if payload else None
    logger.debug("Starting Bluetooth scan on adapter: %s", adapter_name or "all")
    await request.app.state.bt_manager.start_scan(adapter_name)
    return {"status": "ok", "scanning": True, "adapter": adapter_name or "all"}


@router.post("/scan/stop")
@trace_call
async def stop_scan(request: Request, payload: ScanRequest | None = None):
    adapter_name = payload.adapter_name if payload else None
    logger.debug("Stopping Bluetooth scan on adapter: %s", adapter_name or "all")
    await request.app.state.bt_manager.stop_scan(adapter_name)
    return {"status": "ok", "scanning": False, "adapter": adapter_name or "all"}


# ==============================================================================
# Device Routes
# ==============================================================================

@router.get("/devices", response_model=list[DeviceInfo])
@trace_call
async def list_devices(request: Request, audio_only: bool = True):
    devices = request.app.state.bt_manager.get_devices(audio_only=audio_only)
    logger.debug("Listing devices (audio_only=%s): returning %d devices", audio_only, len(devices))
    # Attach live playback state (the dashboard slider binds to playback.volume).
    return [ui_speaker_record(request, device) for device in devices]


@router.post("/devices/pair")
@trace_call
async def pair_device(payload: PairRequest, request: Request):
    logger.debug("Pairing request received for %s", payload.address)
    try:
        # The manager opens a short-lived pairing window for this one address, so
        # the BlueZ agent answers only while this operator-initiated call runs.
        success = await request.app.state.bt_manager.pair_and_trust(payload.address, payload.pin)
        # Register in auto reconnect if available
        reconnect_engine = getattr(request.app.state, "reconnect_engine", None)
        if reconnect_engine:
            reconnect_engine.register_speaker(payload.address)
        publish = getattr(request.app.state, "publish_native_speaker", None)
        if publish:
            await publish(payload.address)
        logger.debug("Pairing completed successfully for %s", payload.address)
        return {"status": "ok", "paired": success, "address": payload.address}
    except Exception as e:
        detail = safe_detail(e)
        if isinstance(e, BluetoothOperationInProgress):
            logger.warning("Pairing busy for %s: %s", payload.address, detail)
            raise HTTPException(status_code=409, detail="Bluetooth pairing is already in progress") from e
        logger.error("Pairing error for %s: %s", payload.address, detail)
        # Surface the bounded, redacted BlueZ reason so the operator UI can
        # explain why pairing failed instead of showing a generic message.
        reason = detail or "unknown BlueZ error"
        raise HTTPException(status_code=400, detail=f"Pairing failed: {reason}") from e


@router.post("/devices/{address}/connect")
@trace_call
async def connect_device(address: str, request: Request):
    try:
        address = normalize_address(address)
        logger.debug("Connect request received for %s", address)
        # The operator asked for a connection, so a stale A2DP transport is reset
        # here on purpose; the auto-reconnect engine deliberately does not.
        success = await request.app.state.bt_manager.connect_device(address, reset_existing=True)
        publish = getattr(request.app.state, "publish_native_speaker", None)
        if publish:
            await publish(address)
        logger.debug("Connect completed successfully for %s", address)
        return {"status": "ok", "connected": success, "address": address}
    except Exception as e:
        detail = safe_detail(e)
        if isinstance(e, BluetoothOperationInProgress):
            logger.warning("Connection busy for %s: %s", address, detail)
            raise HTTPException(status_code=409, detail="Bluetooth connection is already in progress") from e
        logger.error("Connection error for %s: %s", address, detail)
        reason = detail or "unknown BlueZ error"
        raise HTTPException(status_code=400, detail=f"Connection failed: {reason}") from e


@router.post("/devices/{address}/disconnect")
@trace_call
async def disconnect_device(address: str, request: Request):
    try:
        address = normalize_address(address)
        logger.debug("Disconnect request received for %s", address)
        success = await request.app.state.bt_manager.disconnect_device(address)
        logger.debug("Disconnect completed successfully for %s", address)
        return {"status": "ok", "connected": False, "address": address}
    except Exception as e:
        logger.error("Disconnection error for %s: %s", address, safe_detail(e))
        raise HTTPException(status_code=400, detail="Disconnection failed") from e


@router.post("/devices/{address}/volume")
@trace_call
async def set_device_volume(address: str, payload: VolumeRequest, request: Request):
    """Apply a live volume level (0-100) to one speaker's PipeWire/PulseAudio sink."""
    try:
        address = normalize_address(address)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid Bluetooth address") from error
    logger.debug("Live volume request for %s: %d%%", address, payload.volume)
    bridge = getattr(request.app.state, "ha_bridge", None)
    if bridge is None:
        raise HTTPException(status_code=503, detail="Media player bridge is unavailable")
    try:
        await bridge.execute(address, "set_volume", volume=payload.volume / 100)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid volume request") from error
    except Exception as e:
        logger.error("Volume update error for %s: %s", address, safe_detail(e))
        raise HTTPException(status_code=400, detail="Volume update failed") from e
    publish = getattr(request.app.state, "publish_native_speaker", None)
    if publish:
        await publish(address)
    return {"status": "ok", "address": address, "volume": payload.volume}


@router.delete("/devices/{address}")
@trace_call
async def remove_device(address: str, request: Request):
    try:
        address = normalize_address(address)
        logger.debug("Remove device request received for %s", address)
        success = await request.app.state.bt_manager.remove_device(address)
        reconnect_engine = getattr(request.app.state, "reconnect_engine", None)
        if reconnect_engine:
            reconnect_engine.unregister_speaker(address)
        request.app.state.config_store.remove_speaker(address)
        logger.debug("Remove device completed successfully for %s", address)
        return {"status": "ok", "removed": success, "address": address}
    except Exception as e:
        logger.error("Remove error for %s: %s", address, safe_detail(e))
        raise HTTPException(status_code=400, detail="Device removal failed") from e


# ==============================================================================
# Settings Routes
# ==============================================================================

@router.get("/settings", response_model=SystemSettings)
@trace_call
async def get_settings(request: Request):
    logger.debug("System settings requested")
    return request.app.state.config_store.settings


@router.put("/settings/speakers/{address}", response_model=SpeakerSettings)
@trace_call
async def update_speaker_settings(address: str, payload: SpeakerUpdateRequest, request: Request):
    try:
        address = normalize_address(address)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid Bluetooth address") from error
    logger.debug(
        "Updating speaker settings for %s (alias=%s, auto_reconnect=%s, adapter=%s, volume=%s, codec=%s)",
        address,
        payload.custom_alias,
        payload.auto_reconnect,
        payload.preferred_adapter,
        payload.default_volume,
        payload.codec_override,
    )
    updated = request.app.state.config_store.update_speaker(
        address=address,
        custom_alias=payload.custom_alias,
        auto_reconnect=payload.auto_reconnect,
        preferred_adapter=payload.preferred_adapter,
        default_volume=payload.default_volume,
        codec_override=payload.codec_override,
    )
    reconnect_engine = getattr(request.app.state, "reconnect_engine", None)
    if reconnect_engine and payload.auto_reconnect is not None:
        if payload.auto_reconnect:
            reconnect_engine.register_speaker(address, preferred_adapter=payload.preferred_adapter)
        else:
            reconnect_engine.unregister_speaker(address)
    # A volume saved in the speaker's settings is the level the operator expects
    # the speaker to be at, so it is applied now as well as at every reconnect.
    # Best-effort on purpose: a speaker that is switched off has no sink, and
    # saving its settings must still succeed.
    if payload.default_volume is not None:
        bridge = getattr(request.app.state, "ha_bridge", None)
        if bridge is not None:
            try:
                await bridge.execute(
                    address, COMMAND_SET_VOLUME, volume=payload.default_volume / VOLUME_MAX_PERCENT
                )
            except Exception as error:
                logger.warning(
                    "Could not apply volume %d%% to %s: %s",
                    payload.default_volume,
                    address,
                    safe_detail(error),
                )
    return updated


SAFE_DEVICE_MANAGEMENT_REASONS = frozenset({
    "Bluetooth pairing is already in progress",
    "Bluetooth connection is already in progress",
    "Pairing failed",
    "Connection failed",
    "Disconnection failed",
    "Device removal failed",
    "Bluetooth scan failed",
    "Bluetooth adapter was not found",
    "Bluetooth adapter update failed",
    "Bluetooth adapter list is unavailable",
    "Bluetooth device list is unavailable",
    "Speaker settings update failed",
    "System settings update failed",
    "Invalid Bluetooth address",
    "Invalid Bluetooth adapter",
    "Speaker alias is invalid",
    "Codec override is invalid",
    "System settings are invalid",
})


class NativeSystemSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_level: Literal["trace", "debug", "info", "notice", "warning", "error", "fatal"] = None
    default_codec: Literal["auto", "sbc", "sbc_xq", "aac", "aptx", "aptx_hd", "ldac"] = None
    auto_reconnect_enabled: StrictBool = None

    @model_validator(mode="after")
    @trace_call
    def require_setting(self):
        if not self.model_fields_set:
            raise ValueError("At least one system setting must be provided")
        return self


@trace_call
def _native_management_failure(error: Exception, reason: str) -> HTTPException:
    logger.error("Native device-management operation failed: %s", safe_detail(error))
    safe_reason = reason if reason in SAFE_DEVICE_MANAGEMENT_REASONS else "Bluetooth action failed"
    return HTTPException(status_code=400, detail=safe_reason)


@router.post("/native/scan/start")
@trace_call
async def native_start_scan(request: Request, payload: ScanRequest | None = None, authorization: str | None = Header(default=None)):
    require_native_auth(authorization, request)
    adapter_name = payload.adapter_name if payload else None
    try:
        await request.app.state.bt_manager.start_scan(adapter_name)
    except Exception as error:
        raise _native_management_failure(error, "Bluetooth scan failed")
    return {"status": "ok", "scanning": True, "adapter": adapter_name or "all"}


@router.post("/native/scan/stop")
@trace_call
async def native_stop_scan(request: Request, payload: ScanRequest | None = None, authorization: str | None = Header(default=None)):
    require_native_auth(authorization, request)
    adapter_name = payload.adapter_name if payload else None
    try:
        await request.app.state.bt_manager.stop_scan(adapter_name)
    except Exception as error:
        raise _native_management_failure(error, "Bluetooth scan failed")
    return {"status": "ok", "scanning": False, "adapter": adapter_name or "all"}


@router.get("/native/devices")
@trace_call
async def native_list_devices(request: Request, authorization: str | None = Header(default=None)):
    require_native_auth(authorization, request)
    try:
        devices = request.app.state.bt_manager.get_devices(audio_only=False)
        return [ui_speaker_record(request, device) for device in devices]
    except Exception as error:
        raise _native_management_failure(error, "Bluetooth device list is unavailable")


@router.post("/native/devices/pair")
@trace_call
async def native_pair_device(payload: PairRequest, request: Request, authorization: str | None = Header(default=None)):
    require_native_auth(authorization, request)
    try:
        paired = await request.app.state.bt_manager.pair_and_trust(payload.address, payload.pin)
        reconnect_engine = getattr(request.app.state, "reconnect_engine", None)
        if reconnect_engine:
            reconnect_engine.register_speaker(payload.address)
        publish = getattr(request.app.state, "publish_native_speaker", None)
        if publish:
            await publish(payload.address)
        return {"status": "ok", "paired": paired, "address": payload.address}
    except BluetoothOperationInProgress as error:
        logger.warning("Native pairing busy for %s: %s", payload.address, safe_detail(error))
        raise HTTPException(status_code=409, detail="Bluetooth pairing is already in progress") from error
    except Exception as error:
        raise _native_management_failure(error, "Pairing failed")


@router.post("/native/devices/{address}/connect")
@trace_call
async def native_connect_device(address: str, request: Request, authorization: str | None = Header(default=None)):
    require_native_auth(authorization, request)
    try:
        address = normalize_address(address)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid Bluetooth address") from error
    try:
        connected = await request.app.state.bt_manager.connect_device(address, reset_existing=True)
        publish = getattr(request.app.state, "publish_native_speaker", None)
        if publish:
            await publish(address)
        return {"status": "ok", "connected": connected, "address": address}
    except BluetoothOperationInProgress as error:
        logger.warning("Native connection busy for %s: %s", address, safe_detail(error))
        raise HTTPException(status_code=409, detail="Bluetooth connection is already in progress") from error
    except Exception as error:
        raise _native_management_failure(error, "Connection failed")


@router.post("/native/devices/{address}/disconnect")
@trace_call
async def native_disconnect_device(address: str, request: Request, authorization: str | None = Header(default=None)):
    require_native_auth(authorization, request)
    try:
        address = normalize_address(address)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid Bluetooth address") from error
    try:
        await request.app.state.bt_manager.disconnect_device(address)
        return {"status": "ok", "connected": False, "address": address}
    except Exception as error:
        raise _native_management_failure(error, "Disconnection failed")


@router.delete("/native/devices/{address}")
@trace_call
async def native_remove_device(address: str, request: Request, authorization: str | None = Header(default=None)):
    require_native_auth(authorization, request)
    try:
        address = normalize_address(address)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid Bluetooth address") from error
    try:
        removed = await request.app.state.bt_manager.remove_device(address)
        reconnect_engine = getattr(request.app.state, "reconnect_engine", None)
        if reconnect_engine:
            reconnect_engine.unregister_speaker(address)
        request.app.state.config_store.remove_speaker(address)
        publish = getattr(request.app.state, "publish_native_speaker", None)
        if publish:
            await publish(address)
        return {"status": "ok", "removed": removed, "address": address}
    except Exception as error:
        raise _native_management_failure(error, "Device removal failed")


@router.get("/native/adapters")
@trace_call
async def native_list_adapters(request: Request, authorization: str | None = Header(default=None)):
    require_native_auth(authorization, request)
    try:
        return request.app.state.bt_manager.get_adapters()
    except Exception as error:
        raise _native_management_failure(error, "Bluetooth adapter list is unavailable")


@router.post("/native/adapters/{adapter_name}/power")
@trace_call
async def native_set_adapter_power(adapter_name: str, payload: PowerRequest, request: Request, authorization: str | None = Header(default=None)):
    require_native_auth(authorization, request)
    try:
        adapter_name = validate_adapter_name(adapter_name)
        adapter = request.app.state.bt_manager.get_adapter_by_name(adapter_name)
        if not adapter:
            raise HTTPException(status_code=404, detail="Bluetooth adapter was not found")
        await adapter.set_power(payload.powered)
        return {"status": "ok", "interface": adapter_name, "powered": payload.powered}
    except HTTPException:
        raise
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid Bluetooth adapter") from error
    except Exception as error:
        raise _native_management_failure(error, "Bluetooth adapter update failed")


@router.get("/native/settings")
@trace_call
async def native_get_settings(request: Request, authorization: str | None = Header(default=None)):
    require_native_auth(authorization, request)
    settings = request.app.state.config_store.settings
    return {
        "log_level": settings.log_level,
        "default_codec": settings.default_codec,
        "auto_reconnect_enabled": settings.auto_reconnect_enabled,
        "speakers": {address: speaker.model_dump(mode="json") for address, speaker in settings.speakers.items()},
    }


@router.put("/native/settings/system")
@trace_call
async def native_update_system_settings(payload: NativeSystemSettingsUpdate, request: Request, authorization: str | None = Header(default=None)):
    require_native_auth(authorization, request)
    settings = request.app.state.config_store.settings
    changed = payload.model_dump(exclude_unset=True)
    for key, value in changed.items():
        setattr(settings, key, value)
    request.app.state.config_store.save()
    if "log_level" in changed:
        logging.getLogger("bl_haos").setLevel({"trace": "DEBUG", "notice": "INFO", "fatal": "CRITICAL"}.get(changed["log_level"], changed["log_level"].upper()))
    if "auto_reconnect_enabled" in changed:
        reconnect_engine = getattr(request.app.state, "reconnect_engine", None)
        if reconnect_engine:
            reconnect_engine.set_global_enabled(settings.auto_reconnect_enabled)
            if settings.auto_reconnect_enabled:
                for device in request.app.state.bt_manager.get_devices(audio_only=True):
                    if device.trusted:
                        speaker = request.app.state.config_store.get_speaker(device.address)
                        reconnect_engine.register_speaker(
                            device.address,
                            enabled=speaker.auto_reconnect if speaker is not None else True,
                            preferred_adapter=speaker.preferred_adapter if speaker is not None else None,
                        )
                for address, speaker in settings.speakers.items():
                    if speaker.auto_reconnect:
                        reconnect_engine.register_speaker(address, preferred_adapter=speaker.preferred_adapter)
                    else:
                        reconnect_engine.unregister_speaker(address)
    return {
        "log_level": settings.log_level,
        "default_codec": settings.default_codec,
        "auto_reconnect_enabled": settings.auto_reconnect_enabled,
    }


@router.put("/native/settings/speakers/{address}")
@trace_call
async def native_update_speaker_settings(address: str, payload: SpeakerUpdateRequest, request: Request, authorization: str | None = Header(default=None)):
    require_native_auth(authorization, request)
    try:
        address = normalize_address(address)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid Bluetooth address") from error
    try:
        updated = request.app.state.config_store.update_speaker(
            address=address,
            custom_alias=payload.custom_alias,
            auto_reconnect=payload.auto_reconnect,
            preferred_adapter=payload.preferred_adapter,
            default_volume=payload.default_volume,
            codec_override=payload.codec_override,
        )
        reconnect_engine = getattr(request.app.state, "reconnect_engine", None)
        if reconnect_engine and payload.auto_reconnect is not None:
            if payload.auto_reconnect:
                reconnect_engine.register_speaker(address, preferred_adapter=payload.preferred_adapter)
            else:
                reconnect_engine.unregister_speaker(address)
        if payload.default_volume is not None:
            bridge = getattr(request.app.state, "ha_bridge", None)
            if bridge is not None:
                try:
                    await bridge.execute(address, COMMAND_SET_VOLUME, volume=payload.default_volume / VOLUME_MAX_PERCENT)
                except Exception as error:
                    logger.warning("Could not apply saved volume for %s: %s", address, safe_detail(error))
        publish = getattr(request.app.state, "publish_native_speaker", None)
        if publish:
            await publish(address)
        return updated
    except Exception as error:
        raise _native_management_failure(error, "Speaker settings update failed")
