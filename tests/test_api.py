import asyncio
import inspect
import json
import logging
from urllib.parse import quote

from backend.bl_haos.main import app
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock
from unittest.mock import Mock

import pytest


def test_api_health():
    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in {"healthy", "degraded", "unavailable"}
        assert data["service"] == "BL-HAOS"
        assert "adapters_count" in data
        assert data["health"]["version"] == 1


def test_native_diagnostics_are_sanitized(monkeypatch):
    with TestClient(app) as client:
        response = client.get("/api/diagnostics/native")

    assert response.status_code == 200
    diagnostics = response.json()
    assert diagnostics["bridge_version"] == 1
    assert "credential" not in str(diagnostics).lower()


def test_native_transport_requires_credential():
    with TestClient(app) as client:
        assert client.get("/api/native/identity").status_code == 401
        assert client.get("/api/native/speakers").status_code == 401


def test_native_auth_failures_are_generic_and_never_reflect_the_expected_token():
    with TestClient(app) as client:
        token = app.state.config_store.settings.native_token
        headers_to_try = [
            {},
            {"Authorization": "Basic not-a-bearer"},
            {"Authorization": "Bearer wrong"},
            {"Authorization": f"Bearer {token[:-1]}x"},
        ]
        responses = [client.get("/api/native/identity", headers=headers) for headers in headers_to_try]

    assert {response.status_code for response in responses} == {401}
    assert {response.json()["detail"] for response in responses} == {
        "Native bridge authentication required"
    }
    assert all(token not in response.text for response in responses)


def test_settings_never_serialize_token_or_accept_control_fields():
    with TestClient(app) as client:
        token = app.state.config_store.settings.native_token
        settings = client.get("/api/settings")
        response = client.put(
            "/api/settings/speakers/10:22:33:44:55:66",
            json={"native_token": "attacker-controlled", "default_volume": 101},
        )

    assert settings.status_code == 200
    assert "native_token" not in settings.json()
    assert token not in settings.text
    assert response.status_code == 422

def test_api_adapters_and_scan():
    with TestClient(app) as client:
        # Simulate an adapter added
        app.state.bt_manager._on_interfaces_added("/org/bluez/hci0", {
            "org.bluez.Adapter1": {
                "Address": "00:11:22:33:44:55",
                "Name": "Controller",
                "Powered": True,
                "Discovering": False,
            }
        })

        resp = client.get("/api/adapters")
        assert resp.status_code == 200
        adapters = resp.json()
        assert len(adapters) > 0
        assert adapters[0]["interface"] == "hci0"

        # Test scan start
        scan_resp = client.post("/api/scan/start", json={"adapter_name": "hci0"})
        assert scan_resp.status_code == 200
        assert scan_resp.json()["scanning"] is True

        # Test scan stop
        stop_resp = client.post("/api/scan/stop", json={"adapter_name": "hci0"})
        assert stop_resp.status_code == 200
        assert stop_resp.json()["scanning"] is False

def test_api_devices_and_settings():
    with TestClient(app) as client:
        dev_addr = "10:22:33:44:55:66"
        app.state.bt_manager._on_interfaces_added("/org/bluez/hci0/dev_10_22_33_44_55_66", {
            "org.bluez.Device1": {
                "Address": dev_addr,
                "Name": "Test Speaker",
                "Adapter": "/org/bluez/hci0",
                "UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb"],
                "Class": 0x240414,
                "Paired": False,
                "Trusted": False,
                "Connected": False,
            }
        })

        # Get devices
        resp = client.get("/api/devices")
        assert resp.status_code == 200
        devices = resp.json()
        assert len(devices) > 0

        # Update settings for device
        update_resp = client.put(
            f"/api/settings/speakers/{dev_addr}",
            json={"custom_alias": "Living Room Soundbar", "default_volume": 80, "auto_reconnect": True}
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["custom_alias"] == "Living Room Soundbar"
        assert update_resp.json()["default_volume"] == 80

        # Verify settings endpoint
        settings_resp = client.get("/api/settings")
        assert settings_resp.status_code == 200
        settings = settings_resp.json()
        assert dev_addr.lower() in settings["speakers"]


def test_pair_publishes_native_speaker():
    with TestClient(app) as client:
        dev_addr = "22:33:44:55:66:77"
        app.state.bt_manager._on_interfaces_added("/org/bluez/hci0/dev_22_33_44_55_66_77", {
            "org.bluez.Device1": {
                "Address": dev_addr,
                "Name": "Native Speaker",
                "Adapter": "/org/bluez/hci0",
                "UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb"],
                "Class": 0x240414,
                "Paired": False,
                "Trusted": False,
                "Connected": False,
            }
        })
        published = []
        pairing_calls = []

        async def publish(address):
            published.append(address)

        app.state.publish_native_speaker = publish

        async def pair(address, pin=None):
            pairing_calls.append((address, pin))
            device = app.state.bt_manager.get_device_by_address(address)
            device._properties["Trusted"] = True
            device._properties["Paired"] = True
            return True

        app.state.bt_manager.pair_and_trust = pair
        response = client.post("/api/devices/pair", json={"address": dev_addr, "pin": "4321"})

        assert response.status_code == 200
        assert published == [dev_addr]
        # The operator's PIN is what the pairing window answers with, so it must
        # reach the manager instead of a hardcoded default (THREAT-MODEL.md, T3).
        assert pairing_calls == [(dev_addr, "4321")]


