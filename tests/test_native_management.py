from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from backend.bl_haos.api.routes import router
from backend.bl_haos.bluetooth.models import AdapterInfo, DeviceInfo
from backend.bl_haos.main import app


ADDRESS = "10:22:33:44:55:66"
TOKEN_HEADER = lambda: {"Authorization": f"Bearer {app.state.config_store.settings.native_token}"}


def _device(address=ADDRESS, *, paired=False, trusted=False, connected=False):
    return DeviceInfo(
        path=f"/org/bluez/hci0/dev_{address.replace(':', '_')}",
        adapter_path="/org/bluez/hci0",
        adapter_name="hci0",
        address=address,
        name="Test Speaker",
        paired=paired,
        trusted=trusted,
        connected=connected,
        is_audio_sink=True,
    )


def test_native_management_auth_rejects_before_side_effects():
    with TestClient(app) as client:
        token = app.state.config_store.settings.native_token
        original_manager = app.state.bt_manager
        manager = Mock()
        manager.start_scan = AsyncMock()
        manager.stop_scan = AsyncMock()
        manager.pair_and_trust = AsyncMock()
        manager.connect_device = AsyncMock()
        manager.disconnect_device = AsyncMock()
        manager.remove_device = AsyncMock()
        manager.get_devices = Mock(return_value=[])
        manager.get_adapters = Mock(return_value=[])
        manager.get_adapter_by_name = Mock(return_value=None)
        app.state.bt_manager = manager
        calls = [
            ("get", "/api/native/devices", None),
            ("post", "/api/native/scan/start", {}),
            ("post", "/api/native/scan/stop", {}),
            ("post", "/api/native/devices/pair", {"address": ADDRESS, "pin": "8642"}),
            ("post", f"/api/native/devices/{ADDRESS}/connect", None),
            ("post", f"/api/native/devices/{ADDRESS}/disconnect", None),
            ("delete", f"/api/native/devices/{ADDRESS}", None),
            ("get", "/api/native/adapters", None),
            ("post", "/api/native/adapters/hci0/power", {"powered": True}),
            ("get", "/api/native/settings", None),
            ("put", f"/api/native/settings/speakers/{ADDRESS}", {"custom_alias": "Office"}),
            ("put", "/api/native/settings/system", {"default_codec": "sbc"}),
        ]
        try:
            for method, path, body in calls:
                for headers in ({}, {"Authorization": "Basic wrong"}, {"Authorization": f"Bearer {token}-wrong"}):
                    response = getattr(client, method)(path, json=body, headers=headers) if body is not None else getattr(client, method)(path, headers=headers)
                    assert response.status_code == 401, (method, path, response.text)
            manager.start_scan.assert_not_awaited()
            manager.stop_scan.assert_not_awaited()
            manager.pair_and_trust.assert_not_awaited()
            manager.connect_device.assert_not_awaited()
            manager.disconnect_device.assert_not_awaited()
            manager.remove_device.assert_not_awaited()
            manager.get_devices.assert_not_called()
            manager.get_adapters.assert_not_called()
        finally:
            app.state.bt_manager = original_manager


def test_native_scan_pair_connect_tracer_uses_authenticated_surface():
    class Manager:
        def __init__(self):
            self.device = _device()
            self.scan_started = False
            self.pair_calls = []
            self.connect_calls = []

        async def start_scan(self, adapter_name=None):
            self.scan_started = True

        async def stop_scan(self, adapter_name=None):
            self.scan_started = False

        def get_devices(self, audio_only=False):
            return [self.device]

        async def pair_and_trust(self, address, pin):
            self.pair_calls.append((address, pin))
            self.device.paired = True
            self.device.trusted = True
            return True

        async def connect_device(self, address, reset_existing=True):
            self.connect_calls.append((address, reset_existing))
            self.device.connected = True
            return True

    with TestClient(app) as client:
        original_manager = app.state.bt_manager
        original_bridge = app.state.ha_bridge
        manager = Manager()
        app.state.bt_manager = manager
        app.state.ha_bridge = None
        headers = TOKEN_HEADER()
        try:
            assert client.post("/api/native/scan/start", json={}, headers=headers).status_code == 200
            discovered = client.get("/api/native/devices", headers=headers)
            assert discovered.status_code == 200
            assert discovered.json()[0]["paired"] is False
            paired = client.post("/api/native/devices/pair", json={"address": ADDRESS, "pin": "8642"}, headers=headers)
            assert paired.status_code == 200
            connected = client.post(f"/api/native/devices/{ADDRESS}/connect", headers=headers)
            assert connected.status_code == 200
            final = client.get("/api/native/devices", headers=headers)
            assert final.status_code == 200
            assert final.json()[0]["connected"] is True
            assert manager.scan_started is True
            assert manager.pair_calls == [(ADDRESS, "8642")]
            assert manager.connect_calls == [(ADDRESS, True)]
        finally:
            app.state.bt_manager = original_manager
            app.state.ha_bridge = original_bridge


