from pathlib import Path
from unittest.mock import AsyncMock

from backend.bl_haos.bluetooth.models import DeviceInfo
from backend.bl_haos.api.ws import NATIVE_SPEAKER_UPDATED_EVENT
from backend.bl_haos.constants import (
    BEARER_PREFIX,
    EVENT_SPEAKER_UPDATED,
    NATIVE_API_PREFIX,
    NATIVE_BRIDGE_ID,
    NATIVE_BRIDGE_VERSION,
    WS_NATIVE_PATH,
    WS_PING,
    WS_POLICY_VIOLATION_CODE,
    WS_PONG,
    WS_PUBLIC_PATH,
)
from backend.bl_haos.main import app
from fastapi.testclient import TestClient
from starlette.status import HTTP_401_UNAUTHORIZED
from starlette.websockets import WebSocketDisconnect


def test_native_transport_uses_the_private_supervisor_network():
    """The private surfaces are real, authenticated, and named by shared constants."""
    schema_paths = set(app.openapi()["paths"])
    assert f"{NATIVE_API_PREFIX}/identity" in schema_paths
    assert f"{NATIVE_API_PREFIX}/speakers" in schema_paths

    with TestClient(app) as client:
        unauthenticated = client.get(f"{NATIVE_API_PREFIX}/identity")
        assert unauthenticated.status_code == HTTP_401_UNAUTHORIZED

        # The dashboard socket answers the shared heartbeat vocabulary.
        with client.websocket_connect(WS_PUBLIC_PATH) as websocket:
            websocket.send_text(WS_PING)
            assert websocket.receive_text() == WS_PONG

        # The native socket exists but refuses unauthenticated clients.
        try:
            with client.websocket_connect(WS_NATIVE_PATH) as websocket:
                websocket.receive_text()
        except WebSocketDisconnect as rejection:
            assert rejection.code == WS_POLICY_VIOLATION_CODE
        else:
            raise AssertionError("the native socket must reject unauthenticated clients")

    routes_source = Path("backend/bl_haos/api/routes.py").read_text(encoding="utf-8")
    assert "compare_digest" in routes_source
    assert "BL_HAOS_BRIDGE_TOKEN" not in routes_source
    assert NATIVE_SPEAKER_UPDATED_EVENT == EVENT_SPEAKER_UPDATED


def test_native_snapshot_filters_trusted_sinks(monkeypatch):
    speakers = [
        DeviceInfo(
            path="/speaker",
            adapter_path="/adapter",
            address="AA:BB:CC:DD:EE:FF",
            trusted=True,
            is_audio_sink=True,
            connected=True,
        ),
        DeviceInfo(
            path="/untrusted",
            adapter_path="/adapter",
            address="11:22:33:44:55:66",
            trusted=False,
            is_audio_sink=True,
        ),
    ]

    with TestClient(app) as client:
        token = app.state.config_store.settings.native_token
        headers = {"Authorization": f"{BEARER_PREFIX}{token}"}
        monkeypatch.setattr(app.state.bt_manager, "get_devices", lambda audio_only=True: speakers)
        identity = client.get(f"{NATIVE_API_PREFIX}/identity", headers=headers)
        assert identity.json() == {"bridge_id": NATIVE_BRIDGE_ID, "version": NATIVE_BRIDGE_VERSION}
        snapshot = client.get(f"{NATIVE_API_PREFIX}/speakers", headers=headers)

    assert snapshot.status_code == 200
    assert list(snapshot.json()["speakers"]) == ["aa:bb:cc:dd:ee:ff"]


def test_native_snapshot_includes_connected_speaker(monkeypatch):
    speakers = [
        DeviceInfo(
            path="/speaker",
            adapter_path="/adapter",
            address="EC:81:93:53:A9:16",
            trusted=False,
            is_audio_sink=True,
            connected=True,
            paired=True,
        ),
    ]

    with TestClient(app) as client:
        token = app.state.config_store.settings.native_token
        headers = {"Authorization": f"{BEARER_PREFIX}{token}"}
        monkeypatch.setattr(app.state.bt_manager, "get_devices", lambda audio_only=True: speakers)
        snapshot = client.get(f"{NATIVE_API_PREFIX}/speakers", headers=headers)

    assert snapshot.status_code == 200
    assert "ec:81:93:53:a9:16" in snapshot.json()["speakers"]
    assert snapshot.json()["speakers"]["ec:81:93:53:a9:16"]["connected"] is True
    assert snapshot.json()["speakers"]["ec:81:93:53:a9:16"]["trusted"] is True


def test_native_command_returns_bridge_record(monkeypatch):
    speaker = DeviceInfo(
        path="/speaker",
        adapter_path="/adapter",
        address="AA:BB:CC:DD:EE:FF",
        trusted=True,
        is_audio_sink=True,
        connected=True,
    )
    execute = AsyncMock()

    with TestClient(app) as client:
        token = app.state.config_store.settings.native_token
        headers = {"Authorization": f"{BEARER_PREFIX}{token}"}
        monkeypatch.setattr(app.state.ha_bridge, "execute", execute)
        monkeypatch.setattr(app.state.bt_manager, "get_devices", lambda audio_only=True: [speaker])
        acknowledged = client.post(
            f"{NATIVE_API_PREFIX}/speakers/aa:bb:cc:dd:ee:ff/command",
            headers=headers,
            json={"operation": "play"},
        )

    assert acknowledged.status_code == 200
    assert acknowledged.json()["address"] == "aa:bb:cc:dd:ee:ff"
    execute.assert_awaited_once_with("aa:bb:cc:dd:ee:ff", "play", volume=None, url=None)