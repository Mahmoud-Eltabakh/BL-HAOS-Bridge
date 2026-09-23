from backend.bl_haos.main import app
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock
from unittest.mock import Mock


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
