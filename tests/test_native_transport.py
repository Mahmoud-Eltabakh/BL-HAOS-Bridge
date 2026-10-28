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
        payload = identity.json()
        snapshot = client.get(f"{NATIVE_API_PREFIX}/speakers", headers=headers)

    assert payload["bridge_id"] == NATIVE_BRIDGE_ID
    assert payload["version"] == NATIVE_BRIDGE_VERSION
    assert payload["queue_version"] == 1
    # Identity carries a digest of the credential, never the credential.
    from backend.bl_haos.health import token_fingerprint

    assert payload["credential_fingerprint"] == token_fingerprint(token)
    assert token not in identity.text

    assert snapshot.status_code == 200
    assert list(snapshot.json()["speakers"]) == ["aa:bb:cc:dd:ee:ff"]


def test_native_snapshot_excludes_a_paired_but_untrusted_speaker(monkeypatch):
    """Pairing alone is not consent: a rogue peer must not become an entity.

    This mirrors the state that exposed the finding: a device in radio range
    reaches paired+connected by itself (PIN 0000, auto-confirmed agent), and the
    bridge used to publish it as a trusted speaker and accept commands for it.
    See THREAT-MODEL.md, T3.
    """
    speakers = [
        DeviceInfo(
            path="/rogue",
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
        refused = client.post(
            f"{NATIVE_API_PREFIX}/speakers/ec:81:93:53:a9:16/command",
            headers=headers,
            json={"operation": "play"},
        )

    assert snapshot.status_code == 200
    assert snapshot.json()["speakers"] == {}
    assert refused.status_code == 409


def test_native_snapshot_publishes_a_trusted_speaker(monkeypatch):
    speakers = [
        DeviceInfo(
            path="/speaker",
            adapter_path="/adapter",
            address="EC:81:93:53:A9:16",
            trusted=True,
            is_audio_sink=True,
            connected=True,
        ),
    ]

    with TestClient(app) as client:
        token = app.state.config_store.settings.native_token
        headers = {"Authorization": f"{BEARER_PREFIX}{token}"}
        monkeypatch.setattr(app.state.bt_manager, "get_devices", lambda audio_only=True: speakers)
        snapshot = client.get(f"{NATIVE_API_PREFIX}/speakers", headers=headers)

    assert snapshot.status_code == 200
    record = snapshot.json()["speakers"]["ec:81:93:53:a9:16"]
    assert record["trusted"] is True
    assert record["connected"] is True
    assert record["queue_version"] == 1


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


def test_native_snapshot_and_ingress_event_share_sanitized_queue_envelope(monkeypatch, caplog):
    from types import SimpleNamespace

    from backend.bl_haos.api.routes import native_speaker_record
    from backend.bl_haos.main import _playback_updated_payload

    url = "https://private.example.test/track.mp3?token=queue-event-secret"
    media_source_id = "media-source://private/library/item"
    raw_exception = "provider failed with https://private.example.test/?token=queue-event-secret"

    class QueueBridge:
        def get_state(self, _address):
            return "playing"

        def get_volume(self, _address):
            return 0.4

        def get_timeline(self, _address):
            return {
                "position": 12.0,
                "duration": 90.0,
                "position_updated_at": 10.0,
                "title": url,
                "artist": "Artist",
                "url": url,
                "media_source_id": media_source_id,
                "raw_exception": raw_exception,
            }

        def get_queue(self, _address):
            return {
                "status": "playing",
                "playlist": "Road set",
                "title": "Track title",
                "artist": "Artist",
                "position": 12.0,
                "duration": 90.0,
                "position_updated_at": 10.0,
                "index": 2,
                "count": 4,
                "shuffle": True,
                "repeat": "all",
                "error": raw_exception,
                "url": url,
                "media_source_id": media_source_id,
                "generation": 123,
                "raw_exception": raw_exception,
                "native_token": "queue-native-bearer-sentinel",
            }

    address = "aa:bb:cc:dd:ee:71"
    bridge = QueueBridge()
    speaker = DeviceInfo(
        path="/speaker",
        adapter_path="/adapter",
        address=address,
        trusted=True,
        is_audio_sink=True,
        connected=True,
    )
    source = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ha_bridge=bridge)))
    native_record = native_speaker_record(source, speaker)
    event = _playback_updated_payload(bridge, speaker)
    expected_queue = {
        "status", "playlist", "title", "artist", "position", "duration",
        "position_updated_at", "index", "count", "shuffle", "repeat", "error",
    }

    assert native_record["playback"] == event["playback"]
    assert event["address"] == address
    assert set(event["playback"]["queue"]) == expected_queue
    assert native_record["queue_version"] == 1
    assert event["playback"]["queue"]["error"] is None
    assert all(value not in str(native_record) + str(event) for value in (url, media_source_id, raw_exception))
    assert "queue-native-bearer-sentinel" not in str(native_record) + str(event)
    assert "123" not in str(native_record) + str(event)
    assert "queue-event-secret" not in caplog.text


def test_legacy_bridge_snapshot_does_not_infer_queue_support():
    from types import SimpleNamespace

    from backend.bl_haos.api.routes import native_speaker_record, playback_envelope

    class LegacyBridge:
        def get_state(self, _address):
            return "idle"

        def get_volume(self, _address):
            return None

        def get_timeline(self, _address):
            return {"position": None, "duration": None, "position_updated_at": None}

    envelope = playback_envelope(LegacyBridge(), "aa:bb:cc:dd:ee:72")
    speaker = DeviceInfo(
        path="/legacy",
        adapter_path="/adapter",
        address="aa:bb:cc:dd:ee:72",
        trusted=True,
        is_audio_sink=True,
        connected=True,
    )
    legacy_record = native_speaker_record(
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ha_bridge=LegacyBridge()))),
        speaker,
    )

    assert "queue" not in envelope
    assert "queue_version" not in legacy_record


def test_disconnect_invalidates_queue_before_stop_is_scheduled(monkeypatch):
    from backend.bl_haos.main import _invalidate_and_schedule_playback_stop

    events = []

    class Bridge:
        def _invalidate_playback_session(self, address):
            events.append(("invalidate", address))

        async def execute(self, address, operation):
            events.append(("execute", address, operation))

    def schedule(coroutine, description):
        events.append(("scheduled", description))
        coroutine.close()

    monkeypatch.setattr("backend.bl_haos.main._spawn", schedule)
    _invalidate_and_schedule_playback_stop(Bridge(), "aa:bb:cc:dd:ee:73")

    assert events[0] == ("invalidate", "aa:bb:cc:dd:ee:73")
    assert events[1][0] == "scheduled"