def test_native_speaker_record_includes_the_playback_timeline(monkeypatch):
    """HA draws the progress bar from position/duration in the native payload."""
    from backend.bl_haos.api.routes import native_speaker_record
    from backend.bl_haos.bluetooth.constants import A2DP_SINK_UUID, DEVICE_INTERFACE

    dev_addr = "aa:bb:cc:dd:ee:11"
    bridge = Mock()
    bridge.get_state.return_value = "playing"
    bridge.get_volume.return_value = 40
    bridge.get_timeline.return_value = {
        "position": 12,
        "duration": 120,
        "position_updated_at": 1_800_000_000,
    }
    with TestClient(app) as client:
        monkeypatch.setattr(app.state, "ha_bridge", bridge, raising=False)
        app.state.bt_manager._on_interfaces_added(
            "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_11",
            {
                DEVICE_INTERFACE: {
                    "Address": "AA:BB:CC:DD:EE:11",
                    "Name": "Timeline Speaker",
                    "Adapter": "/org/bluez/hci0",
                    "UUIDs": [A2DP_SINK_UUID],
                    "Class": 0x240414,
                    "Paired": True,
                    "Trusted": True,
                    "Connected": True,
                }
            },
        )
        device = app.state.bt_manager.get_device_by_address(dev_addr).to_info()
        record = native_speaker_record(app, device)

    assert {"state", "volume", "position", "duration", "position_updated_at"} <= set(record["playback"])


def test_native_queue_snapshot_bounds_timeline_numbers():
    from backend.bl_haos.api.routes import native_queue_snapshot

    class QueueBridge:
        @staticmethod
        def get_queue(_address):
            return {
                "status": "playing",
                "playlist": "Bounded queue",
                "position": -1,
                "duration": 315_576_001,
                "position_updated_at": 4_102_444_801,
                "index": 1,
                "count": 1,
                "shuffle": False,
                "repeat": "off",
            }

    queue = native_queue_snapshot(QueueBridge(), "aa:bb:cc:dd:ee:11")

    assert queue is not None
    assert queue["position"] is None
    assert queue["duration"] is None
    assert queue["position_updated_at"] is None

def test_offline_speaker_stays_published_as_unavailable():
    """A trusted speaker that goes offline stays in the snapshot, unavailable.

    The integration turns that into "entity unavailable". Dropping the record
    instead (which the bridge used to do) removed the address from the snapshot,
    so the entity was deleted from Home Assistant rather than marked unavailable.
    """
    from backend.bl_haos.api.routes import native_speaker_record
    from backend.bl_haos.bluetooth.constants import A2DP_SINK_UUID, DEVICE_INTERFACE
    from backend.bl_haos.constants import BEARER_PREFIX

    dev_addr = "aa:bb:cc:dd:ee:21"
    dev_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_21"
    with TestClient(app) as client:
        app.state.bt_manager._on_interfaces_added(
            dev_path,
            {
                DEVICE_INTERFACE: {
                    "Address": "AA:BB:CC:DD:EE:21",
                    "Name": "Offline Speaker",
                    "Adapter": "/org/bluez/hci0",
                    "UUIDs": [A2DP_SINK_UUID],
                    "Class": 0x240414,
                    "Paired": True,
                    "Trusted": True,
                    "Connected": True,
                }
            },
        )
        # BlueZ withdraws the object: the speaker is switched off.
        app.state.bt_manager._on_interfaces_removed(dev_path, [DEVICE_INTERFACE])
        device = app.state.bt_manager.get_device_by_address(dev_addr).to_info()
        record = native_speaker_record(app, device)
        token = app.state.config_store.settings.native_token
        headers = {"Authorization": f"{BEARER_PREFIX}{token}"}
        snapshot = client.get("/api/native/speakers", headers=headers).json()

    assert device.detached is True
    assert record["available"] is False
    assert record["trusted"] is True
    assert dev_addr in snapshot["speakers"]
    assert snapshot["speakers"][dev_addr]["available"] is False




def test_pairing_failure_surfaces_a_bounded_bluez_reason():
    """Operators need the real BlueZ reason instead of a generic failure string."""
    dev_addr = "aa:bb:cc:dd:ee:09"

    async def pair(_address, _pin=None):
        raise RuntimeError("org.bluez.Error.Failed br-connection-page-timeout")

    with TestClient(app) as client:
        app.state.bt_manager.pair_and_trust = pair
        response = client.post("/api/devices/pair", json={"address": dev_addr, "pin": None})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail.startswith("Pairing failed:")
    assert "br-connection-page-timeout" in detail


def test_pairing_failure_redacts_credentials_in_reason():
    """Surfaced failure detail must not leak tokens or URLs."""
    dev_addr = "aa:bb:cc:dd:ee:0a"

    async def pair(_address, _pin=None):
        raise RuntimeError("denied token=supersecret123 at https://bridge.local/pair?t=abc")

    with TestClient(app) as client:
        app.state.bt_manager.pair_and_trust = pair
        response = client.post("/api/devices/pair", json={"address": dev_addr, "pin": None})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "supersecret123" not in detail
    assert "https://" not in detail


