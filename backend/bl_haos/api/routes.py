"""REST API Route Handlers for BL-HAOS."""

import asyncio
import hmac
import logging
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..bluetooth.models import AdapterInfo, DeviceInfo
from ..config import SpeakerSettings, SystemSettings
from ..ha.player import MediaPlayerError
from ..health import (
    HealthRegistry,
    HealthState,
    normalize_address,
    validate_adapter_name,
    validate_media_type,
    validate_media_url,
    validate_pin,
    safe_detail,
)

logger = logging.getLogger("bl_haos.api.routes")
router = APIRouter(prefix="/api", tags=["api"])
NATIVE_BRIDGE_ID = "bl_haos_native_bridge"
NATIVE_BRIDGE_VERSION = 1
A2DP_SINK_RETRY_ATTEMPTS = 15
A2DP_SINK_RETRY_INTERVAL = 1.0


def require_native_auth(authorization: str | None, request: Request) -> None:
    """Reject native transport requests without the installation credential."""
    expected = request.app.state.config_store.settings.native_token
    supplied = authorization.removeprefix("Bearer ") if isinstance(authorization, str) else ""
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Native bridge authentication required")


def native_diagnostics(app: Any) -> dict[str, Any]:
    """Return only bounded, non-secret native bridge readiness details."""
    devices = app.state.bt_manager.get_devices(audio_only=True)
    trusted_speakers = [device for device in devices if (device.trusted or device.paired or device.connected) and device.is_audio_sink]
    health = getattr(app.state, "health_registry", None)
    snapshot = health.snapshot() if health else None
    return {
        "bridge_version": NATIVE_BRIDGE_VERSION,
        "native_transport_ready": hasattr(app.state, "ha_bridge"),
        "native_client_count": len(getattr(app.state.native_ws_manager, "active_connections", [])),
        "trusted_speaker_count": len(trusted_speakers),
        "connected_trusted_speaker_count": sum(device.connected for device in trusted_speakers),
        "health_status": snapshot.status.value if snapshot else HealthState.UNKNOWN.value,
    }


class PairRequest(BaseModel):
    address: str = Field(min_length=17, max_length=17)
    pin: str | None = "0000"

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


class SpeakerUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    custom_alias: str | None = None
    auto_reconnect: bool | None = None
    preferred_adapter: str | None = None
    default_volume: int | None = Field(default=None, ge=0, le=100)
    codec_override: str | None = None

    @field_validator("preferred_adapter")
    @classmethod
    def valid_preferred_adapter(cls, value: str | None) -> str | None:
        return validate_adapter_name(value) if value is not None else None

    @field_validator("custom_alias")
    @classmethod
    def valid_alias(cls, value: str | None) -> str | None:
        if value is not None and (len(value) > 128 or any(ord(char) < 32 for char in value)):
            raise ValueError("Speaker alias is invalid")
        return value

    @field_validator("codec_override")
    @classmethod
    def valid_codec(cls, value: str | None) -> str | None:
        if value is not None and value not in {"auto", "sbc", "sbc_xq", "aac", "aptx", "aptx_hd", "ldac"}:
            raise ValueError("Codec override is invalid")
        return value


class NativeCommandRequest(BaseModel):
    """Versioned command payload accepted from the bundled integration only."""

    version: Literal[1] = 1
    operation: Literal["play", "pause", "stop", "set_volume", "play_media"]
    volume: float | None = Field(default=None, ge=0, le=1)
    url: str | None = Field(default=None, max_length=2048)
    media_type: str | None = Field(default=None, max_length=128)

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


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: Literal["refresh_diagnostics", "retry_reconnect", "refresh_device", "recheck_dependency"]
    target: str | None = Field(default=None, max_length=64)


def native_speaker_record(source: Request | Any, device: DeviceInfo) -> dict[str, Any]:
    """Expose only trusted audio-sink metadata for the native integration."""
    address = device.address.strip().lower().replace("-", ":")
    app = source.app if hasattr(source, "app") else source
    bridge = getattr(app.state, "ha_bridge", None)
    return {
        "address": address,
        "name": device.alias or device.name or device.address,
        "available": device.connected,
        "connected": device.connected,
        "adapter": device.adapter_name,
        "trusted": bool(device.trusted or device.paired or device.connected),
        "is_audio_sink": device.is_audio_sink,
        "playback": {
            "state": bridge.get_state(device.address) if bridge else "idle",
            "volume": bridge.get_volume(device.address) if bridge else None,
        },
    }


