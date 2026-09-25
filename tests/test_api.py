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


def test_operator_diagnostics_and_support_bundle_are_bounded():
    with TestClient(app) as client:
        diagnostics = client.get("/api/diagnostics")
        bundle = client.get("/api/support/bundle")

    assert diagnostics.status_code == 200
    data = diagnostics.json()
    assert data["contract_version"] == 1
    assert data["lifecycle"] in {"healthy", "degraded", "stopping", "stopped"}
    assert "last_failure" in data
    assert bundle.status_code == 200
    assert bundle.headers["content-type"].startswith("application/json")
    assert "attachment" in bundle.headers["content-disposition"]
    assert bundle.json()["schema"] == "bl-haos.support-bundle"


def test_native_transport_requires_credential():
    with TestClient(app) as client:
        assert client.get("/api/native/identity").status_code == 401
        assert client.get("/api/native/speakers").status_code == 401


def test_recovery_contract_requires_native_auth_and_accepts_only_bounded_actions(monkeypatch):
    with TestClient(app) as client:
        assert client.get("/api/recovery").status_code == 401
        token = app.state.config_store.settings.native_token
        headers = {"Authorization": f"Bearer {token}"}
        contract = client.get("/api/recovery", headers=headers)
        assert contract.status_code == 200
        assert contract.json()["contract_version"] == 1
        invalid = client.post(
            "/api/recovery/actions",
            headers=headers,
            json={"action_id": "shell", "target": "aa:bb:cc:11:22:33"},
        )
        assert invalid.status_code == 422
        malformed = client.post(
            "/api/recovery/actions",
            headers=headers,
            json={"action_id": "retry_reconnect", "target": "not-a-device"},
        )
        assert malformed.status_code == 422

        connect = AsyncMock(return_value=True)
        monkeypatch.setattr(app.state.bt_manager, "connect_device", connect)
        result = client.post(
            "/api/recovery/actions",
            headers=headers,
            json={"action_id": "retry_reconnect", "target": "AA-BB-CC-11-22-33"},
        )
        assert result.status_code == 200
        assert result.json()["target"] == "aa:bb:cc:11:22:33"
        connect.assert_awaited_once_with("aa:bb:cc:11:22:33")


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

        async def publish(address):
            published.append(address)

        app.state.publish_native_speaker = publish
        app.state.bt_manager.pair_and_trust = lambda address: None

        async def pair(address):
            device = app.state.bt_manager.get_device_by_address(address)
            device._properties["Trusted"] = True
            device._properties["Paired"] = True
            return True

        app.state.bt_manager.pair_and_trust = pair
        response = client.post("/api/devices/pair", json={"address": dev_addr, "pin": None})

        assert response.status_code == 200
        assert published == [dev_addr]


def test_pairing_failure_surfaces_a_bounded_bluez_reason():
    """Operators need the real BlueZ reason instead of a generic failure string."""
    dev_addr = "aa:bb:cc:dd:ee:09"

    async def pair(_address):
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

    async def pair(_address):
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


def test_recovery_contract_is_readable_through_ingress_without_native_token():
    """The Ingress dashboard must be able to read recovery guidance (401 regression)."""
    with TestClient(app) as client:
        anonymous = client.get("/api/recovery")
        ingress = client.get("/api/recovery", headers={"X-Ingress-Path": "/ingress/bl_haos"})
        native_token = app.state.config_store.settings.native_token
        native = client.get("/api/recovery", headers={"Authorization": f"Bearer {native_token}"})

    assert anonymous.status_code == 401
    assert ingress.status_code == 200
    assert ingress.json()["contract_version"] == 1
    assert native.status_code == 200


def test_recovery_execution_is_blocked_without_ingress_or_native_credential(monkeypatch):
    with TestClient(app) as client:
        connect = AsyncMock(return_value=True)
        monkeypatch.setattr(app.state.bt_manager, "connect_device", connect)
        anonymous = client.post(
            "/api/recovery/actions",
            json={"action_id": "retry_reconnect", "target": "aa:bb:cc:11:22:33"},
        )
        ingress = client.post(
            "/api/recovery/actions",
            headers={"X-Ingress-Path": "/ingress/bl_haos"},
            json={"action_id": "retry_reconnect", "target": "aa:bb:cc:11:22:33"},
        )

    assert anonymous.status_code == 401
    assert ingress.status_code == 200
    assert ingress.json()["result"] == "succeeded"
    connect.assert_awaited_once_with("aa:bb:cc:11:22:33")


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


def test_live_volume_route_validates_address_and_level():
    with TestClient(app) as client:
        bad_address = client.post("/api/devices/not-a-mac/volume", json={"volume": 50})
        bad_volume = client.post("/api/devices/10:22:33:44:55:66/volume", json={"volume": 250})
        out_of_range = client.post("/api/devices/10:22:33:44:55:66/volume", json={"volume": -1})

    assert bad_address.status_code == 422
    assert bad_volume.status_code == 422
    assert out_of_range.status_code == 422


def test_settings_speaker_update_accepts_latency_offset(monkeypatch):
    with TestClient(app) as client:
        updated = client.put(
            "/api/settings/speakers/10:22:33:44:55:66",
            json={"latency_offset_ms": -250},
        )
        stored = client.get("/api/settings")

    assert updated.status_code == 200
    assert updated.json()["latency_offset_ms"] == -250
    assert stored.json()["speakers"]["10:22:33:44:55:66"]["latency_offset_ms"] == -250


def test_settings_reject_out_of_bounds_latency_offset():
    with TestClient(app) as client:
        response = client.put(
            "/api/settings/speakers/10:22:33:44:55:66",
            json={"latency_offset_ms": 99999},
        )
    assert response.status_code == 422


def test_settings_update_persists_latency_into_multiroom_manager(monkeypatch):
    with TestClient(app) as client:
        client.put(
            "/api/settings/speakers/30:44:55:66:77:88",
            json={"latency_offset_ms": 120},
        )
        app.state.bt_manager._on_interfaces_added("/org/bluez/hci0/dev_30_44_55_66_77_88", {
            "org.bluez.Device1": {
                "Address": "30:44:55:66:77:88",
                "Name": "Persisted Speaker",
                "Adapter": "/org/bluez/hci0",
                "UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb"],
                "Class": 0x240414,
                "Paired": True,
                "Trusted": True,
                "Connected": True,
            }
        })
        app.state.multiroom_manager.attach_speaker("30:44:55:66:77:88", "Persisted Speaker")
        clients = app.state.multiroom_manager.get_clients()
        match = [c for c in clients if c.speaker_address == "30:44:55:66:77:88"]

    assert match and match[0].latency_offset_ms == 120