def test_media_type_accepts_home_assistant_content_types():
    """HA sends content types like 'music'; they must not 422 a play_media call."""
    from backend.bl_haos.health import validate_media_type

    for value in ("music", "audio/mpeg", "video/mp4", "channel", "tvshow", "playlist", "episode"):
        assert validate_media_type(value) == value
    assert validate_media_type(None) is None


def test_media_type_still_rejects_unsafe_values():
    from backend.bl_haos.health import validate_media_type

    for value in ("", "   ", "a" * 200, "music; rm -rf /", "audio/mpeg\nx", "javascript:alert(1)"):
        with pytest.raises(ValueError, match="Media type"):
            validate_media_type(value)


def test_native_play_media_accepts_ha_content_type(monkeypatch):
    """Regression: HA content types must reach the player instead of a 422/500."""
    with TestClient(app) as client:
        dev_addr = "10:22:33:44:55:66"
        app.state.bt_manager._on_interfaces_added(f"/org/bluez/hci0/dev_{dev_addr.replace(':', '_')}", {
            "org.bluez.Device1": {
                "Address": dev_addr,
                "Name": "Test Speaker",
                "Adapter": "/org/bluez/hci0",
                "UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb"],
                "Class": 0x240414,
                "Paired": True,
                "Trusted": True,
                "Connected": True,
            }
        })
        token = app.state.config_store.settings.native_token
        execute = AsyncMock()
        monkeypatch.setattr(app.state.ha_bridge, "execute", execute)
        response = client.post(
            f"/api/native/speakers/{dev_addr}/command",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "operation": "play_media",
                "url": "https://example.test/audio.mp3",
                "media_type": "music",
            },
        )

        assert response.status_code == 200, response.text
        execute.assert_awaited_once_with(
            dev_addr, "play_media", volume=None, url="https://example.test/audio.mp3"
        )


@pytest.mark.parametrize("failure", [None, "player", "fragments", "unexpected", "connect", "connect_false", "retry", "exhausted"])
@pytest.mark.parametrize("media_type", ["audio/mpeg", "labelhostsentinel.example/labelpathsentinel", "labeltokensentinel"])
def test_native_play_media_redact_logs_and_errors(monkeypatch, caplog, failure, media_type):
    from backend.bl_haos.ha.player import MediaPlayerError

    sentinels = (
        "routehostsentinel.example", "route_path_sentinel", "routequerykeysentinel",
        "routequeryvaluesentinel", "routeusersentinel", "routepasswordsentinel", "routetokensentinel",
    )
    url = (
        f"https://{sentinels[0]}/{sentinels[1]}?{sentinels[2]}={sentinels[3]}"
        f"&user={sentinels[4]}&password={sentinels[5]}&token={sentinels[6]}"
    )
    address = "10:22:33:44:55:66"
    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.bt_manager._on_interfaces_added("/org/bluez/hci0/dev_10_22_33_44_55_66", {
            "org.bluez.Device1": {
                "Address": address, "Name": "Sentinel Speaker", "Adapter": "/org/bluez/hci0",
                "UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb"],
                "Paired": True, "Trusted": True, "Connected": True,
            }
        })
        execute = AsyncMock()
        reconnect = AsyncMock(return_value=True)
        if failure in {"player", "fragments"}:
            execute.side_effect = MediaPlayerError(url if failure == "player" else " ".join(sentinels))
        elif failure == "unexpected":
            execute.side_effect = RuntimeError(url)
        elif failure in {"connect", "connect_false", "retry", "exhausted"}:
            missing = MediaPlayerError("Connected Bluetooth audio sink is unavailable " + " ".join(sentinels))
            missing.__cause__ = RuntimeError(url)
            execute.side_effect = [missing, MediaPlayerError(" ".join(sentinels))] if failure == "retry" else missing
            if failure == "connect":
                reconnect.side_effect = RuntimeError(" ".join(sentinels))
            elif failure == "connect_false":
                reconnect.return_value = False
        monkeypatch.setattr(app.state.ha_bridge, "execute", execute)
        monkeypatch.setattr(app.state.bt_manager, "connect_device", reconnect)
        monkeypatch.setattr("backend.bl_haos.api.routes.A2DP_SINK_RETRY_INTERVAL", 0)
        caplog.set_level(logging.DEBUG, logger="bl_haos")
        caplog.set_level(logging.DEBUG, logger="backend.bl_haos")
        caplog.clear()
        response = client.post(
            f"/api/native/speakers/{address}/command",
            headers={"Authorization": f"Bearer {app.state.config_store.settings.native_token}"},
            json={"operation": "play_media", "url": url, "media_type": media_type},
        )
        assert response.status_code == (409 if failure else 200)
        assert execute.await_args_list[0].args == (address, "play_media")
        assert all(call.kwargs == {"volume": None, "url": url} for call in execute.await_args_list)
        if failure not in {"connect", "connect_false", "retry", "exhausted"}:
            execute.assert_awaited_once_with(address, "play_media", volume=None, url=url)
            reconnect.assert_not_awaited()
        else:
            reconnect.assert_awaited_once_with(address, reset_existing=True)
        assert "call enter function=" in caplog.text
        assert "play_media" in caplog.text
        for sentinel in (url, *sentinels, "labelhostsentinel", "labelpathsentinel", "labeltokensentinel"):
            assert sentinel not in caplog.text + response.text
        if failure == "connect_false":
            import traceback
            from fastapi import HTTPException
            from starlette.requests import Request
            from backend.bl_haos.api.routes import NativeCommandRequest, command_native_speaker

            execute.reset_mock()
            reconnect.reset_mock()
            payload = NativeCommandRequest(operation="play_media", url=url, media_type=media_type)
            assert payload.media_type == media_type
            assert payload.url == url
            with pytest.raises(HTTPException) as caught:
                client.portal.call(
                    command_native_speaker, address, payload, Request({"type": "http", "app": app}),
                    f"Bearer {app.state.config_store.settings.native_token}",
                )
            assert caught.value.status_code == 409
            assert caught.value.detail == "Auto-reconnect failed: could not re-establish the Bluetooth connection"
            execute.assert_awaited_once_with(address, "play_media", volume=None, url=url)
            reconnect.assert_awaited_once_with(address, reset_existing=True)
            displayed = "".join(traceback.format_exception(caught.value))
            for sentinel in (url, *sentinels):
                assert sentinel not in displayed + caplog.text
            assert caught.value.__suppress_context__


