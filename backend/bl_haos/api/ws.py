"""WebSocket Manager and Live Event Bus for Ingress Frontend."""

import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..health import authorized_bearer, safe_detail

logger = logging.getLogger("bl_haos.api.ws")
router = APIRouter(tags=["websocket"])
NATIVE_SPEAKER_UPDATED_EVENT = "speaker_updated"
HEALTH_EVENT = "health"


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info("WebSocket client connected. Total clients: %d", len(self.active_connections))

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info("WebSocket client disconnected. Total clients: %d", len(self.active_connections))

    async def broadcast(self, event_type: str, data: Any):
        """Broadcast JSON message to all connected clients."""
        if not self.active_connections:
            return

        message = {
            "event": event_type,
            "data": data if isinstance(data, (dict, list, str, int, float, bool)) else (
                data.model_dump(mode="json") if hasattr(data, "model_dump") else str(data)
            )
        }
        if event_type == HEALTH_EVENT and isinstance(message["data"], dict):
            message["version"] = message["data"].get("version", 1)
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


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep-alive heartbeat & client messages
            text = await websocket.receive_text()
            if text == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error("WebSocket error: %s", e)
        ws_manager.disconnect(websocket)


@router.websocket("/ws/native")
async def native_websocket_endpoint(websocket: WebSocket):
    """Serve native speaker updates on the private Supervisor network."""
    expected = websocket.app.state.config_store.settings.native_token
    if not authorized_bearer(websocket.headers.get("authorization"), expected):
        await websocket.close(code=1008, reason="Native bridge authentication required")
        return
    await native_ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        native_ws_manager.disconnect(websocket)
    except Exception as error:
        logger.debug("Native WebSocket closed: %s", safe_detail(error))
        native_ws_manager.disconnect(websocket)
