import asyncio

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


@pytest.mark.asyncio
async def test_broadcast_send_failure_disconnects_client_without_raising():
    """A dead client is pruned from the connection list; other clients still receive."""

    class DeadSocket:
        async def send_text(self, message):
            raise RuntimeError("connection reset")

    class LiveSocket:
        def __init__(self):
            self.messages = []

        async def send_text(self, message):
            self.messages.append(message)

    dead, live = DeadSocket(), LiveSocket()
    ws_manager.active_connections.extend([dead, live])
    try:
        await ws_manager.broadcast("device_updated", {"address": "aa:bb:cc:dd:ee:01"})
    finally:
        ws_manager.disconnect(live)

    assert dead not in ws_manager.active_connections
    assert len(live.messages) == 1


@pytest.mark.asyncio
async def test_websocket_endpoints_remove_socket_on_disconnect():
    """Regression: disconnect paths always unregister the socket (finally-based cleanup)."""
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_text("ping")
            assert websocket.receive_text() == "pong"
            assert len(ws_manager.active_connections) == 1
        # After the context exits the socket must be pruned, not leaked.
        for _ in range(20):
            if not ws_manager.active_connections:
                break
            await asyncio.sleep(0.05)
    assert not ws_manager.active_connections