@pytest.mark.parametrize("url", [
    "https://rejectusersentinel:rejectpasswordsentinel@rejecthostsentinel.example/rejectpathsentinel?token=rejecttokensentinel",
    "file:///rejectpathsentinel?token=rejecttokensentinel",
    "http://127.0.0.1/rejectpathsentinel?token=rejecttokensentinel",
])
def test_native_play_media_redact_rejected_urls(monkeypatch, caplog, url):
    with TestClient(app) as client:
        execute = AsyncMock()
        reconnect = AsyncMock()
        monkeypatch.setattr(app.state.ha_bridge, "execute", execute)
        monkeypatch.setattr(app.state.bt_manager, "connect_device", reconnect)
        caplog.set_level(logging.DEBUG, logger="bl_haos")
        caplog.set_level(logging.DEBUG, logger="backend.bl_haos")
        caplog.clear()
        response = client.post(
            "/api/native/speakers/10:22:33:44:55:66/command",
            headers={"Authorization": f"Bearer {app.state.config_store.settings.native_token}"},
            json={"operation": "play_media", "url": url},
        )
        assert response.status_code == 422
        execute.assert_not_awaited()
        reconnect.assert_not_awaited()
        for sentinel in (url, "rejectusersentinel", "rejectpasswordsentinel", "rejecthostsentinel", "rejectpathsentinel", "rejecttokensentinel"):
            assert sentinel not in caplog.text + response.text


def test_native_play_media_rejects_unsafe_url_without_side_effects(monkeypatch):
    with TestClient(app) as client:
        token = app.state.config_store.settings.native_token
        headers = {"Authorization": f"Bearer {token}"}
        execute = AsyncMock()
        reconnect = AsyncMock()
        monkeypatch.setattr(app.state.ha_bridge, "execute", execute)
        monkeypatch.setattr(app.state.bt_manager, "connect_device", reconnect)
        response = client.post(
            "/api/native/speakers/10:22:33:44:55:66/command",
            headers=headers,
            json={"operation": "play_media", "url": "file:///etc/passwd"},
        )

    assert response.status_code == 422
    assert "etc/passwd" not in response.text
    execute.assert_not_awaited()
    reconnect.assert_not_awaited()


def test_native_play_media_rejects_targets_that_are_the_bridge_or_a_dead_end(monkeypatch):
    """A media URL must not be usable as a request into the bridge itself."""
    from backend.bl_haos.health import validate_media_url

    with TestClient(app) as client:
        token = app.state.config_store.settings.native_token
        execute = AsyncMock()
        monkeypatch.setattr(app.state.ha_bridge, "execute", execute)
        responses = [
            client.post(
                "/api/native/speakers/10:22:33:44:55:66/command",
                headers={"Authorization": f"Bearer {token}"},
                json={"operation": "play_media", "url": url},
            )
            for url in (
                "http://127.0.0.1:8099/api/devices?audio_only=false",
                "http://localhost:8099/api/health",
                "http://2130706433:8099/api/health",
                "http://169.254.169.254/latest/meta-data/",
                "http://0.0.0.0/audio.mp3",
            )
        ]

    assert {response.status_code for response in responses} == {422}
    execute.assert_not_awaited()

    # The private LAN stays usable: Home Assistant serves TTS and local media
    # from a private address, so blocking RFC1918 wholesale would break the
    # product (see THREAT-MODEL.md, T2).
    assert validate_media_url("http://192.168.1.21:8123/api/tts_proxy/abc.mp3")
    assert validate_media_url("https://media.example.com/song.mp3")
    for blocked in ("http://[::ffff:127.0.0.1]:8099/", "http://224.0.0.1/", "http://localhost./x"):
        with pytest.raises(ValueError, match="loopback, link-local or multicast"):
            validate_media_url(blocked)


