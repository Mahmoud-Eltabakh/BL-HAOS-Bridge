"""REST API Route Handlers for BL-HAOS."""

import hmac
import logging
import os
from typing import List, Optional, Dict, Any, Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ..ha.player import MediaPlayerError

from ..bluetooth.models import AdapterInfo, DeviceInfo
from ..config import SystemSettings, SpeakerSettings

logger = logging.getLogger("bl_haos.api.routes")
router = APIRouter(prefix="/api", tags=["api"])
NATIVE_BRIDGE_ID = "bl_haos_native_bridge"
NATIVE_BRIDGE_VERSION = 1


def native_diagnostics(app: Any) -> dict[str, Any]:
    """Return only bounded, non-secret native bridge readiness details."""
    devices = app.state.bt_manager.get_devices(audio_only=True)
    trusted_speakers = [device for device in devices if device.trusted and device.is_audio_sink]
    credential_present = bool(_bridge_credential())
    return {
        "bridge_credential_present": credential_present,
        "bridge_version": NATIVE_BRIDGE_VERSION,
        "native_transport_ready": credential_present and hasattr(app.state, "ha_bridge"),
        "native_client_count": len(getattr(app.state.native_ws_manager, "active_connections", [])),
        "trusted_speaker_count": len(trusted_speakers),
        "connected_trusted_speaker_count": sum(device.connected for device in trusted_speakers),
    }


class PairRequest(BaseModel):
    address: str
    pin: Optional[str] = "0000"


class PowerRequest(BaseModel):
    powered: bool


class ScanRequest(BaseModel):
    adapter_name: Optional[str] = None


class SpeakerUpdateRequest(BaseModel):
    custom_alias: Optional[str] = None
    auto_reconnect: Optional[bool] = None
    preferred_adapter: Optional[str] = None
    default_volume: Optional[int] = None
    codec_override: Optional[str] = None


class NativeCommandRequest(BaseModel):
    """Versioned command payload accepted from the bundled integration only."""

    version: Literal[1] = 1
    operation: Literal["play", "pause", "stop", "set_volume", "play_media"]
    volume: float | None = Field(default=None, ge=0, le=1)
    url: str | None = None
    media_type: str | None = Field(default=None, max_length=128)


def _bridge_credential() -> str:
    """Read the configured bridge token without logging it."""
    return os.environ.get("BL_HAOS_BRIDGE_TOKEN", "").strip()


async def require_native_bridge(
    authorization: Optional[str] = Header(default=None),
    bridge_credential: Optional[str] = Header(default=None, alias="X-BL-HAOS-Bridge-Credential"),
) -> None:
    """Reject uncredentialed native bridge requests before sending data."""
    supplied = bridge_credential or (authorization.removeprefix("Bearer ") if authorization else "")
    expected = _bridge_credential()
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid native bridge credential")


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
        "trusted": device.trusted,
        "is_audio_sink": device.is_audio_sink,
        "playback": {
            "state": bridge.get_state(device.address) if bridge else "idle",
            "volume": bridge.get_volume(device.address) if bridge else None,
        },
    }


@router.get("/health")
async def get_health(request: Request):
    diagnostics = native_diagnostics(request.app)
    return {
        "status": "ok",
        "service": "BL-HAOS",
        "dbus_connected": request.app.state.bt_manager.bus is not None,
        "adapters_count": len(request.app.state.bt_manager.get_adapters()),
        "devices_count": len(request.app.state.bt_manager.get_devices(audio_only=False)),
    }


@router.get("/diagnostics/native")
async def get_native_diagnostics(request: Request):
    """Expose sanitized native bridge readiness for the Ingress dashboard."""
    return native_diagnostics(request.app)


@router.get("/native/identity", dependencies=[Depends(require_native_bridge)])
async def get_native_identity():
    """Return the fixed, versioned native bridge identity."""
    return {"bridge_id": NATIVE_BRIDGE_ID, "version": NATIVE_BRIDGE_VERSION}


@router.get("/native/speakers", dependencies=[Depends(require_native_bridge)])
async def list_native_speakers(request: Request):
    """Return the current trusted Bluetooth audio-sink snapshot."""
    speakers = {
        record["address"]: record
        for device in request.app.state.bt_manager.get_devices(audio_only=True)
        if device.trusted
        for record in [native_speaker_record(request, device)]
    }
    return {"speakers": speakers}


@router.post("/native/speakers/{address}/command", dependencies=[Depends(require_native_bridge)])
async def command_native_speaker(address: str, payload: NativeCommandRequest, request: Request):
    """Apply an authenticated command and return the post-operation speaker record."""
    normalized = address.strip().lower().replace("-", ":")
    device = next(
        (candidate for candidate in request.app.state.bt_manager.get_devices(audio_only=True)
         if candidate.address.strip().lower().replace("-", ":") == normalized),
        None,
    )
    if device is None:
        raise HTTPException(status_code=404, detail="Native speaker was not found")
    if not (device.trusted and device.is_audio_sink and device.connected):
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
        raise HTTPException(status_code=409, detail=str(error)) from error
    publish = getattr(request.app.state, "publish_native_speaker", None)
    if publish:
        await publish(normalized)
    return native_speaker_record(request, device)


# ==============================================================================
# Adapter Routes
# ==============================================================================

@router.get("/adapters", response_model=List[AdapterInfo])
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
async def start_scan(request: Request, payload: Optional[ScanRequest] = None):
    adapter_name = payload.adapter_name if payload else None
    await request.app.state.bt_manager.start_scan(adapter_name)
    return {"status": "ok", "scanning": True, "adapter": adapter_name or "all"}


@router.post("/scan/stop")
async def stop_scan(request: Request, payload: Optional[ScanRequest] = None):
    adapter_name = payload.adapter_name if payload else None
    await request.app.state.bt_manager.stop_scan(adapter_name)
    return {"status": "ok", "scanning": False, "adapter": adapter_name or "all"}


# ==============================================================================
# Device Routes
# ==============================================================================

@router.get("/devices", response_model=List[DeviceInfo])
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
        return {"status": "ok", "paired": success, "address": payload.address}
    except Exception as e:
        logger.error("Pairing error for %s: %s", payload.address, e)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/devices/{address}/connect")
async def connect_device(address: str, request: Request):
    try:
        success = await request.app.state.bt_manager.connect_device(address)
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
async def set_speaker_latency(address: str, payload: Dict[str, int], request: Request):
    offset = payload.get("latency_offset_ms", 0)
    success = request.app.state.multiroom_manager.set_latency_offset(address, offset)
    return {"status": "ok", "address": address, "latency_offset_ms": offset, "updated": success}
