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

from .api.routes import native_speaker_record, playback_envelope
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
    EVENT_PLAYBACK_UPDATED,
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
from .health import FailureClass, HealthRegistry, HealthState, SpeakerState, safe_detail

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


def _spawn(coro, description: str) -> asyncio.Task | None:
    """Schedule one background publish, or dispose of it when there is no loop.

    Bluetooth events can arrive from a caller that is not running the event
    loop. Creating a task there raises RuntimeError and abandons the coroutine,
    which used to drop the event silently and surface later as
    "coroutine 'ConnectionManager.broadcast' was never awaited". Closing the
    coroutine keeps the drop explicit, warning-free, and logged with the event
    that was lost.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        logger.warning("Dropped background task '%s': no running event loop", description)
        return None
    tracked = app.state.event_tasks = getattr(app.state, "event_tasks", set())
    task = loop.create_task(coro)
    tracked.add(task)

    def _on_done(completed: asyncio.Task) -> None:
        tracked.discard(completed)
        if completed.cancelled():
            return
        error = completed.exception()
        if error is not None:
            logger.warning("Background task '%s' failed: %s", description, safe_detail(error))

    task.add_done_callback(_on_done)
    return task


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
    _spawn(_publish_supervisor_discovery(config_store.settings.native_token, log_level.lower()), "supervisor discovery")

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
             if candidate.address.strip().lower().replace("-", ":").replace("_", ":") == address.strip().lower().replace("-", ":").replace("_", ":")),
            None,
        )
        # Only an operator-trusted speaker is published to Home Assistant.
        # Pairing alone is not consent: a device in radio range reaches
        # "paired" without the operator doing anything (THREAT-MODEL.md, T3).
        if device and device.trusted and device.is_audio_sink:
            logger.debug("Broadcasting native speaker update for %s", address)
            await native_ws_manager.broadcast(NATIVE_SPEAKER_UPDATED_EVENT, native_speaker_record(app, device))

    async def _publish_playback_state(address: str) -> None:
        """Publish a playback change to both surfaces.

        Home Assistant and the Ingress dashboard listen on different sockets, so a
        volume or state changed from the media_player entity has to be published on
        both, or the dashboard keeps showing the stale level. Only playback changes
        come through here; BlueZ property events still publish to Home Assistant
        alone and reach the dashboard as ``device_updated``.
        """
        await _publish_native_speaker(address)
        device = next(
            (candidate for candidate in bt_manager.get_devices(audio_only=True)
             if candidate.address.strip().lower().replace("-", ":").replace("_", ":") == address.strip().lower().replace("-", ":").replace("_", ":")),
            None,
        )
        if device is None:
            return
        await ws_manager.broadcast(
            EVENT_PLAYBACK_UPDATED,
            {
                # The device's own address, exactly as its record carries it: the
                # dashboard matches this event against the records it already has.
                "address": device.address,
                "playback": playback_envelope(app.state.ha_bridge, device.address),
            },
        )

    ha_bridge = MediaPlayerBridge(config_store=config_store, state_callback=_publish_playback_state, health_registry=health)
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
        _spawn(_broadcast_bt_event(event_type, data), f"broadcast {event_type}")
        if event_type in (EVENT_DEVICE_UPDATED, EVENT_DEVICE_DISCOVERED) and hasattr(data, "address"):
            is_audio = getattr(data, "is_audio_sink", False)
            is_conn = getattr(data, "connected", False)
            is_trust = getattr(data, "trusted", False)
            is_paired = getattr(data, "paired", False)

            if is_audio and not is_conn and (is_trust or is_paired):
                # A speaker we may be streaming to just went away; end the stream
                # instead of letting the decoder write into a dead sink.
                _spawn(ha_bridge.execute(data.address, PLAYBACK_STOPPED), f"stop playback for {data.address}")
                ha_bridge.unregister_keepalive(data.address)
            if is_audio and is_trust:
                # Publishing to Home Assistant and arming auto-reconnect are both
                # consequences of the operator trusting a speaker, never of a
                # device that merely paired or connected.
                _spawn(_publish_native_speaker(data.address), f"native speaker update for {data.address}")
                reconnect_engine.register_speaker(data.address)
                if is_conn:
                    ha_bridge.register_keepalive(data.address)

    bt_manager.add_event_listener(_on_bt_event)

    # Initialize and start AutoReconnectEngine
    reconnect_engine = AutoReconnectEngine(bt_manager, health_registry=health)
    app.state.reconnect_engine = reconnect_engine

    for device in bt_manager.get_devices():
        if device.trusted:
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
    ha_bridge.start_volume_watch()
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