def test_api_rejects_invalid_address_and_adapter_before_manager_calls(monkeypatch):
    with TestClient(app) as client:
        manager_lookup = Mock()
        monkeypatch.setattr(app.state.bt_manager, "get_adapter_by_name", manager_lookup)
        invalid_address = client.post("/api/devices/pair", json={"address": "ff:ff:ff:ff:ff:ff"})
        invalid_adapter = client.post("/api/adapters/not-an-adapter/power", json={"powered": True})

    assert invalid_address.status_code == 422
    assert invalid_adapter.status_code == 422
    manager_lookup.assert_not_called()


def test_native_command_rejects_inconsistent_media_payload_without_execution(monkeypatch):
    with TestClient(app) as client:
        token = app.state.config_store.settings.native_token
        execute = AsyncMock()
        monkeypatch.setattr(app.state.ha_bridge, "execute", execute)
        response = client.post(
            "/api/native/speakers/10:22:33:44:55:66/command",
            headers={"Authorization": f"Bearer {token}"},
            json={"operation": "pause", "url": "https://example.test/audio.mp3"},
        )

    assert response.status_code == 422
    execute.assert_not_awaited()


def test_native_queue_start_reaches_bridge_session_with_sanitized_snapshot(monkeypatch, caplog):
    from backend.bl_haos.bluetooth.constants import A2DP_SINK_UUID, DEVICE_INTERFACE
    from backend.bl_haos.ha.player import MediaPlayerBridge

    class FakeQueueProcess:
        def __init__(self, returncode=None):
            self.returncode = returncode
            self.stdout = None
            self.stderr = None
            self.stdin = None

        async def wait(self):
            while self.returncode is None:
                await asyncio.sleep(0.001)
            return self.returncode

        async def communicate(self):
            return b"", b""

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = -9

    spawned = []

    async def process_factory(*args, **_kwargs):
        spawned.append(args[0])
        return FakeQueueProcess(returncode=0 if args[0] == "ffprobe" else None)

    async def sink_resolver(_address):
        return "bluez_output.aa_bb_cc_dd_ee_66.1"

    address = "aa:bb:cc:dd:ee:66"
    request_address = "AA-BB-CC-DD-EE-66"
    secret_url = "https://media.example.test/first.mp3?token=queue-route-secret"
    bridge = MediaPlayerBridge(sink_resolver=sink_resolver, process_factory=process_factory)
    bridge.warmup_attempts = 0
    published = []

    async def publish(speaker_address):
        published.append(speaker_address)

    caplog.set_level(logging.DEBUG, logger="bl_haos")
    caplog.set_level(logging.DEBUG, logger="backend.bl_haos")
    with TestClient(app) as client:
        app.state.bt_manager._on_interfaces_added(
            "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_66",
            {
                DEVICE_INTERFACE: {
                    "Address": "AA:BB:CC:DD:EE:66",
                    "Name": "Queue Speaker",
                    "Adapter": "/org/bluez/hci0",
                    "UUIDs": [A2DP_SINK_UUID],
                    "Class": 0x240414,
                    "Paired": True,
                    "Trusted": True,
                    "Connected": True,
                }
            },
        )
        monkeypatch.setattr(app.state, "ha_bridge", bridge)
        monkeypatch.setattr(app.state, "publish_native_speaker", publish, raising=False)
        token = app.state.config_store.settings.native_token
        endpoint = f"/api/native/speakers/{request_address}/command"
        body = {
            "operation": "queue_start",
            "queue_version": 1,
            "playlist": "Morning set",
            "items": [{"url": secret_url, "title": "First track", "artist": "Artist"}],
        }

        unauthenticated = client.post(endpoint, json=body)
        assert unauthenticated.status_code == 401
        assert bridge.playback_sessions == {}
        assert spawned == []

        response = client.post(
            endpoint,
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )
        assert response.status_code == 200, response.text
        record = response.json()
        queue = record["playback"]["queue"]
        session = bridge.playback_sessions[address]
        process_pair = bridge.active_processes[address]

        assert record["address"] == address
        assert record["playback"]["state"] == "playing"
        assert queue["status"] == "playing"
        assert queue["playlist"] == "Morning set"
        assert queue["title"] == "First track"
        assert queue["artist"] == "Artist"
        assert queue["index"] == queue["count"] == 1
        assert queue["shuffle"] is False
        assert queue["repeat"] == "off"
        assert session.items[session.index].title == "First track"
        assert session.processes == process_pair
        assert len(process_pair) == 2
        assert [command for command in spawned if command in {"ffmpeg", "pw-play"}] == [
            "ffmpeg", "pw-play"
        ]
        assert published == [address]
        assert secret_url not in response.text
        assert "queue-route-secret" not in caplog.text

        async def cleanup():
            await bridge.execute(address, "stop")
            await asyncio.gather(*tuple(bridge._background_tasks), return_exceptions=True)

        client.portal.call(cleanup)

        encoded_secret = "https%3A%2F%2Fprivate.example%2Fstream%3Ftoken%3Dencoded-queue-secret"
        encoded_response = client.post(
            endpoint,
            headers={"Authorization": f"Bearer {token}"},
            json={
                "operation": "queue_start",
                "queue_version": 1,
                "playlist": "Encoded title probe",
                "items": [{"url": f"https://media.example.test/{encoded_secret}"}],
            },
        )
        assert encoded_response.status_code == 200, encoded_response.text
        encoded_record = encoded_response.json()
        assert encoded_record["playback"]["queue"].get("title") is None
        assert encoded_record["playback"].get("title") is None
        assert "private.example" not in encoded_response.text
        assert "encoded-queue-secret" not in encoded_response.text
        assert "private.example" not in caplog.text
        assert "encoded-queue-secret" not in caplog.text
        client.portal.call(cleanup)

        deeply_encoded = "http://x"
        for _ in range(5):
            deeply_encoded = quote(deeply_encoded, safe="")
        deep_response = client.post(
            endpoint,
            headers={"Authorization": f"Bearer {token}"},
            json={
                "operation": "queue_start",
                "queue_version": 1,
                "playlist": "Deep encoding probe",
                "items": [{"url": f"https://media.example.test/{deeply_encoded}"}],
            },
        )
        assert deep_response.status_code == 200, deep_response.text
        deep_record = deep_response.json()
        assert deep_record["playback"].get("title") is None
        assert deep_record["playback"]["queue"].get("title") is None
        assert "http://x" not in deep_response.text
        assert deeply_encoded not in deep_response.text
        assert "http://x" not in caplog.text
        assert deeply_encoded not in caplog.text
        client.portal.call(cleanup)