@router.get("/health")
async def get_health(request: Request):
    diagnostics = native_diagnostics(request.app)
    registry: HealthRegistry | None = getattr(request.app.state, "health_registry", None)
    snapshot = registry.snapshot() if registry else None
    return {
        "status": "ok" if not snapshot or snapshot.status == HealthState.HEALTHY else snapshot.status.value,
        "service": "BL-HAOS",
        "dbus_connected": request.app.state.bt_manager.bus is not None,
        "adapters_count": len(request.app.state.bt_manager.get_adapters()),
        "devices_count": len(request.app.state.bt_manager.get_devices(audio_only=False)),
        "health": snapshot.model_dump(mode="json") if snapshot else None,
        "diagnostics": diagnostics,
    }


@router.get("/diagnostics/native")
async def get_native_diagnostics(request: Request):
    """Expose sanitized native bridge readiness for the Ingress dashboard."""
    return native_diagnostics(request.app)


def operator_diagnostics(request: Request) -> dict[str, Any]:
    """Return one bounded projection shared by support and operator clients."""
    demo_runtime = getattr(request.app.state, "demo_runtime", None)
    if demo_runtime is not None:
        return demo_runtime.snapshot()
    service = getattr(request.app.state, "diagnostics", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Diagnostics unavailable")
    adapters = [
        {"name": adapter.interface, "powered": adapter.powered, "discovering": adapter.discovering}
        for adapter in request.app.state.bt_manager.get_adapters()
    ]
    speakers = request.app.state.bt_manager.get_devices(audio_only=True)
    sinks = {
        "available": any(device.connected for device in speakers),
        "count": sum(bool(device.connected) for device in speakers),
    }
    return service.snapshot(adapters=adapters, sinks=sinks)


@router.get("/diagnostics")
async def get_diagnostics(request: Request):
    return operator_diagnostics(request)


@router.get("/recovery")
async def get_recovery(request: Request, authorization: str | None = Header(default=None)):
    require_native_auth(authorization, request)
    service = getattr(request.app.state, "recovery", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Recovery unavailable")
    return service.contract(operator_diagnostics(request))


@router.post("/recovery/actions")
async def execute_recovery(payload: RecoveryRequest, request: Request, authorization: str | None = Header(default=None)):
    require_native_auth(authorization, request)
    service = getattr(request.app.state, "recovery", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Recovery unavailable")
    try:
        result = await service.execute(payload.action_id, payload.target)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    refreshed = operator_diagnostics(request)
    result["diagnostics"] = refreshed
    return result


@router.get("/support/bundle")
async def get_support_bundle(request: Request):
    service = getattr(request.app.state, "diagnostics", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Diagnostics unavailable")
    payload = operator_diagnostics(request)
    bundle = service.support_bundle(
        adapters=payload["adapters"],
        sinks=payload["sink_availability"],
    )
    return JSONResponse(
        content=bundle,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="bl-haos-support-bundle.json"'},
    )


@router.get("/native/identity")
async def get_native_identity(request: Request, authorization: str | None = Header(default=None)):
    """Return the fixed, versioned native bridge identity."""
    require_native_auth(authorization, request)
    return {"bridge_id": NATIVE_BRIDGE_ID, "version": NATIVE_BRIDGE_VERSION}


@router.get("/native/speakers")
async def list_native_speakers(request: Request, authorization: str | None = Header(default=None)):
    """Return the current trusted Bluetooth audio-sink snapshot."""
    require_native_auth(authorization, request)
    speakers = {
        record["address"]: record
        for device in request.app.state.bt_manager.get_devices(audio_only=True)
        if device.trusted or device.paired or device.connected
        for record in [native_speaker_record(request, device)]
    }
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
    if not ((device.trusted or device.paired or device.connected) and device.is_audio_sink and device.connected):
        raise HTTPException(status_code=409, detail="Native speaker is unavailable")
    try:
        await request.app.state.ha_bridge.execute(
            normalized, payload.operation, volume=payload.volume, url=payload.url
        )
    except MediaPlayerError as error:
        if "Connected PipeWire A2DP sink is unavailable" in str(error):
            logger.info("A2DP sink unavailable for %s, attempting auto-reconnect", normalized)
            try:
                reconnected = await request.app.state.bt_manager.connect_device(normalized)
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
                    sink_missing = "Connected PipeWire A2DP sink is unavailable" in str(retry_error)
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
    return request.app.state.bt_manager.get_adapters()


@router.post("/adapters/{adapter_name}/power")
async def set_adapter_power(adapter_name: str, payload: PowerRequest, request: Request):
    try:
        adapter_name = validate_adapter_name(adapter_name)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid Bluetooth adapter") from error
    adapter = request.app.state.bt_manager.get_adapter_by_name(adapter_name)
    if not adapter:
        raise HTTPException(status_code=404, detail=f"Adapter {adapter_name} not found")
    await adapter.set_power(payload.powered)
    return {"status": "ok", "interface": adapter_name, "powered": payload.powered}


@router.post("/scan/start")
async def start_scan(request: Request, payload: ScanRequest | None = None):
    adapter_name = payload.adapter_name if payload else None
    await request.app.state.bt_manager.start_scan(adapter_name)
    return {"status": "ok", "scanning": True, "adapter": adapter_name or "all"}


@router.post("/scan/stop")
async def stop_scan(request: Request, payload: ScanRequest | None = None):
    adapter_name = payload.adapter_name if payload else None
    await request.app.state.bt_manager.stop_scan(adapter_name)
    return {"status": "ok", "scanning": False, "adapter": adapter_name or "all"}


# ==============================================================================
# Device Routes
# ==============================================================================

@router.get("/devices", response_model=list[DeviceInfo])
async def list_devices(request: Request, audio_only: bool = True):
    return request.app.state.bt_manager.get_devices(audio_only=audio_only)


@router.post("/devices/pair")
async def pair_device(payload: PairRequest, request: Request):
    try:
        if payload.pin and hasattr(request.app.state.bt_manager, "agent") and request.app.state.bt_manager.agent:
            request.app.state.bt_manager.agent.pin_callback = lambda dev: payload.pin
        success = await request.app.state.bt_manager.pair_and_trust(payload.address)
        # Register in auto reconnect if available
        reconnect_engine = getattr(request.app.state, "reconnect_engine", None)
        if reconnect_engine:
            reconnect_engine.register_speaker(payload.address)
        publish = getattr(request.app.state, "publish_native_speaker", None)
        if publish:
            await publish(payload.address)
        return {"status": "ok", "paired": success, "address": payload.address}
    except Exception as e:
        logger.error("Pairing error for %s: %s", payload.address, safe_detail(e))
        raise HTTPException(status_code=400, detail="Pairing failed") from e


@router.post("/devices/{address}/connect")
async def connect_device(address: str, request: Request):
    try:
        address = normalize_address(address)
        success = await request.app.state.bt_manager.connect_device(address)
        publish = getattr(request.app.state, "publish_native_speaker", None)
        if publish:
            await publish(address)
        return {"status": "ok", "connected": success, "address": address}
    except Exception as e:
        logger.error("Connection error for %s: %s", address, safe_detail(e))
        raise HTTPException(status_code=400, detail="Connection failed") from e


@router.post("/devices/{address}/disconnect")
async def disconnect_device(address: str, request: Request):
    try:
        address = normalize_address(address)
        success = await request.app.state.bt_manager.disconnect_device(address)
        return {"status": "ok", "connected": False, "address": address}
    except Exception as e:
        logger.error("Disconnection error for %s: %s", address, safe_detail(e))
        raise HTTPException(status_code=400, detail="Disconnection failed") from e


@router.delete("/devices/{address}")
async def remove_device(address: str, request: Request):
    try:
        address = normalize_address(address)
        success = await request.app.state.bt_manager.remove_device(address)
        reconnect_engine = getattr(request.app.state, "reconnect_engine", None)
        if reconnect_engine:
            reconnect_engine.unregister_speaker(address)
        request.app.state.config_store.remove_speaker(address)
        return {"status": "ok", "removed": success, "address": address}
    except Exception as e:
        logger.error("Remove error for %s: %s", address, safe_detail(e))
        raise HTTPException(status_code=400, detail="Device removal failed") from e


# ==============================================================================
# Settings Routes
# ==============================================================================

@router.get("/settings", response_model=SystemSettings)
async def get_settings(request: Request):
    return request.app.state.config_store.settings


@router.put("/settings/speakers/{address}", response_model=SpeakerSettings)
async def update_speaker_settings(address: str, payload: SpeakerUpdateRequest, request: Request):
    try:
        address = normalize_address(address)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid Bluetooth address") from error
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


# ==============================================================================
# Multi-room Routes
# ==============================================================================

@router.get("/multiroom/groups")
async def list_multiroom_groups(request: Request):
    manager = getattr(request.app.state, "multiroom_manager", None)
    return manager.get_groups() if manager else []


@router.get("/multiroom/clients")
async def list_multiroom_clients(request: Request):
    manager = getattr(request.app.state, "multiroom_manager", None)
    return manager.get_clients() if manager else []


@router.post("/multiroom/speakers/{address}/latency")
async def set_speaker_latency(address: str, payload: dict[str, int], request: Request):
    offset = payload.get("latency_offset_ms", 0)
    manager = getattr(request.app.state, "multiroom_manager", None)
    success = manager.set_latency_offset(address, offset) if manager else True
    return {"status": "ok", "address": address, "latency_offset_ms": offset, "updated": success}
