"""WebSocket Manager and Live Event Bus for Ingress Frontend."""

import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..constants import (
    EVENT_HEALTH,
    EVENT_SPEAKER_UPDATED,
    WS_HEALTH_DEFAULT_VERSION,
    WS_NATIVE_PATH,
    WS_PING,
    WS_PONG,
    WS_POLICY_VIOLATION_CODE,
    WS_PUBLIC_PATH,
)
from ..health import authorized_bearer, safe_detail

logger = logging.getLogger("bl_haos.api.ws")
router = APIRouter(tags=["websocket"])
NATIVE_SPEAKER_UPDATED_EVENT = EVENT_SPEAKER_UPDATED
HEALTH_EVENT = EVENT_HEALTH


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.debug("WebSocket client connected. Total clients: %d", len(self.active_connections))

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.debug("WebSocket client disconnected. Total clients: %d", len(self.active_connections))

    async def broadcast(self, event_type: str, data: Any):
        """Broadcast JSON message to all connected clients."""
        if not self.active_connections:
            return

        logger.debug("Broadcasting WebSocket event '%s' to %d clients", event_type, len(self.active_connections))
        message = {
            "event": event_type,
            "data": data if isinstance(data, (dict, list, str, int, float, bool)) else (
                data.model_dump(mode="json") if hasattr(data, "model_dump") else str(data)
            )
        }
        if event_type == HEALTH_EVENT and isinstance(message["data"], dict):
            message["version"] = message["data"].get("version", WS_HEALTH_DEFAULT_VERSION)
        raw_text = json.dumps(message)
        dead_connections = []

        for conn in self.active_connections:
            try:
                await conn.send_text(raw_text)
            except Exception as e:
                logger.warning("Failed to send WebSocket message to client: %s", safe_detail(e))
                dead_connections.append(conn)

        for dead in dead_connections:
            self.disconnect(dead)


ws_manager = ConnectionManager()
native_ws_manager = ConnectionManager()


@router.websocket(WS_PUBLIC_PATH)
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep-alive heartbeat & client messages
            text = await websocket.receive_text()
            if text == WS_PING:
                await websocket.send_text(WS_PONG)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("WebSocket error: %s", e)
    finally:
        ws_manager.disconnect(websocket)


@router.websocket(WS_NATIVE_PATH)
async def native_websocket_endpoint(websocket: WebSocket):
    """Serve native speaker updates on the private Supervisor network."""
    expected = websocket.app.state.config_store.settings.native_token
    if not authorized_bearer(websocket.headers.get("authorization"), expected):
        logger.debug("Native WebSocket connection rejected: authentication required")
        await websocket.close(code=WS_POLICY_VIOLATION_CODE, reason="Native bridge authentication required")
        return
    await native_ws_manager.connect(websocket)
    logger.debug("Native WebSocket client connected successfully")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.debug("Native WebSocket client disconnected cleanly")
    except Exception as error:
        logger.debug("Native WebSocket closed: %s", safe_detail(error))
    finally:
        native_ws_manager.disconnect(websocket)
