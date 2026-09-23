import pytest
from backend.bl_haos.api.ws import ws_manager
from backend.bl_haos.main import app
from fastapi.testclient import TestClient


def test_websocket_connection_and_heartbeat():
    client = TestClient(app)
    with client.websocket_connect("/ws") as websocket:
        websocket.send_text("ping")
        data = websocket.receive_text()
        assert data == "pong"

@pytest.mark.asyncio
async def test_websocket_broadcast():
    # Test broadcast queue without errors
    await ws_manager.broadcast("test_event", {"message": "hello world"})
    assert True


@pytest.mark.asyncio
async def test_websocket_telemetry_keeps_structured_event_envelope():
    class Socket:
        def __init__(self):
            self.messages = []

        async def send_text(self, message):
            self.messages.append(message)

    socket = Socket()
    ws_manager.active_connections.append(socket)
    await ws_manager.broadcast(
        "telemetry",
        {
            "version": 1,
            "name": "startup",
            "correlation_id": "corr-1",
            "component": "bridge",
            "recovery": "healthy",
        },
    )
    payload = __import__("json").loads(socket.messages[0])
    assert payload["event"] == "telemetry"
    assert payload["data"]["correlation_id"] == "corr-1"
    assert payload["data"]["version"] == 1
    ws_manager.active_connections.clear()
