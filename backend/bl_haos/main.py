"""FastAPI Application Entrypoint for BL-HAOS."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api.routes import native_speaker_record
from .api.routes import router as api_router
from .api.ws import HEALTH_EVENT, NATIVE_SPEAKER_UPDATED_EVENT, native_ws_manager, ws_manager
from .api.ws import router as ws_router
from .bluetooth.manager import BluetoothManager
from .bluetooth.reconnect import AutoReconnectEngine
from .config import ConfigStore
from .ha.player import MediaPlayerBridge
from .multiroom.manager import MultiroomManager
from .health import FailureClass, HealthRegistry, HealthState, SpeakerState

logging.basicConfig(level=logging.INFO, format="[bl-haos] %(asctime)s %(levelname)s [%(name)s]: %(message)s")
logger = logging.getLogger("bl_haos.main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting BL-HAOS backend daemon...")
    health = HealthRegistry()
    app.state.health_registry = health
    await health.publish(ws_manager)
    config_store = ConfigStore()
    app.state.config_store = config_store

    bt_manager = BluetoothManager()
    await bt_manager.initialize()
    app.state.bt_manager = bt_manager
    health.observe_component(
        "bluetooth",
        HealthState.HEALTHY if bt_manager.bus is not None else HealthState.UNAVAILABLE,
        failure=None if bt_manager.bus is not None else FailureClass.DBUS_UNAVAILABLE,
        detail=None if bt_manager.bus is not None else "System D-Bus unavailable",
        source="bluez",
    )

    async def _publish_native_speaker(address: str) -> None:
        device = next(
            (candidate for candidate in bt_manager.get_devices(audio_only=True)
             if candidate.address.strip().lower().replace("-", ":") == address.strip().lower().replace("-", ":")),
            None,
        )
        if device and (device.trusted or device.paired or device.connected) and device.is_audio_sink:
            await native_ws_manager.broadcast(NATIVE_SPEAKER_UPDATED_EVENT, native_speaker_record(app, device))

    ha_bridge = MediaPlayerBridge(config_store=config_store, state_callback=_publish_native_speaker, health_registry=health)
    app.state.ha_bridge = ha_bridge
    app.state.publish_native_speaker = _publish_native_speaker
    app.state.native_ws_manager = native_ws_manager
    health.observe_component("native_bridge", HealthState.HEALTHY, required=False, source="startup")

    # Initialize Multi-room Manager
    multiroom_manager = MultiroomManager(health_registry=health)
    app.state.multiroom_manager = multiroom_manager
    health.observe_component("pipewire", HealthState.UNKNOWN, source="startup")
    health.observe_component("snapcast", HealthState.UNKNOWN, required=False, source="startup")

    # Wire event broadcaster to WebSocket manager and HA Discovery
    def _on_bt_event(event_type: str, data):
        asyncio.create_task(ws_manager.broadcast(event_type, data))
        if event_type in ("device_updated", "device_discovered") and hasattr(data, "address"):
            is_audio = getattr(data, "is_audio_sink", False)
            is_conn = getattr(data, "connected", False)
            is_trust = getattr(data, "trusted", False)
            is_paired = getattr(data, "paired", False)

            if is_audio and (is_trust or is_paired or is_conn):
                asyncio.create_task(_publish_native_speaker(data.address))
                if not is_conn:
                    asyncio.create_task(ha_bridge.execute(data.address, "stop"))
                    ha_bridge.unregister_keepalive(data.address)
                reconnect_engine.register_speaker(data.address)
            if is_conn and is_audio:
                multiroom_manager.attach_speaker(data.address, getattr(data, "alias", None) or getattr(data, "name", None) or data.address)
                ha_bridge.register_keepalive(data.address)
            elif not is_conn and is_audio:
                multiroom_manager.detach_speaker(data.address)

    bt_manager.add_event_listener(_on_bt_event)

    # Initialize and start AutoReconnectEngine
    reconnect_engine = AutoReconnectEngine(bt_manager, health_registry=health)
    app.state.reconnect_engine = reconnect_engine

    for device in bt_manager.get_devices():
        if device.trusted or device.paired or device.connected:
            reconnect_engine.register_speaker(device.address)
        if device.connected and device.is_audio_sink:
            ha_bridge.register_keepalive(device.address)

    # Register known speakers from config
    for addr, spk in config_store.settings.speakers.items():
        if spk.auto_reconnect:
            reconnect_engine.register_speaker(addr, preferred_adapter=spk.preferred_adapter)

    await reconnect_engine.start()
    await ha_bridge.start_keepalive()
    health.set_lifecycle(HealthState.HEALTHY)
    await health.publish(ws_manager)
    logger.info("BL-HAOS backend daemon is ready.")

    yield

    # Shutdown
    logger.info("Stopping BL-HAOS backend daemon...")
    health.set_lifecycle(HealthState.STOPPING)
    await health.publish(ws_manager)
    await reconnect_engine.stop()
    await ha_bridge.async_shutdown()
    health.set_lifecycle(HealthState.STOPPED)
    await health.publish(ws_manager)


app = FastAPI(
    title="BL-HAOS Bluetooth Audio Adapter",
    description="High-fidelity Bluetooth Audio Adapter & Multi-room Streaming for Home Assistant OS",
    version="0.1.0",
    lifespan=lifespan,
    root_path="",
)

app.include_router(api_router)
app.include_router(ws_router)

# Mount static web frontend if built assets exist
for static_dir in [Path("web_ui/dist"), Path("/var/www/bl-haos"), Path("frontend/dist")]:
    if static_dir.exists() and (static_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
        break