def test_native_management_remaining_endpoints_and_settings(monkeypatch):
    with TestClient(app) as client:
        original_manager = app.state.bt_manager
        original_settings = app.state.config_store.settings.model_copy(deep=True)
        settings_store = app.state.config_store
        monkeypatch.setattr(settings_store, "save", Mock())
        manager = Mock()
        manager.get_devices = Mock(return_value=[_device(paired=True, trusted=True)])
        manager.get_adapters = Mock(return_value=[AdapterInfo(path="/org/bluez/hci0", interface="hci0", address="00:11:22:33:44:55")])
        manager.get_adapter_by_name.return_value = Mock(set_power=AsyncMock())
        manager.disconnect_device = AsyncMock(return_value=True)
        manager.remove_device = AsyncMock(return_value=True)
        manager.connect_device = AsyncMock(return_value=True)
        manager.pair_and_trust = AsyncMock(return_value=True)
        app.state.bt_manager = manager
        headers = TOKEN_HEADER()
        try:
            devices = client.get("/api/native/devices", headers=headers)
            assert devices.status_code == 200 and devices.json()[0]["address"] == ADDRESS
            adapters = client.get("/api/native/adapters", headers=headers)
            assert adapters.status_code == 200 and adapters.json()[0]["interface"] == "hci0"
            power = client.post("/api/native/adapters/hci0/power", json={"powered": False}, headers=headers)
            assert power.status_code == 200
            manager.get_adapter_by_name.return_value.set_power.assert_awaited_once_with(False)
            assert client.post(f"/api/native/devices/{ADDRESS}/disconnect", headers=headers).status_code == 200
            assert client.delete(f"/api/native/devices/{ADDRESS}", headers=headers).status_code == 200

            settings_response = client.get("/api/native/settings", headers=headers)
            assert settings_response.status_code == 200
            settings = settings_response.json()
            assert "native_token" not in settings and "native_token" not in settings_response.text
            updated = client.put(
                "/api/native/settings/system",
                json={"log_level": "debug", "default_codec": "ldac", "auto_reconnect_enabled": False},
                headers=headers,
            )
            assert updated.status_code == 200
            assert updated.json() == {"log_level": "debug", "default_codec": "ldac", "auto_reconnect_enabled": False}
            assert settings_store.settings.default_codec == "ldac"
            assert app.state.reconnect_engine.globally_enabled is False
            assert settings_store.save.call_count == 2
            invalid_system = client.put("/api/native/settings/system", json={"default_codec": "bad"}, headers=headers)
            assert invalid_system.status_code == 422
            assert "native_token" not in invalid_system.text
            speaker = client.put(
                f"/api/native/settings/speakers/{ADDRESS}",
                json={"custom_alias": "Office", "auto_reconnect": True, "default_volume": 68},
                headers=headers,
            )
            assert speaker.status_code == 200
            assert speaker.json()["custom_alias"] == "Office"
            assert client.put(
                f"/api/native/settings/speakers/{ADDRESS}", json={"custom_alias": "bad\u0001alias"}, headers=headers
            ).status_code == 422
        finally:
            settings_store.settings = original_settings
            app.state.bt_manager = original_manager


@pytest.mark.parametrize(
    ("method", "path", "json_body", "manager_method", "reason"),
    [
        ("post", "/api/native/scan/start", {}, "start_scan", "Bluetooth scan failed"),
        ("post", "/api/native/scan/stop", {}, "stop_scan", "Bluetooth scan failed"),
        ("post", "/api/native/devices/pair", {"address": ADDRESS, "pin": "8642"}, "pair_and_trust", "Pairing failed"),
        ("post", f"/api/native/devices/{ADDRESS}/connect", None, "connect_device", "Connection failed"),
        ("post", f"/api/native/devices/{ADDRESS}/disconnect", None, "disconnect_device", "Disconnection failed"),
        ("delete", f"/api/native/devices/{ADDRESS}", None, "remove_device", "Device removal failed"),
    ],
)
def test_native_management_errors_do_not_expose_raw_details(monkeypatch, method, path, json_body, manager_method, reason):
    with TestClient(app) as client:
        original_manager = app.state.bt_manager
        manager = Mock()
        for name in ("start_scan", "stop_scan", "pair_and_trust", "connect_device", "disconnect_device", "remove_device"):
            setattr(manager, name, AsyncMock(side_effect=RuntimeError("/org/bluez/hci0/dev_secret: raw DBus detail")))
        app.state.bt_manager = manager
        try:
            response = getattr(client, method)(path, json=json_body, headers=TOKEN_HEADER()) if json_body is not None else getattr(client, method)(path, headers=TOKEN_HEADER())
            assert response.status_code == 400
            assert response.json()["detail"] == reason
            assert "/org/bluez" not in response.text and "raw DBus" not in response.text
        finally:
            app.state.bt_manager = original_manager


def test_native_http_routes_all_require_authentication():
    route_methods = {
        "/api/native/identity": ("get", None),
        "/api/native/speakers": ("get", None),
        "/api/native/speakers/{address}/command": ("post", {"operation": "stop", "version": 1}),
        "/api/native/scan/start": ("post", {}),
        "/api/native/scan/stop": ("post", {}),
        "/api/native/devices": ("get", None),
        "/api/native/devices/pair": ("post", {"address": ADDRESS}),
        "/api/native/devices/{address}/connect": ("post", None),
        "/api/native/devices/{address}/disconnect": ("post", None),
        "/api/native/devices/{address}": ("delete", None),
        "/api/native/adapters": ("get", None),
        "/api/native/adapters/{adapter_name}/power": ("post", {"powered": True}),
        "/api/native/settings": ("get", None),
        "/api/native/settings/system": ("put", {"default_codec": "auto"}),
        "/api/native/settings/speakers/{address}": ("put", {"custom_alias": "test"}),
    }
    mounted = {route.path for route in router.routes if getattr(route, "methods", None) and route.path.startswith("/api/native/")}
    assert mounted <= route_methods.keys(), f"Unmapped native route(s): {mounted - route_methods.keys()}"
    with TestClient(app) as client:
        for path, (method, body) in route_methods.items():
            actual_path = path.replace("{address}", ADDRESS).replace("{adapter_name}", "hci0")
            if body is None:
                response = getattr(client, method)(actual_path)
            else:
                response = getattr(client, method)(actual_path, json=body)
            assert response.status_code == 401, (path, response.text)