def test_native_queue_start_does_not_reconnect_or_retry_sink_failure(monkeypatch):
    from backend.bl_haos.ha.player import MediaPlayerError

    address = "10:22:33:44:55:66"
    with TestClient(app) as client:
        app.state.bt_manager._on_interfaces_added(
            "/org/bluez/hci0/dev_10_22_33_44_55_66",
            {
                "org.bluez.Device1": {
                    "Address": address,
                    "Name": "Queue Speaker",
                    "Adapter": "/org/bluez/hci0",
                    "UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb"],
                    "Paired": True,
                    "Trusted": True,
                    "Connected": True,
                }
            },
        )
        start_queue = AsyncMock(side_effect=MediaPlayerError("audio sink is unavailable"))
        reconnect = AsyncMock(return_value=True)
        monkeypatch.setattr(app.state.ha_bridge, "start_queue", start_queue)
        monkeypatch.setattr(app.state.bt_manager, "connect_device", reconnect)
        token = app.state.config_store.settings.native_token
        response = client.post(
            f"/api/native/speakers/{address}/command",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "operation": "queue_start",
                "queue_version": 1,
                "playlist": "One item",
                "items": [{"url": "https://media.example.test/one.mp3", "title": "One"}],
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "Native playback command failed"
    start_queue.assert_awaited_once()
    reconnect.assert_not_awaited()


def test_native_queue_start_rejects_oversized_batch_before_dispatch(monkeypatch):
    from backend.bl_haos.ha.player import MAX_QUEUE_ITEMS
    from backend.bl_haos.api.routes import NativeCommandRequest
    from backend.bl_haos.main import MAX_NATIVE_COMMAND_BODY_BYTES
    from pydantic import ValidationError

    with TestClient(app) as client:
        start_queue = AsyncMock()
        monkeypatch.setattr(app.state.ha_bridge, "start_queue", start_queue)
        token = app.state.config_store.settings.native_token
        response = client.post(
            "/api/native/speakers/10:22:33:44:55:66/command",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "operation": "queue_start",
                "queue_version": 1,
                "playlist": "Too many items",
                "items": [
                    {"url": f"https://media.example.test/{index}.mp3"}
                    for index in range(MAX_QUEUE_ITEMS + 1)
                ],
            },
        )
        aggregate_body = {
            "operation": "queue_start",
            "queue_version": 1,
            "playlist": "Too much data",
            "items": [
                {"url": f"https://media.example.test/{'x' * 1100}"}
                for _ in range(MAX_QUEUE_ITEMS)
            ],
        }
        aggregate_response = client.post(
            "/api/native/speakers/10:22:33:44:55:66/command",
            headers={"Authorization": f"Bearer {token}"},
            json=aggregate_body,
        )
        raw_body = json.dumps({
            "operation": "queue_start",
            "queue_version": 1,
            "playlist": "Whitespace padding",
            "items": [{"url": "https://media.example.test/one.mp3"}],
        }).encode() + b" " * (MAX_NATIVE_COMMAND_BODY_BYTES + 1)
        raw_response = client.post(
            "/api/native/speakers/10:22:33:44:55:66/command",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            content=raw_body,
        )

    assert response.status_code == 422
    assert aggregate_response.status_code == 422
    assert raw_response.status_code == 413
    assert raw_response.content == b""
    with pytest.raises(ValidationError) as caught:
        NativeCommandRequest.model_validate(aggregate_body)
    assert "Queue request is too large" in str(caught.value)
    start_queue.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_command_body_limit_counts_chunked_body_without_content_length():
    from backend.bl_haos.main import (
        MAX_NATIVE_COMMAND_BODY_BYTES,
        NativeCommandBodyLimitMiddleware,
    )

    messages = iter((
        {"type": "http.request", "body": b"x" * MAX_NATIVE_COMMAND_BODY_BYTES, "more_body": True},
        {"type": "http.request", "body": b"x", "more_body": False},
    ))
    responses = []
    dispatched = False

    async def receive():
        return next(messages)

    async def send(message):
        responses.append(message)

    async def downstream(_scope, _receive, _send):
        nonlocal dispatched
        dispatched = True

    middleware = NativeCommandBodyLimitMiddleware(downstream)
    await middleware(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/native/speakers/aa:bb:cc:dd:ee:11/command",
            "headers": [],
        },
        receive,
        send,
    )

    assert not dispatched
    assert responses[0]["status"] == 413
    assert responses[1]["body"] == b""


