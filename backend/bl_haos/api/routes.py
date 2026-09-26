"""REST API Route Handlers for BL-HAOS."""

import asyncio
import hmac
import logging
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..bluetooth.models import AdapterInfo, DeviceInfo
from ..bluetooth.device import BluetoothOperationInProgress
from ..config import SpeakerSettings, SystemSettings
from ..ha.player import MediaPlayerError
from ..constants import (
    A2DP_SINK_RETRY_ATTEMPTS,
    A2DP_SINK_RETRY_INTERVAL,
    API_PREFIX,
    APP_NAME,
    BEARER_PREFIX,
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

logger = logging.getLogger("bl_haos.api.routes")
router = APIRouter(prefix=API_PREFIX, tags=["api"])


def require_native_auth(authorization: str | None, request: Request) -> None:
    """Reject native transport requests without the installation credential."""
    expected = request.app.state.config_store.settings.native_token
    supplied = authorization.removeprefix(BEARER_PREFIX) if isinstance(authorization, str) else ""
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Native bridge authentication required")


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
    def valid_address(cls, value: str) -> str:
        return normalize_address(value)

    @field_validator("pin")
    @classmethod
    def valid_pin(cls, value: str | None) -> str | None:
        return validate_pin(value)


class PowerRequest(BaseModel):
    powered: bool


class ScanRequest(BaseModel):
    adapter_name: str | None = None

    @field_validator("adapter_name")
    @classmethod
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
    def valid_preferred_adapter(cls, value: str | None) -> str | None:
        return validate_adapter_name(value) if value is not None else None

    @field_validator("custom_alias")
    @classmethod
    def valid_alias(cls, value: str | None) -> str | None:
        if value is not None and (len(value) > MAX_ALIAS_LENGTH or any(ord(char) < 32 for char in value)):
            raise ValueError("Speaker alias is invalid")
        return value

    @field_validator("codec_override")
    @classmethod
    def valid_codec(cls, value: str | None) -> str | None:
        if value is not None and value not in SUPPORTED_CODECS:
            raise ValueError("Codec override is invalid")
        return value


class NativeCommandRequest(BaseModel):
    """Versioned command payload accepted from the bundled integration only."""

    version: Literal[NATIVE_COMMAND_VERSION] = NATIVE_COMMAND_VERSION
    operation: Literal["play", "pause", "stop", "set_volume", "play_media"]
    volume: float | None = Field(default=None, ge=VOLUME_MIN_RATIO, le=VOLUME_MAX_RATIO)
    url: str | None = Field(default=None, max_length=MAX_MEDIA_URL_LENGTH)
    media_type: str | None = Field(default=None, max_length=MAX_MEDIA_TYPE_LENGTH)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str | None) -> str | None:
        return validate_media_url(value) if value is not None else None

    @field_validator("media_type")
    @classmethod
    def valid_media_type(cls, value: str | None) -> str | None:
        return validate_media_type(value)

    @model_validator(mode="after")
    def validate_operation_payload(self):
        if self.operation == "set_volume" and self.volume is None:
            raise ValueError("Volume is required")
        if self.operation == "play_media" and self.url is None:
            raise ValueError("Media URL is required")
        if self.operation != "play_media" and (self.url is not None or self.media_type is not None):
            raise ValueError("Media fields are only valid for play_media")
        return self


def playback_envelope(bridge: Any, address: str) -> dict[str, Any]:
    """Return the live playback state shared by the native API and the dashboard.

    One builder for both surfaces keeps the volume the dashboard shows and the
    volume the integration reports from drifting apart.
    """
    if bridge is None:
        return {"state": PLAYBACK_IDLE, "volume": None}
    return {
        "state": bridge.get_state(address),
        "volume": bridge.get_volume(address),
        **native_playback_timeline(bridge, address),
    }


