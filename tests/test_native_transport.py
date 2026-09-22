from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from backend.bl_haos.bluetooth.models import DeviceInfo
from backend.bl_haos.main import app


def test_native_transport_is_authenticated_and_uses_configured_token():
    routes = Path("backend/bl_haos/api/routes.py").read_text(encoding="utf-8")
    websocket = Path("backend/bl_haos/api/ws.py").read_text(encoding="utf-8")

    assert "/native/identity" in routes
    assert "/native/speakers" in routes
    assert "compare_digest" in routes
    assert "BL_HAOS_BRIDGE_TOKEN" in routes
    assert "/ws/native" in websocket
    assert "speaker_updated" in websocket


def test_native_snapshot_requires_a_credential_and_filters_trusted_sinks(monkeypatch):
    monkeypatch.setenv("BL_HAOS_BRIDGE_TOKEN", "test-credential")
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
        monkeypatch.setattr(app.state.bt_manager, "get_devices", lambda audio_only=True: speakers)
        assert client.get("/api/native/identity").status_code == 401
        identity = client.get("/api/native/identity", headers={"Authorization": "Bearer test-credential"})
        assert identity.json() == {"bridge_id": "bl_haos_native_bridge", "version": 1}
        snapshot = client.get("/api/native/speakers", headers={"Authorization": "Bearer test-credential"})

    assert snapshot.status_code == 200
    assert list(snapshot.json()["speakers"]) == ["aa:bb:cc:dd:ee:ff"]


def test_native_command_authenticates_before_lookup_and_returns_bridge_record(monkeypatch):
    monkeypatch.setenv("BL_HAOS_BRIDGE_TOKEN", "test-credential")
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
        monkeypatch.setattr(app.state.ha_bridge, "execute", execute)
        monkeypatch.setattr(app.state.bt_manager, "get_devices", lambda audio_only=True: [speaker])
        unauthenticated = client.post(
            "/api/native/speakers/aa:bb:cc:dd:ee:ff/command", json={"operation": "play"}
        )
        assert unauthenticated.status_code == 401
        assert execute.await_count == 0
        acknowledged = client.post(
            "/api/native/speakers/aa:bb:cc:dd:ee:ff/command",
            headers={"Authorization": "Bearer test-credential"},
            json={"operation": "play"},
        )

    assert acknowledged.status_code == 200
    assert acknowledged.json()["address"] == "aa:bb:cc:dd:ee:ff"
    execute.assert_awaited_once_with("aa:bb:cc:dd:ee:ff", "play", volume=None, url=None)