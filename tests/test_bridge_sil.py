"""Service-in-process tests for the bridge REST and WebSocket seams."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.bl_haos.api.ws import native_ws_manager, ws_manager
from backend.bl_haos.bluetooth.models import DeviceInfo
from backend.bl_haos.main import app


@pytest.fixture(autouse=True)
def isolate_websocket_managers():
    """Keep module-global connection managers independent across tests."""
    ws_manager.active_connections.clear()
    native_ws_manager.active_connections.clear()
    yield
    ws_manager.active_connections.clear()
    native_ws_manager.active_connections.clear()


def test_websocket_device_discovery():
    """A BlueZ-shaped device event is serialized onto the live WebSocket."""
    device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    address = "AA:BB:CC:DD:EE:FF"

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            client.portal.call(
                app.state.bt_manager._on_interfaces_added,
                device_path,
                {
                    "org.bluez.Device1": {
                        "Address": address,
                        "Name": "Living Room Speaker",
                        "Alias": "Living Room Speaker",
                        "Adapter": "/org/bluez/hci0",
                        "UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb"],
                        "Class": 0x240414,
                        "Paired": True,
                        "Trusted": True,
                        "Connected": False,
                        "RSSI": -38,
                    }
                },
            )

            event = websocket.receive_json()

    assert event["event"] == "device_discovered"
    assert event["data"] == {
        "path": device_path,
        "adapter_path": "/org/bluez/hci0",
        "adapter_name": "hci0",
        "address": address,
        "name": "Living Room Speaker",
        "alias": "Living Room Speaker",
        "icon": None,
        "paired": True,
        "trusted": True,
        "connected": False,
        "blocked": False,
        "legacy_pairing": False,
        "rssi": -38,
        "tx_power": None,
        "class_of_device": 0x240414,
        "uuids": ["0000110b-0000-1000-8000-00805f9b34fb"],
        "is_audio_sink": True,
        "device_type": "Loudspeaker",
        "battery_percentage": None,
        "last_seen": event["data"]["last_seen"],
    }


def test_rest_contracts_with_injected_bluetooth_state():
    """REST state and scan routes use the same injected manager state."""
    adapter_path = "/org/bluez/hci0"
    device_path = f"{adapter_path}/dev_11_22_33_44_55_66"

    with TestClient(app) as client:
        client.portal.call(
            app.state.bt_manager._on_interfaces_added,
            adapter_path,
            {
                "org.bluez.Adapter1": {
                    "Address": "00:11:22:33:44:55",
                    "Name": "Test Controller",
                    "Powered": True,
                    "Discovering": False,
                    "Pairable": True,
                }
            },
        )
        client.portal.call(
            app.state.bt_manager._on_interfaces_added,
            device_path,
            {
                "org.bluez.Device1": {
                    "Address": "11:22:33:44:55:66",
                    "Name": "Test Soundbar",
                    "Adapter": adapter_path,
                    "UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb"],
                    "Paired": True,
                    "Trusted": True,
                    "Connected": False,
                }
            },
        )

        health = client.get("/api/health")
        adapters = client.get("/api/adapters")
        devices = client.get("/api/devices")
        scan_start = client.post("/api/scan/start", json={"adapter_name": "hci0"})
        scan_stop = client.post("/api/scan/stop", json={"adapter_name": "hci0"})

    assert health.status_code == 200
    assert health.json()["status"] in {"healthy", "degraded", "unavailable"}
    assert health.json()["health"]["version"] == 1
    assert health.json()["adapters_count"] == 1
    assert health.json()["devices_count"] == 1
    assert adapters.status_code == 200
    assert adapters.json()[0]["interface"] == "hci0"
    assert devices.status_code == 200
    assert devices.json()[0]["address"] == "11:22:33:44:55:66"
    assert scan_start.json() == {"status": "ok", "scanning": True, "adapter": "hci0"}
    assert scan_stop.json() == {"status": "ok", "scanning": False, "adapter": "hci0"}


def test_websocket_device_update_and_disconnect_cleanup():
    """A property change reaches a connected client and cleanup removes it."""
    device_path = "/org/bluez/hci0/dev_12_34_56_78_9A_BC"
    device_properties = {
        "Address": "12:34:56:78:9A:BC",
        "Name": "Office Speaker",
        "Adapter": "/org/bluez/hci0",
        "UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb"],
        "Paired": True,
        "Trusted": True,
        "Connected": False,
        "RSSI": -70,
    }

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            client.portal.call(
                app.state.bt_manager._on_interfaces_added,
                device_path,
                {"org.bluez.Device1": device_properties},
            )
            assert websocket.receive_json()["event"] == "device_discovered"

            client.portal.call(
                app.state.bt_manager._on_properties_changed,
                device_path,
                "org.bluez.Device1",
                {"Connected": True, "RSSI": -41},
            )
            event = websocket.receive_json()

        assert not ws_manager.active_connections

    assert event["event"] == "device_updated"
    assert event["data"]["address"] == "12:34:56:78:9A:BC"
    assert event["data"]["connected"] is True
    assert event["data"]["rssi"] == -41


def test_native_rest_requires_authentication_and_blocks_unauthorized_commands():
    """Missing and incorrect native credentials never reach the bridge."""
    execute = AsyncMock()
    payload = {"operation": "play"}

    with TestClient(app) as client:
        client.portal.call(
            app.state.bt_manager._on_interfaces_added,
            "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_01",
            {
                "org.bluez.Device1": {
                    "Address": "AA:BB:CC:DD:EE:01",
                    "Name": "Native Speaker",
                    "Adapter": "/org/bluez/hci0",
                    "UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb"],
                    "Trusted": True,
                    "Paired": True,
                    "Connected": True,
                }
            },
        )
        app.state.ha_bridge.execute = execute
        token = app.state.config_store.settings.native_token

        for headers in ({}, {"Authorization": "Bearer incorrect"}, {"Authorization": "invalid"}):
            assert client.get("/api/native/identity", headers=headers).status_code == 401
            assert client.get("/api/native/speakers", headers=headers).status_code == 401
            assert client.post(
                "/api/native/speakers/aa:bb:cc:dd:ee:01/command",
                headers=headers,
                json=payload,
            ).status_code == 401

        assert not execute.await_args_list

        valid = client.post(
            "/api/native/speakers/aa:bb:cc:dd:ee:01/command",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )

    assert valid.status_code == 200
    assert valid.json()["address"] == "aa:bb:cc:dd:ee:01"
    execute.assert_awaited_once_with("aa:bb:cc:dd:ee:01", "play", volume=None, url=None)


def _register_native_speaker(client, address_no_colons: str):
    client.portal.call(
        app.state.bt_manager._on_interfaces_added,
        f"/org/bluez/hci0/dev_{address_no_colons}",
        {
            "org.bluez.Device1": {
                "Address": address_no_colons.replace("_", ":"),
                "Name": "Native Speaker",
                "Adapter": "/org/bluez/hci0",
                "UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb"],
                "Trusted": True,
                "Paired": True,
                "Connected": True,
            }
        },
    )


def test_auto_reconnect_reports_bluez_failure_distinctly(monkeypatch):
    """When BlueZ itself can't reconnect, the error says so instead of a generic message."""
    from backend.bl_haos.ha.player import MediaPlayerError

    execute = AsyncMock(side_effect=MediaPlayerError("Connected PipeWire A2DP sink is unavailable"))
    connect = AsyncMock(return_value=False)

    with TestClient(app) as client:
        _register_native_speaker(client, "AA_BB_CC_DD_EE_02")
        app.state.ha_bridge.execute = execute
        monkeypatch.setattr(app.state.bt_manager, "connect_device", connect)
        token = app.state.config_store.settings.native_token

        response = client.post(
            "/api/native/speakers/aa:bb:cc:dd:ee:02/command",
            headers={"Authorization": f"Bearer {token}"},
            json={"operation": "play"},
        )

    assert response.status_code == 409
    assert "could not re-establish the Bluetooth connection" in response.json()["detail"]
    connect.assert_awaited_once_with("aa:bb:cc:dd:ee:02")
    execute.assert_awaited_once()


