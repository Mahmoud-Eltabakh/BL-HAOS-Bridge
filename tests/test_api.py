import pytest
from fastapi.testclient import TestClient
from backend.bl_haos.main import app

def test_api_health():
    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "BL-HAOS"
        assert "adapters_count" in data


def test_native_diagnostics_are_sanitized(monkeypatch):
    monkeypatch.setenv("BL_HAOS_BRIDGE_TOKEN", "test-credential")
    with TestClient(app) as client:
        response = client.get("/api/diagnostics/native")

    assert response.status_code == 200
    diagnostics = response.json()
    assert diagnostics["bridge_credential_present"] is True
    assert diagnostics["bridge_version"] == 1
    assert "test-credential" not in str(diagnostics)

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
        dev_addr = "11:22:33:44:55:66"
        app.state.bt_manager._on_interfaces_added("/org/bluez/hci0/dev_11_22_33_44_55_66", {
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
