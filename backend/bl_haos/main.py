"""FastAPI Application Entrypoint for BL-HAOS."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import native_speaker_record
from .api.routes import router as api_router
from .api.ws import HEALTH_EVENT, NATIVE_SPEAKER_UPDATED_EVENT, native_ws_manager, ws_manager
from .api.ws import router as ws_router
from .bluetooth.manager import BluetoothManager
from .bluetooth.reconnect import AutoReconnectEngine
from .config import ConfigStore
from .constants import (
    APP_DESCRIPTION,
    APP_SLUG,
    APP_TITLE,
    BEARER_PREFIX,
    COMPONENT_BLUETOOTH,
    COMPONENT_HEALTH,
    COMPONENT_LIFECYCLE,
    COMPONENT_NATIVE_BRIDGE,
    COMPONENT_PIPEWIRE,
    ENV_LOG_LEVEL,
    ENV_SUPERVISOR_TOKEN,
    EVENT_DEVICE_DISCOVERED,
    EVENT_DEVICE_UPDATED,
    EVENT_HEALTH_OBSERVATION,
    EVENT_TELEMETRY,
    LOGGER_NAME,
    LOG_FORMAT,
    LOG_LEVEL_INFO,
    PLAYBACK_STOPPED,
    RECOVERY_HEALTHY,
    RECOVERY_PENDING,
    ROOT_PATH,
    SOURCE_BLUEZ,
    SOURCE_STARTUP,
    STATIC_DIRECTORY_CANDIDATES,
    STATIC_INDEX_FILE,
    SUPERVISOR_DISCOVERY_TIMEOUT_SECONDS,
    SUPERVISOR_DISCOVERY_URL,
    VERSION,
)
from .diagnostics import DiagnosticsService
from .demo import DemoRuntime
from .ha.player import MediaPlayerBridge
from .health import FailureClass, HealthRegistry, HealthState, SpeakerState

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL_INFO),
    format=LOG_FORMAT,
)
logger = logging.getLogger("bl_haos.main")


async def _publish_supervisor_discovery(native_token: str, log_level: str) -> None:
    """Push the native bridge token to Supervisor so the integration can auto-connect."""
    supervisor_token = os.environ.get(ENV_SUPERVISOR_TOKEN)
    if not supervisor_token or not native_token:
        logger.debug(
            "Supervisor discovery skipped (supervisor_token present: %s, native_token present: %s)",
            bool(supervisor_token),
            bool(native_token),
        )
        return
    logger.debug("Publishing Supervisor discovery registration for BL-HAOS service")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                SUPERVISOR_DISCOVERY_URL,
                headers={"Authorization": f"{BEARER_PREFIX}{supervisor_token}"},
                json={"service": APP_SLUG, "config": {"token": native_token, "log_level": log_level}},
                timeout=aiohttp.ClientTimeout(total=SUPERVISOR_DISCOVERY_TIMEOUT_SECONDS),
            ) as response:
                if response.status >= 400:
                    logger.warning("Supervisor discovery registration failed: HTTP %s", response.status)
                else:
                    logger.debug("Supervisor discovery registration succeeded (HTTP %s)", response.status)
    except (aiohttp.ClientError, TimeoutError) as err:
        logger.warning("Supervisor discovery registration failed: %s", err)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting BL-HAOS backend daemon...")
    health = HealthRegistry()
    app.state.health_registry = health
    diagnostics = DiagnosticsService(health)
    app.state.diagnostics = diagnostics

    async def _publish_health_telemetry(snapshot):
        logger.debug(
            "Health telemetry broadcast: status=%s, lifecycle=%s",
            snapshot.status.value,
            snapshot.lifecycle.value,
        )
        event = diagnostics.record_event(
            EVENT_HEALTH_OBSERVATION,
            component=COMPONENT_HEALTH,
            detail={"status": snapshot.status.value, "lifecycle": snapshot.lifecycle.value},
            recovery=RECOVERY_HEALTHY if snapshot.status == HealthState.HEALTHY else RECOVERY_PENDING,
        )
        await ws_manager.broadcast(EVENT_TELEMETRY, event)

    health.add_listener(_publish_health_telemetry)
    await health.publish(ws_manager)
    config_store = ConfigStore()
    app.state.config_store = config_store
    log_level = os.environ.get(ENV_LOG_LEVEL, config_store.settings.log_level).upper()
    logging.getLogger(LOGGER_NAME).setLevel(getattr(logging, log_level, getattr(logging, LOG_LEVEL_INFO)))
    logger.debug(
        "Configuration loaded: demo_mode=%s, log_level=%s, speakers_count=%d",
        config_store.settings.demo_mode,
        log_level.lower(),
        len(config_store.settings.speakers),
    )
    asyncio.create_task(_publish_supervisor_discovery(config_store.settings.native_token, log_level.lower()))

    if config_store.settings.demo_mode:
        logger.debug("Running in demo mode with scenario '%s'", config_store.settings.demo_scenario)
        demo_runtime = DemoRuntime(config_store.settings.demo_scenario, health)
        app.state.demo_runtime = demo_runtime
        app.state.bt_manager = demo_runtime
        app.state.ha_bridge = demo_runtime
        app.state.native_ws_manager = native_ws_manager
        async def _publish_demo_speaker(address: str) -> None:
            return None

        app.state.publish_native_speaker = _publish_demo_speaker
        await health.publish(ws_manager)
        yield
        health.set_lifecycle(HealthState.STOPPED)
        return

    bt_manager = BluetoothManager(health_registry=health)
    await bt_manager.initialize()
    app.state.bt_manager = bt_manager
    health.observe_component(
        COMPONENT_BLUETOOTH,
        HealthState.HEALTHY if bt_manager.bus is not None else HealthState.UNAVAILABLE,
        failure=None if bt_manager.bus is not None else FailureClass.DBUS_UNAVAILABLE,
        detail=None if bt_manager.bus is not None else "System D-Bus unavailable",
        source=SOURCE_BLUEZ,
    )

    async def _publish_native_speaker(address: str) -> None:
        device = next(
            (candidate for candidate in bt_manager.get_devices(audio_only=True)
             if candidate.address.strip().lower().replace("-", ":") == address.strip().lower().replace("-", ":")),
            None,
        )
        if device and (device.trusted or device.paired or device.connected) and device.is_audio_sink:
            logger.debug("Broadcasting native speaker update for %s", address)
            await native_ws_manager.broadcast(NATIVE_SPEAKER_UPDATED_EVENT, native_speaker_record(app, device))

    ha_bridge = MediaPlayerBridge(config_store=config_store, state_callback=_publish_native_speaker, health_registry=health)
    app.state.ha_bridge = ha_bridge
    app.state.publish_native_speaker = _publish_native_speaker
    app.state.native_ws_manager = native_ws_manager
    health.observe_component(COMPONENT_NATIVE_BRIDGE, HealthState.HEALTHY, required=False, source=SOURCE_STARTUP)

    health.observe_component(COMPONENT_PIPEWIRE, HealthState.UNKNOWN, required=False, source=SOURCE_STARTUP)

    # Wire event broadcaster to WebSocket manager and HA Discovery
    def _broadcast_bt_event(event_type: str, data):
        logger.debug("Bluetooth event received: %s (data=%s)", event_type, data)
        return ws_manager.broadcast(event_type, data)

    def _on_bt_event(event_type: str, data):
        tracked = app.state.event_tasks = getattr(app.state, "event_tasks", set())
        task = asyncio.create_task(_broadcast_bt_event(event_type, data))
        tracked.add(task)
        task.add_done_callback(tracked.discard)
        if event_type in (EVENT_DEVICE_UPDATED, EVENT_DEVICE_DISCOVERED) and hasattr(data, "address"):
            is_audio = getattr(data, "is_audio_sink", False)
            is_conn = getattr(data, "connected", False)
            is_trust = getattr(data, "trusted", False)
            is_paired = getattr(data, "paired", False)

            if is_audio and (is_trust or is_paired or is_conn):
                asyncio.create_task(_publish_native_speaker(data.address))
                if not is_conn:
                    asyncio.create_task(ha_bridge.execute(data.address, PLAYBACK_STOPPED))
                    ha_bridge.unregister_keepalive(data.address)
                reconnect_engine.register_speaker(data.address)
            if is_conn and is_audio:
                ha_bridge.register_keepalive(data.address)

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
            try:
                reconnect_engine.register_speaker(addr, preferred_adapter=spk.preferred_adapter)
            except ValueError:
                logger.warning("Ignoring invalid persisted reconnect speaker identifier")

    logger.debug("Starting AutoReconnectEngine and keepalive tasks...")
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
    title=APP_TITLE,
    description=APP_DESCRIPTION,
    version=VERSION,
    lifespan=lifespan,
    root_path=ROOT_PATH,
)


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(request: Request, exc: RequestValidationError):
    """Return stable validation failures without reflecting untrusted values."""
    return JSONResponse(status_code=422, content={"detail": "Invalid request payload"})

app.include_router(api_router)
app.include_router(ws_router)

# Mount static web frontend if built assets exist
for static_dir in (Path(candidate) for candidate in STATIC_DIRECTORY_CANDIDATES):
    if static_dir.exists() and (static_dir / STATIC_INDEX_FILE).exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
        break