def native_speaker_record(source: Request | Any, device: DeviceInfo) -> dict[str, Any]:
    """Expose only operator-trusted audio-sink metadata for the native integration."""
    address = device.address.strip().lower().replace("-", ":")
    app = source.app if hasattr(source, "app") else source
    bridge = getattr(app.state, "ha_bridge", None)
    return {
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


def native_playback_timeline(bridge: Any, address: str) -> dict[str, Any]:
    """Add the position/duration envelope when the bridge can provide one."""
    getter = getattr(bridge, "get_timeline", None)
    if not callable(getter):
        return {}
    timeline = getter(address)
    return timeline if isinstance(timeline, dict) else {}


@router.get("/health")
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
async def get_native_diagnostics(request: Request):
    """Expose sanitized native bridge readiness for the Ingress dashboard."""
    logger.debug("Native diagnostics requested")
    return native_diagnostics(request.app)


@router.get("/native/identity")
async def get_native_identity(request: Request, authorization: str | None = Header(default=None)):
    """Return the fixed, versioned native bridge identity."""
    require_native_auth(authorization, request)
    logger.debug("Native bridge identity requested")
    return {
        "bridge_id": NATIVE_BRIDGE_ID,
        "version": NATIVE_BRIDGE_VERSION,
        # The caller necessarily holds the credential already, so a digest here
        # discloses nothing - it lets an operator confirm which credential the
        # bridge is using after a rotation. The unauthenticated diagnostics
        # endpoint deliberately stays free of any credential-shaped field.
        "credential_fingerprint": token_fingerprint(request.app.state.config_store.settings.native_token),
    }


@router.get("/native/speakers")
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
        logger.debug("Executing media player command: %s (volume: %s, url: %s)", payload.operation, payload.volume, payload.url)
        await request.app.state.ha_bridge.execute(
            normalized, payload.operation, volume=payload.volume, url=payload.url
        )
        logger.debug("Successfully executed media player command %s for %s", payload.operation, normalized)
    except MediaPlayerError as error:
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
                logger.warning("Auto-reconnect BlueZ step failed for %s: %s", normalized, safe_detail(connect_error))
                raise HTTPException(
                    status_code=409,
                    detail="Auto-reconnect failed: could not re-establish the Bluetooth connection",
                ) from connect_error
            if not reconnected:
                logger.warning("Auto-reconnect: BlueZ reported failure reconnecting %s", normalized)
                raise HTTPException(
                    status_code=409,
                    detail="Auto-reconnect failed: could not re-establish the Bluetooth connection",
                )
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
                            detail=safe_detail(retry_error) or "Native playback command failed",
                        ) from retry_error
                    if attempt == A2DP_SINK_RETRY_ATTEMPTS - 1:
                        logger.warning(
                            "Auto-reconnect: sink still unavailable for %s after reconnect: %s",
                            normalized, safe_detail(retry_error),
                        )
                        raise HTTPException(
                            status_code=409,
                            detail="Auto-reconnect failed: Bluetooth reconnected but no audio sink appeared",
                        ) from retry_error
                except Exception as retry_error:
                    raise HTTPException(status_code=409, detail="Native playback command failed") from retry_error
        else:
            raise HTTPException(
                status_code=409,
                detail=safe_detail(error) or "Native playback command failed",
            ) from error
    publish = getattr(request.app.state, "publish_native_speaker", None)
    if publish:
        await publish(normalized)
    return native_speaker_record(request, device)


# ==============================================================================
# Adapter Routes
# ==============================================================================

@router.get("/adapters", response_model=list[AdapterInfo])
async def list_adapters(request: Request):
    adapters = request.app.state.bt_manager.get_adapters()
    logger.debug("Listing adapters: %d adapters found", len(adapters))
    return adapters


@router.post("/adapters/{adapter_name}/power")
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
async def start_scan(request: Request, payload: ScanRequest | None = None):
    adapter_name = payload.adapter_name if payload else None
    logger.debug("Starting Bluetooth scan on adapter: %s", adapter_name or "all")
    await request.app.state.bt_manager.start_scan(adapter_name)
    return {"status": "ok", "scanning": True, "adapter": adapter_name or "all"}


@router.post("/scan/stop")
async def stop_scan(request: Request, payload: ScanRequest | None = None):
    adapter_name = payload.adapter_name if payload else None
    logger.debug("Stopping Bluetooth scan on adapter: %s", adapter_name or "all")
    await request.app.state.bt_manager.stop_scan(adapter_name)
    return {"status": "ok", "scanning": False, "adapter": adapter_name or "all"}


# ==============================================================================
# Device Routes
# ==============================================================================

@router.get("/devices", response_model=list[DeviceInfo])
async def list_devices(request: Request, audio_only: bool = True):
    devices = request.app.state.bt_manager.get_devices(audio_only=audio_only)
    logger.debug("Listing devices (audio_only=%s): returning %d devices", audio_only, len(devices))
    # Attach live playback state (the dashboard slider binds to playback.volume).
    return [ui_speaker_record(request, device) for device in devices]


@router.post("/devices/pair")
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
async def get_settings(request: Request):
    logger.debug("System settings requested")
    return request.app.state.config_store.settings


@router.put("/settings/speakers/{address}", response_model=SpeakerSettings)
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
    return updated