def test_auto_reconnect_reports_sink_still_missing_distinctly(monkeypatch):
    """When BlueZ reconnects but no sink appears, the error says so instead of a generic message."""
    from backend.bl_haos.api.routes import A2DP_SINK_RETRY_ATTEMPTS
    from backend.bl_haos.ha.player import MediaPlayerError

    execute = AsyncMock(side_effect=MediaPlayerError("Connected PipeWire A2DP sink is unavailable"))
    connect = AsyncMock(return_value=True)
    sleep = AsyncMock()

    with TestClient(app) as client:
        _register_native_speaker(client, "AA_BB_CC_DD_EE_03")
        app.state.ha_bridge.execute = execute
        monkeypatch.setattr(app.state.bt_manager, "connect_device", connect)
        monkeypatch.setattr("backend.bl_haos.api.routes.asyncio.sleep", sleep)
        token = app.state.config_store.settings.native_token

        response = client.post(
            "/api/native/speakers/aa:bb:cc:dd:ee:03/command",
            headers={"Authorization": f"Bearer {token}"},
            json={"operation": "play"},
        )

    assert response.status_code == 409
    assert "no audio sink appeared" in response.json()["detail"]
    connect.assert_awaited_once_with("aa:bb:cc:dd:ee:03")
    assert execute.await_count == A2DP_SINK_RETRY_ATTEMPTS + 1
    assert sleep.await_count == A2DP_SINK_RETRY_ATTEMPTS


