"""REST API Route Handlers for BL-HAOS."""

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ..bluetooth.models import AdapterInfo, DeviceInfo
from ..config import SpeakerSettings, SystemSettings
from ..ha.player import MediaPlayerError
from ..health import HealthRegistry, HealthState

logger = logging.getLogger("bl_haos.api.routes")
router = APIRouter(prefix="/api", tags=["api"])
NATIVE_BRIDGE_ID = "bl_haos_native_bridge"
NATIVE_BRIDGE_VERSION = 1


def require_native_auth(authorization: str | None, request: Request) -> None:
    """Reject native transport requests without the installation credential."""
    expected = request.app.state.config_store.settings.native_token
    if not expected or authorization != f"Bearer {expected}":
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
    address: str
    pin: str | None = "0000"


class PowerRequest(BaseModel):
    powered: bool


class ScanRequest(BaseModel):
    adapter_name: str | None = None


class SpeakerUpdateRequest(BaseModel):
    custom_alias: str | None = None
    auto_reconnect: bool | None = None
    preferred_adapter: str | None = None
    default_volume: int | None = None
    codec_override: str | None = None


class NativeCommandRequest(BaseModel):
    """Versioned command payload accepted from the bundled integration only."""

    version: Literal[1] = 1
    operation: Literal["play", "pause", "stop", "set_volume", "play_media"]
    volume: float | None = Field(default=None, ge=0, le=1)
    url: str | None = None
    media_type: str | None = Field(default=None, max_length=128)


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
    normalized = address.strip().lower().replace("-", ":")
    device = next(
        (candidate for candidate in request.app.state.bt_manager.get_devices(audio_only=True)
         if candidate.address.strip().lower().replace("-", ":") == normalized),
        None,
    )
    if device is None:
        raise HTTPException(status_code=404, detail="Native speaker was not found")
    if not ((device.trusted or device.paired or device.connected) and device.is_audio_sink and device.connected):
        raise HTTPException(status_code=409, detail="Native speaker is unavailable")
    if payload.operation == "set_volume" and payload.volume is None:
        raise HTTPException(status_code=422, detail="Volume is required")
    if payload.operation == "play_media" and not payload.url:
        raise HTTPException(status_code=422, detail="Media URL is required")
    try:
        await request.app.state.ha_bridge.execute(
            normalized, payload.operation, volume=payload.volume, url=payload.url
        )
    except MediaPlayerError as error:
        if "Connected PipeWire A2DP sink is unavailable" in str(error):
            logger.info("A2DP sink unavailable for %s, attempting auto-reconnect", normalized)
            try:
                await request.app.state.bt_manager.connect_device(normalized)
                await asyncio.sleep(2.0)
                await request.app.state.ha_bridge.execute(
                    normalized, payload.operation, volume=payload.volume, url=payload.url
                )
            except Exception as retry_error:
                raise HTTPException(status_code=409, detail=f"Auto-reconnect failed: {retry_error}") from retry_error
        else:
            raise HTTPException(status_code=409, detail=str(error)) from error
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
        if payload.pin:
            request.app.state.bt_manager.agent.pin_callback = lambda dev: payload.pin
        success = await request.app.state.bt_manager.pair_and_trust(payload.address)
        # Register in auto reconnect
        request.app.state.reconnect_engine.register_speaker(payload.address)
        publish = getattr(request.app.state, "publish_native_speaker", None)
        if publish:
            await publish(payload.address)
        return {"status": "ok", "paired": success, "address": payload.address}
    except Exception as e:
        logger.error("Pairing error for %s: %s", payload.address, e)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/devices/{address}/connect")
async def connect_device(address: str, request: Request):
    try:
        success = await request.app.state.bt_manager.connect_device(address)
        publish = getattr(request.app.state, "publish_native_speaker", None)
        if publish:
            await publish(address)
        return {"status": "ok", "connected": success, "address": address}
    except Exception as e:
        logger.error("Connection error for %s: %s", address, e)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/devices/{address}/disconnect")
async def disconnect_device(address: str, request: Request):
    try:
        success = await request.app.state.bt_manager.disconnect_device(address)
        return {"status": "ok", "connected": False, "address": address}
    except Exception as e:
        logger.error("Disconnection error for %s: %s", address, e)
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/devices/{address}")
async def remove_device(address: str, request: Request):
    try:
        success = await request.app.state.bt_manager.remove_device(address)
        request.app.state.reconnect_engine.unregister_speaker(address)
        request.app.state.config_store.remove_speaker(address)
        return {"status": "ok", "removed": success, "address": address}
    except Exception as e:
        logger.error("Remove error for %s: %s", address, e)
        raise HTTPException(status_code=400, detail=str(e))


# ==============================================================================
# Settings Routes
# ==============================================================================

@router.get("/settings", response_model=SystemSettings)
async def get_settings(request: Request):
    return request.app.state.config_store.settings


@router.put("/settings/speakers/{address}", response_model=SpeakerSettings)
async def update_speaker_settings(address: str, payload: SpeakerUpdateRequest, request: Request):
    updated = request.app.state.config_store.update_speaker(
        address=address,
        custom_alias=payload.custom_alias,
        auto_reconnect=payload.auto_reconnect,
        preferred_adapter=payload.preferred_adapter,
        default_volume=payload.default_volume,
        codec_override=payload.codec_override,
    )
    if payload.auto_reconnect is not None:
        if payload.auto_reconnect:
            request.app.state.reconnect_engine.register_speaker(address, preferred_adapter=payload.preferred_adapter)
        else:
            request.app.state.reconnect_engine.unregister_speaker(address)
    return updated


# ==============================================================================
# Multi-room Routes
# ==============================================================================

@router.get("/multiroom/groups")
async def list_multiroom_groups(request: Request):
    return request.app.state.multiroom_manager.get_groups()


@router.get("/multiroom/clients")
async def list_multiroom_clients(request: Request):
    return request.app.state.multiroom_manager.get_clients()


@router.post("/multiroom/speakers/{address}/latency")
async def set_speaker_latency(address: str, payload: dict[str, int], request: Request):
    offset = payload.get("latency_offset_ms", 0)
    success = request.app.state.multiroom_manager.set_latency_offset(address, offset)
    return {"status": "ok", "address": address, "latency_offset_ms": offset, "updated": success}