def test_native_queue_control_contract_is_versioned_and_bounded():
    from backend.bl_haos.api.routes import NativeCommandRequest
    from pydantic import ValidationError

    request = NativeCommandRequest.model_validate(
        {"operation": "next", "queue_version": 1}
    )
    assert request.operation == "next"
    assert request.queue_version == 1

    invalid_payloads = [
        {"operation": "next", "queue_version": 2},
        {"operation": "set_shuffle", "queue_version": 1, "shuffle": "yes"},
        {"operation": "set_repeat", "queue_version": 1, "repeat": "loop"},
        {"operation": "queue_start", "queue_version": 2, "playlist": "x", "items": [{"url": "https://media.example.test/x"}]},
        {"operation": "next", "queue_version": 1, "url": "https://media.example.test/x"},
    ]
    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            NativeCommandRequest.model_validate(payload)


@pytest.mark.parametrize("operation", ["next", "previous"])
def test_native_queue_navigation_is_single_attempt_and_never_reconnects(monkeypatch, caplog, operation):
    from backend.bl_haos.ha.player import MediaPlayerError

    address = "10:22:33:44:55:67"
    with TestClient(app) as client:
        app.state.bt_manager._on_interfaces_added(
            "/org/bluez/hci0/dev_10_22_33_44_55_67",
            {
                "org.bluez.Device1": {
                    "Address": address,
                    "Name": "Queue Speaker",
                    "Adapter": "/org/bluez/hci0",
                    "UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb"],
                    "Paired": True,
                    "Trusted": True,
                    "Connected": True,
                }
            },
        )
        control_queue = AsyncMock(side_effect=MediaPlayerError("audio sink is unavailable"))
        execute = AsyncMock()
        reconnect = AsyncMock(return_value=True)
        monkeypatch.setattr(app.state.ha_bridge, "control_queue", control_queue, raising=False)
        monkeypatch.setattr(app.state.ha_bridge, "execute", execute)
        monkeypatch.setattr(app.state.bt_manager, "connect_device", reconnect)
        token = app.state.config_store.settings.native_token
        response = client.post(
            f"/api/native/speakers/{address}/command",
            headers={"Authorization": f"Bearer {token}"},
            json={"operation": operation, "queue_version": 1},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "Native playback command failed"
    control_queue.assert_awaited_once_with(address, operation, shuffle=None, repeat=None)
    execute.assert_not_awaited()
    reconnect.assert_not_awaited()
    assert "audio sink is unavailable" not in caplog.text + response.text


def test_native_queue_controls_keep_bearer_and_trusted_sink_checks(monkeypatch):
    address = "10:22:33:44:55:68"
    with TestClient(app) as client:
        app.state.bt_manager._on_interfaces_added(
            "/org/bluez/hci0/dev_10_22_33_44_55_68",
            {
                "org.bluez.Device1": {
                    "Address": address,
                    "Name": "Untrusted Speaker",
                    "Adapter": "/org/bluez/hci0",
                    "UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb"],
                    "Paired": True,
                    "Trusted": False,
                    "Connected": True,
                }
            },
        )
        control_queue = AsyncMock()
        monkeypatch.setattr(app.state.ha_bridge, "control_queue", control_queue, raising=False)
        endpoint = f"/api/native/speakers/{address}/command"
        body = {"operation": "previous", "queue_version": 1}
        unauthenticated = client.post(endpoint, json=body)
        token = app.state.config_store.settings.native_token
        untrusted = client.post(endpoint, headers={"Authorization": f"Bearer {token}"}, json=body)

    assert unauthenticated.status_code == 401
    assert untrusted.status_code == 409
    control_queue.assert_not_awaited()


