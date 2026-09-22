import pytest
from fastapi.testclient import TestClient
from backend.bl_haos.main import app
from backend.bl_haos.api.ws import ws_manager

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