def test_auto_reconnect_waits_until_sink_recovers(monkeypatch):
    """A reconnect polls until PipeWire publishes the delayed A2DP sink."""
    from backend.bl_haos.ha.player import MediaPlayerError

    execute = AsyncMock(side_effect=[
        MediaPlayerError("Connected PipeWire A2DP sink is unavailable"),
        MediaPlayerError("Connected PipeWire A2DP sink is unavailable"),
        None,
    ])
    connect = AsyncMock(return_value=True)
    sleep = AsyncMock()

    with TestClient(app) as client:
        _register_native_speaker(client, "AA_BB_CC_DD_EE_04")
        app.state.ha_bridge.execute = execute
        monkeypatch.setattr(app.state.bt_manager, "connect_device", connect)
        monkeypatch.setattr("backend.bl_haos.api.routes.asyncio.sleep", sleep)
        token = app.state.config_store.settings.native_token

        response = client.post(
            "/api/native/speakers/aa:bb:cc:dd:ee:04/command",
            headers={"Authorization": f"Bearer {token}"},
            json={"operation": "play"},
        )

    assert response.status_code == 200
    assert response.json()["address"] == "aa:bb:cc:dd:ee:04"
    assert execute.await_count == 3
    assert sleep.await_count == 2


def test_native_snapshot_filters_and_normalizes_audio_sinks(monkeypatch):
    """Native snapshots expose trusted audio sinks with normalized addresses."""
    speakers = [
        DeviceInfo(
            path="/speaker",
            adapter_path="/org/bluez/hci0",
            address="AA-BB-CC-DD-EE-02",
            name="Trusted Speaker",
            trusted=True,
            is_audio_sink=True,
            connected=True,
        ),
        DeviceInfo(
            path="/untrusted",
            adapter_path="/org/bluez/hci0",
            address="AA:BB:CC:DD:EE:03",
            name="Untrusted Speaker",
            is_audio_sink=True,
        ),
    ]

    with TestClient(app) as client:
        monkeypatch.setattr(app.state.bt_manager, "get_devices", lambda audio_only=True: speakers)
        token = app.state.config_store.settings.native_token
        response = client.get(
            "/api/native/speakers",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    snapshot = response.json()["speakers"]
    assert list(snapshot) == ["aa:bb:cc:dd:ee:02"]
    assert snapshot["aa:bb:cc:dd:ee:02"]["is_audio_sink"] is True


def test_native_websocket_authentication():
    """Native WebSocket access requires the configured bearer credential."""
    with TestClient(app) as client:
        for headers in ({}, {"Authorization": "Bearer incorrect"}):
            with pytest.raises(WebSocketDisconnect) as error:
                with client.websocket_connect("/ws/native", headers=headers):
                    pass
            assert error.value.code == 1008

        token = app.state.config_store.settings.native_token
        with client.websocket_connect(
            "/ws/native", headers={"Authorization": f"Bearer {token}"}
        ) as websocket:
            assert len(native_ws_manager.active_connections) == 1
            websocket.close()

        assert not native_ws_manager.active_connections