def test_ingress_queue_control_uses_addon_route_without_native_bearer(monkeypatch):
    address = "10:22:33:44:55:69"
    with TestClient(app) as client:
        app.state.bt_manager._on_interfaces_added(
            "/org/bluez/hci0/dev_10_22_33_44_55_69",
            {
                "org.bluez.Device1": {
                    "Address": address,
                    "Name": "Ingress Queue Speaker",
                    "Adapter": "/org/bluez/hci0",
                    "UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb"],
                    "Paired": True,
                    "Trusted": True,
                    "Connected": True,
                }
            },
        )
        control_queue = AsyncMock(return_value={"status": "playing"})
        monkeypatch.setattr(app.state.ha_bridge, "control_queue", control_queue, raising=False)
        response = client.post(
            f"/api/devices/{address}/queue/command",
            json={"queue_version": 1, "operation": "next"},
        )
        shuffle_response = client.post(
            f"/api/devices/{address}/queue/command",
            json={"queue_version": 1, "operation": "set_shuffle", "shuffle": True},
        )
        repeat_response = client.post(
            f"/api/devices/{address}/queue/command",
            json={"queue_version": 1, "operation": "set_repeat", "repeat": "all"},
        )
        token = app.state.config_store.settings.native_token
        secret_in_body = client.post(
            f"/api/devices/{address}/queue/command",
            json={"queue_version": 1, "operation": "next", "native_token": token},
        )

    assert response.status_code == 200, response.text
    assert response.json()["playback"]["state"] in {"idle", "playing", "paused"}
    assert token not in response.text
    assert shuffle_response.status_code == 200
    assert repeat_response.status_code == 200
    assert control_queue.await_args_list == [
        ((address, "next"), {"shuffle": None, "repeat": None}),
        ((address, "set_shuffle"), {"shuffle": True, "repeat": None}),
        ((address, "set_repeat"), {"shuffle": None, "repeat": "all"}),
    ]
    assert secret_in_body.status_code == 422
    assert token not in secret_in_body.text


def test_background_spawn_outside_the_loop_disposes_the_coroutine():
    """Regression: an event delivered off-loop must not leak an un-awaited coroutine.

    Bluetooth events can be dispatched from a caller that is not inside the event
    loop; scheduling a task there raised RuntimeError, the listener swallowed it,
    and the broadcast was lost while CPython later warned
    "coroutine 'ConnectionManager.broadcast' was never awaited".
    """
    from backend.bl_haos.main import _spawn

    coro = asyncio.sleep(0)

    assert _spawn(coro, "unit test") is None
    assert inspect.getcoroutinestate(coro) == inspect.CORO_CLOSED
    assert not getattr(app.state, "event_tasks", set())


def test_native_auth_still_rejects_ingress_header_alone():
    """The private native transport must never trust the Ingress header."""
    with TestClient(app) as client:
        response = client.get(
            "/api/native/identity",
            headers={"X-Ingress-Path": "/ingress/bl_haos"},
        )
    assert response.status_code == 401


def test_live_volume_route_updates_bridge_and_publishes(monkeypatch):
    with TestClient(app) as client:
        published = []

        async def publish(address):
            published.append(address)

        app.state.publish_native_speaker = publish
        response = client.post("/api/devices/10:22:33:44:55:66/volume", json={"volume": 45})

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "address": "10:22:33:44:55:66", "volume": 45}
        assert app.state.ha_bridge.get_volume("10:22:33:44:55:66") == 0.45
        assert published == ["10:22:33:44:55:66"]


def test_saving_a_speaker_volume_applies_it_to_the_speaker():
    """The volume saved in a speaker's settings is a level, not a number in a file.

    Saving it only stored it: the speaker stayed where it was while the settings
    slider, the dashboard card and the entity all claimed the new level.
    """
    with TestClient(app) as client:
        response = client.put(
            "/api/settings/speakers/10:22:33:44:55:77",
            json={"custom_alias": "Patio", "default_volume": 35},
        )

        assert response.status_code == 200
        assert response.json()["default_volume"] == 35
        assert app.state.ha_bridge.get_volume("10:22:33:44:55:77") == pytest.approx(0.35)


def test_live_volume_route_validates_address_and_level():
    with TestClient(app) as client:
        bad_address = client.post("/api/devices/not-a-mac/volume", json={"volume": 50})
        bad_volume = client.post("/api/devices/10:22:33:44:55:66/volume", json={"volume": 250})
        out_of_range = client.post("/api/devices/10:22:33:44:55:66/volume", json={"volume": -1})

    assert bad_address.status_code == 422
    assert bad_volume.status_code == 422
    assert out_of_range.status_code == 422


def test_removed_multiroom_routes_are_gone():
    """Multi-room was never wired to audio; its endpoints must not come back by accident.

    Unmatched GETs are a 404. The POST may answer 405 instead: when a built UI is
    present the Ingress static mount owns the fallthrough path and rejects verbs
    other than GET/HEAD. Either way the route must not be reachable.
    """
    with TestClient(app) as client:
        assert client.get("/api/multiroom/groups").status_code == 404
        assert client.get("/api/multiroom/clients").status_code == 404
        latency_post = client.post(
            "/api/multiroom/speakers/10:22:33:44:55:66/latency",
            json={"latency_offset_ms": 120},
        )
    assert latency_post.status_code in {404, 405}


def test_settings_reject_removed_latency_offset_field():
    with TestClient(app) as client:
        response = client.put(
            "/api/settings/speakers/10:22:33:44:55:66",
            json={"latency_offset_ms": 120},
        )
    assert response.status_code == 422
